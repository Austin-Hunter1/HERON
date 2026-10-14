"""
RINEX 3 broadcast navigation files, for BeiDou.

This is the truth source for the BeiDou orbits (D-054).  A RINEX 3 file is a
multi-GNSS merge that the IGS publishes each day; the BeiDou records in it carry
the legacy D1/D2 message (B1I and B3I), not B-CNAV2, so a RINEX record and an
ephemeris decoded from B2a are two different parameter sets for the same orbit.
They describe the same satellite path, which is what makes the RINEX record a fair
check on the B-CNAV2 decode and on the orbit model.

Only BeiDou records are read.  The record layout is that of RINEX 3.04, Table A8
(one epoch line and seven lines of four values, D19.12 fields):

    line 0   sat id, clock epoch (BDT), a0, a1, a2
    line 1   AODE, Crs, delta n, M0
    line 2   Cuc, e, Cus, sqrt(A)
    line 3   toe, Cic, Omega0, Cis
    line 4   i0, Crc, omega, Omega dot
    line 5   i dot, (spare), BDT week, (spare)
    line 6   SV accuracy, SatH1, TGD1 (B1/B3), TGD2 (B2/B3)
    line 7   transmission time, AODC

GEO satellites are skipped.  They use a different orbit algorithm (a fixed rotation
about the x axis is added), and they do not carry B2a.  A record is called GEO here
when its inclination is below `GEO_INCLINATION_LIMIT_RAD`; the broadcast records do
not state the orbit type.

The group delays in the file are the B1/B2 ones, not B2a's, so `tgd_b2ap` stays zero
for these records.  That makes the *position* comparison valid and the clock one
not.  The download path is a template that was **not** checked against the live
archive (the cloud session that wrote it cannot reach CDDIS): confirm it with
`availability` before relying on it.
"""

from __future__ import annotations

import gzip
from datetime import datetime
from pathlib import Path


from . import cddis, environment_variables
from . import time_systems
from .nav.ephemeris import BDS_A_REF_M, BDS_PI, BDS_SAT_TYPE_IGSO, BDS_SAT_TYPE_MEO, BdsEphemeris

# IGSO and MEO planes are inclined about 55 degrees; GEO planes are near zero.  Any
# value between them separates the two; 0.2 rad (11.5 deg) leaves room both ways.
GEO_INCLINATION_LIMIT_RAD = 0.2
# Above this semi-major axis a satellite is IGSO (or GEO), below it MEO.  MEO is
# 27 906 km and IGSO 42 162 km.
IGSO_MIN_SEMI_MAJOR_AXIS_M = 35_000_000.0

# A BeiDou ephemeris is broadcast for a one hour fit interval around toe.  Past this
# many seconds from toe it is being extrapolated.
MAX_AGE_S = 7200.0

_RECORD_LINES = 8
_FIELD_WIDTH = 19
_FIRST_VALUE_COLUMN = 4  # the continuation lines start with four spaces

# CDDIS directory and file name of the IGS combined multi-GNSS broadcast file.  NOT
# VERIFIED against the live archive; see the module docstring.
_BRDC3_DIRECTORY = "/gnss/data/daily/{yyyy}/brdc/"
_BRDC3_FILENAME = "BRDC00IGS_R_{yyyy}{ddd}0000_01D_MN.rnx.gz"


def _parse_float(text: str) -> float:
    """A D19.12 field: Fortran 'D' exponents, and blank means zero."""
    text = text.strip()
    if not text:
        return 0.0
    return float(text.replace("D", "E").replace("d", "e"))


def _split_header(lines: list[str]) -> tuple[list[str], list[str]]:
    for index, line in enumerate(lines):
        if "END OF HEADER" in line:
            return lines[: index + 1], lines[index + 1 :]
    raise ValueError("no END OF HEADER line: this is not a RINEX file")


def _check_version(header: list[str]) -> None:
    first = header[0] if header else ""
    if "RINEX VERSION / TYPE" not in first:
        raise ValueError("the first line is not a RINEX VERSION / TYPE line")
    version = float(first[:9])
    if not 3.0 <= version < 4.0:
        raise ValueError(
            f"RINEX {version:.2f} is not supported; BeiDou records here follow RINEX 3. "
            "RINEX 4 has a different record layout."
        )


def _values(lines: list[str]) -> list[float]:
    """The 3 + 7 x 4 numeric fields of one record, in order."""
    values = [_parse_float(lines[0][23 + _FIELD_WIDTH * k : 23 + _FIELD_WIDTH * (k + 1)]) for k in range(3)]
    for line in lines[1:]:
        for k in range(4):
            start = _FIRST_VALUE_COLUMN + _FIELD_WIDTH * k
            values.append(_parse_float(line[start : start + _FIELD_WIDTH]))
    return values


def parse_bds_records(lines: list[str]) -> tuple[dict[str, list[BdsEphemeris]], list[str]]:
    """
    Every BeiDou ephemeris in a RINEX 3 navigation file, keyed by satellite id.

    Returns `(ephemerides, skipped)`.  `skipped` names the GEO records that were left
    out, once per record, so a caller can see that something was dropped.
    """
    header, body = _split_header(lines)
    _check_version(header)

    ephemerides: dict[str, list[BdsEphemeris]] = {}
    skipped: list[str] = []
    index = 0
    while index < len(body):
        line = body[index]
        # A new record starts with a system letter in column 1.  Records of other
        # systems have a different length, so their continuation lines (which start
        # with spaces) are walked past one at a time.
        if not line.strip() or line[0] == " ":
            index += 1
            continue
        if line[0] != "C":
            index += 1
            continue
        record = body[index : index + _RECORD_LINES]
        if len(record) < _RECORD_LINES:
            break  # a truncated record at the end of the file
        index += _RECORD_LINES
        ephemeris = _parse_record(record)
        sat_id = record[0][:3].replace(" ", "0")
        if ephemeris is None:
            skipped.append(sat_id)
        else:
            ephemerides.setdefault(sat_id, []).append(ephemeris)
    return ephemerides, skipped


def _parse_record(lines: list[str]) -> BdsEphemeris | None:
    sat_id = lines[0][:3].replace(" ", "0")
    year = int(lines[0][4:8])
    month, day, hour, minute, second = (int(lines[0][8 + 3 * k : 11 + 3 * k]) for k in range(5))
    clock_epoch = datetime(year, month, day, hour, minute, second)
    v = _values(lines)
    a0, a1, a2 = v[0:3]
    (aode, crs, delta_n, m0,
     cuc, e, cus, sqrt_a,
     toe, cic, omega0, cis,
     i0, crc, omega, omega_dot,
     i_dot, _spare1, bdt_week, _spare2,
     _accuracy, health, _tgd1, _tgd2,
     _transmission_time, aodc) = v[3:29]

    semi_major_axis = sqrt_a * sqrt_a
    if semi_major_axis > IGSO_MIN_SEMI_MAJOR_AXIS_M and abs(i0) < GEO_INCLINATION_LIMIT_RAD:
        return None  # GEO
    sat_type = BDS_SAT_TYPE_IGSO if semi_major_axis > IGSO_MIN_SEMI_MAJOR_AXIS_M else BDS_SAT_TYPE_MEO

    _, toc = time_systems.bdt_week_and_tow(clock_epoch)
    return BdsEphemeris(
        sat_id=sat_id,
        week=int(bdt_week),
        toe=toe,
        sat_type=sat_type,
        delta_a=semi_major_axis - BDS_A_REF_M[sat_type],
        a_dot=0.0,
        delta_n0=delta_n / BDS_PI,
        delta_n0_dot=0.0,
        M0=m0 / BDS_PI,
        e=e,
        omega=omega / BDS_PI,
        Omega0=omega0 / BDS_PI,
        Omega_dot=omega_dot / BDS_PI,
        i0=i0 / BDS_PI,
        i_dot=i_dot / BDS_PI,
        Cis=cis, Cic=cic, Crs=crs, Crc=crc, Cus=cus, Cuc=cuc,
        toc=toc,
        a0=a0, a1=a1, a2=a2,
        iode=int(aode),
        iodc=int(aodc),
        health=int(health),
    )


def load_bds_ephemerides(path: str | Path) -> tuple[dict[str, list[BdsEphemeris]], list[str]]:
    """`parse_bds_records` for a file, plain or gzip-compressed (`.gz`)."""
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as handle:
        return parse_bds_records(handle.readlines())


def select_ephemeris(
    candidates: list[BdsEphemeris],
    week: int,
    tow_s: float,
    *,
    max_age_s: float = MAX_AGE_S,
) -> BdsEphemeris | None:
    """
    The record whose `toe` is nearest to a BDT time, or None if none is close enough.

    The week numbers are part of the difference, so a record from the end of one week
    is found for a time at the start of the next.
    """
    best, best_age = None, max_age_s
    for ephemeris in candidates:
        age = abs((week - ephemeris.week) * time_systems.SECONDS_PER_WEEK + tow_s - ephemeris.toe)
        if age <= best_age:
            best, best_age = ephemeris, age
    return best


# ---------------------------------------------------------------------------
# Download (CDDIS, behind Earthdata login)
# ---------------------------------------------------------------------------


def brdc3_url_and_path(day: datetime, resources_dir: str | Path) -> tuple[str, Path, Path]:
    """`(url, compressed_path, decompressed_path)` for one day's multi-GNSS file."""
    fields = dict(yyyy=f"{day:%Y}", ddd=f"{day.timetuple().tm_yday:03d}")
    relative = (_BRDC3_DIRECTORY + _BRDC3_FILENAME).format(**fields)
    compressed = Path(resources_dir) / relative.lstrip("/")
    return cddis.CDDIS_ARCHIVE_URL + relative, compressed, compressed.with_suffix("")


def availability(day: datetime, resources_dir: str | Path | None = None) -> cddis.Availability:
    """Whether the day's multi-GNSS broadcast file is published, without downloading it."""
    if resources_dir is None:
        resources_dir = environment_variables.get_resources_path()
    url, compressed, decompressed = brdc3_url_and_path(day, resources_dir)
    label = f"BRDC (RINEX 3) {day:%Y-%m-%d}"
    if decompressed.exists() and decompressed.stat().st_size > 0:
        size = compressed.stat().st_size if compressed.exists() else None
        return cddis.Availability(label, url, True, "cached locally", size)
    return cddis.Availability(label, url, *cddis.exists(url))


def download_brdc3(
    day: datetime, resources_dir: str | Path | None = None, *, overwrite: bool = False
) -> Path:
    """Fetch and decompress one day's multi-GNSS broadcast file; return the local path."""
    from gnss_tools.misc.data_utils import decompress

    if resources_dir is None:
        resources_dir = environment_variables.get_resources_path()
    url, compressed, decompressed = brdc3_url_and_path(day, resources_dir)
    if decompressed.exists() and not overwrite:
        return decompressed
    poisoned = compressed.exists() and compressed.read_bytes()[:2] != b"\x1f\x8b"
    cddis.download(url, compressed, overwrite=overwrite or poisoned)
    decompress(str(compressed), str(decompressed))
    return decompressed


def load_day(
    day: datetime, resources_dir: str | Path | None = None
) -> tuple[dict[str, list[BdsEphemeris]], list[str]]:
    """The BeiDou ephemerides of one day, downloading the file when it is not cached."""
    return load_bds_ephemerides(download_brdc3(day, resources_dir))
