"""L5-band GNSS for the planner: GPS L5, Galileo E5a, BeiDou-3 B2a, QZSS L5.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from engine.almanac import (
    GPS_L5_PRNS,
    MU,
    OMEGA_E_DOT,
    AlmanacSat,
    _kepler,
    age_days as gps_age_days,
    gps_time,
    parse_yuma,
    sat_ecef as gps_sat_ecef,
)

# Galileo ICD
GAL_MU = 3.986004418e14
GAL_A_REF = 29_600_000.0
GAL_I_REF = math.radians(56.0)
GST_WEEK0_GPS = 1024

CONST_GPS = "G"
CONST_GAL = "E"
CONST_BDS = "C"
CONST_QZSS = "J"

DEFAULT_CONSTELLATIONS = (CONST_GPS, CONST_GAL, CONST_BDS)


@dataclass
class NavSat:
    sid: str
    constellation: str
    prn: int
    health: int
    kind: str
    gps: AlmanacSat | None = None
    gal_week: int = 0
    gal_toa: float = 0.0
    gal_e: float = 0.0
    gal_sqrt_a: float = 0.0
    gal_i0: float = 0.0
    gal_omega_dot: float = 0.0
    gal_omega0: float = 0.0
    gal_omega: float = 0.0
    gal_m0: float = 0.0
    satrec: object | None = None
    epoch: datetime | None = None
    source: str = ""

    def ecef(self, dt: datetime) -> tuple[float, float, float]:
        if self.kind == "gps":
            week, tow = gps_time(dt)
            return gps_sat_ecef(self.gps, week, tow)
        if self.kind == "galileo":
            return _galileo_ecef(self, dt)
        if self.kind == "tle":
            return _tle_ecef(self, dt)
        raise ValueError(self.kind)


def _sid(const: str, prn: int) -> str:
    return f"{const}{prn:02d}"


def _galileo_ecef(sat: NavSat, dt: datetime) -> tuple[float, float, float]:
    week, tow = gps_time(dt)
    gst_week = week - GST_WEEK0_GPS
    tk = (gst_week - sat.gal_week) * 604800.0 + (tow - sat.gal_toa)
    a = sat.gal_sqrt_a * sat.gal_sqrt_a
    n0 = math.sqrt(GAL_MU / (a * a * a))
    mk = sat.gal_m0 + n0 * tk
    ek = _kepler(mk, sat.gal_e)
    sin_e, cos_e = math.sin(ek), math.cos(ek)
    vk = math.atan2(math.sqrt(1.0 - sat.gal_e * sat.gal_e) * sin_e, cos_e - sat.gal_e)
    uk = vk + sat.gal_omega
    rk = a * (1.0 - sat.gal_e * cos_e)
    omega_k = sat.gal_omega0 + (sat.gal_omega_dot - OMEGA_E_DOT) * tk - OMEGA_E_DOT * sat.gal_toa
    xk = rk * math.cos(uk)
    yk = rk * math.sin(uk)
    cos_om, sin_om = math.cos(omega_k), math.sin(omega_k)
    cos_i, sin_i = math.cos(sat.gal_i0), math.sin(sat.gal_i0)
    x = xk * cos_om - yk * cos_i * sin_om
    y = xk * sin_om + yk * cos_i * cos_om
    z = yk * sin_i
    return x, y, z


def _gmst_rad(dt: datetime) -> float:
    jd = dt.timestamp() / 86400.0 + 2440587.5
    t = (jd - 2451545.0) / 36525.0
    gmst = 67310.54841 + (876600.0 * 3600 + 8640184.812866) * t + 0.093104 * t * t - 6.2e-6 * t**3
    return math.radians((gmst % 86400.0) / 240.0)


def _tle_ecef(sat: NavSat, dt: datetime) -> tuple[float, float, float]:
    from sgp4.api import jday

    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second + dt.microsecond * 1e-6)
    err, r, _v = sat.satrec.sgp4(jd, fr)
    if err:
        return (float("nan"), float("nan"), float("nan"))
    th = _gmst_rad(dt)
    c, s = math.cos(th), math.sin(th)
    x, y, z = r
    return (x * c + y * s) * 1000.0, (-x * s + y * c) * 1000.0, z * 1000.0


def parse_galileo_xml(path: Path) -> list[NavSat]:
    root = ET.parse(path).getroot()
    issue = None
    for el in root.iter():
        if el.tag.endswith("issueDate") and el.text:
            issue = datetime.fromisoformat(el.text.replace("Z", "+00:00"))
            break
    if issue is None:
        issue = datetime.now(timezone.utc)
    gps_week, _ = gps_time(issue)
    gst_week_issue = gps_week - GST_WEEK0_GPS

    out: list[NavSat] = []
    for sv in root.iter():
        if not sv.tag.endswith("svAlmanac"):
            continue
        fields: dict[str, str] = {}
        e5a = 0
        svid = None
        for child in sv.iter():
            tag = child.tag.split("}")[-1]
            if tag == "SVID" and child.text:
                svid = int(child.text)
            elif tag == "statusE5a" and child.text:
                e5a = int(float(child.text))
            elif tag in (
                "aSqRoot",
                "ecc",
                "deltai",
                "omega0",
                "omegaDot",
                "w",
                "m0",
                "t0a",
                "wna",
            ) and child.text:
                fields[tag] = child.text
        if svid is None or "aSqRoot" not in fields:
            continue
        wna = int(float(fields["wna"]))
        alm_week = gst_week_issue - (gst_week_issue % 4) + wna
        if alm_week - gst_week_issue > 2:
            alm_week -= 4
        if gst_week_issue - alm_week > 2:
            alm_week += 4
        pi = math.pi
        out.append(
            NavSat(
                sid=_sid(CONST_GAL, svid),
                constellation=CONST_GAL,
                prn=svid,
                health=e5a,
                kind="galileo",
                gal_week=alm_week,
                gal_toa=float(fields["t0a"]),
                gal_e=float(fields["ecc"]),
                gal_sqrt_a=math.sqrt(GAL_A_REF) + float(fields["aSqRoot"]),
                gal_i0=GAL_I_REF + float(fields["deltai"]) * pi,
                gal_omega_dot=float(fields["omegaDot"]) * pi,
                gal_omega0=float(fields["omega0"]) * pi,
                gal_omega=float(fields["w"]) * pi,
                gal_m0=float(fields["m0"]) * pi,
                epoch=issue,
                source=path.name,
            )
        )
    return out


def _parse_tle_file(path: Path) -> list[tuple[str, object]]:
    from sgp4.api import Satrec

    lines = path.read_text(errors="replace").splitlines()
    recs = []
    i = 0
    while i < len(lines) - 1:
        name = lines[i].strip()
        l1 = lines[i + 1].strip() if i + 1 < len(lines) else ""
        l2 = lines[i + 2].strip() if i + 2 < len(lines) else ""
        if l1.startswith("1 ") and l2.startswith("2 "):
            recs.append((name, Satrec.twoline2rv(l1, l2)))
            i += 3
        else:
            i += 1
    return recs


def _prn_from_name(name: str, const: str) -> int | None:
    if const == CONST_BDS:
        m = re.search(r"\(C(\d+)\)", name, re.I)
        return int(m.group(1)) if m else None
    if const == CONST_QZSS:
        m = re.search(r"PRN\s+(\d+)", name, re.I)
        if m:
            n = int(m.group(1))
            return n if n < 200 else n
        m = re.search(r"QZS-(\d+)", name, re.I)
        return int(m.group(1)) if m else None
    return None


def parse_beidou3_tle(path: Path) -> list[NavSat]:
    out = []
    for name, rec in _parse_tle_file(path):
        if "BEIDOU-3" not in name.upper():
            continue
        prn = _prn_from_name(name, CONST_BDS)
        if prn is None or prn < 19:
            continue
        out.append(
            NavSat(
                sid=_sid(CONST_BDS, prn),
                constellation=CONST_BDS,
                prn=prn,
                health=0,
                kind="tle",
                satrec=rec,
                source=path.name,
            )
        )
    return out


BDS_MU = 3.986004418e14
BDS_OMEGA_E = 7.2921150e-5
BDS_MATCH_M = 100_000.0


def _rnx_float(s: str) -> float:
    s = s.strip().replace("D", "E")
    return float(s) if s else 0.0


def parse_brdc_beidou(path: Path) -> dict[int, list[tuple[datetime, list[float]]]]:
    """BeiDou records from a RINEX 3 nav file: PRN -> [(epoch, 31 values)]."""
    import gzip

    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", errors="replace") as fh:
        lines = fh.read().splitlines()
    i = next((k + 1 for k, l in enumerate(lines) if "END OF HEADER" in l), len(lines))
    out: dict[int, list[tuple[datetime, list[float]]]] = {}
    while i < len(lines):
        line = lines[i]
        n = 3 if line[:1] in ("R", "S") else 7
        if line[:1] == "C" and i + n < len(lines):
            try:
                vals = [_rnx_float(line[23 + 19 * k : 42 + 19 * k]) for k in range(3)]
                for k in range(1, n + 1):
                    cont = lines[i + k]
                    vals += [_rnx_float(cont[4 + 19 * j : 23 + 19 * j]) for j in range(4)]
                ep = datetime(
                    int(line[4:8]), int(line[9:11]), int(line[12:14]),
                    int(line[15:17]), int(line[18:20]), int(line[21:23]), tzinfo=timezone.utc,
                )
                out.setdefault(int(line[1:3]), []).append((ep, vals))
            except (ValueError, IndexError):
                pass
        i += n + 1
    return out


def bds_broadcast_ecef(vals: list[float], dt: datetime) -> tuple[float, float, float]:
    """MEO/IGSO position from one BeiDou broadcast ephemeris record."""
    (_, _, _, _, crs, dn, m0, cuc, e, cus, sqrt_a, toe, cic, om0, cis, i0, crc, w, om_dot, idot) = vals[:20]
    week = int(vals[21])
    gps_s = (dt.astimezone(timezone.utc) - datetime(1980, 1, 6, tzinfo=timezone.utc)).total_seconds() + 18.0
    tk = gps_s - 14.0 - (week + 1356) * 604800.0 - toe
    a = sqrt_a * sqrt_a
    mk = m0 + (math.sqrt(BDS_MU / a**3) + dn) * tk
    ek = _kepler(mk, e)
    phi = math.atan2(math.sqrt(1 - e * e) * math.sin(ek), math.cos(ek) - e) + w
    s2, c2 = math.sin(2 * phi), math.cos(2 * phi)
    u = phi + cus * s2 + cuc * c2
    r = a * (1 - e * math.cos(ek)) + crs * s2 + crc * c2
    inc = i0 + idot * tk + cis * s2 + cic * c2
    x, y = r * math.cos(u), r * math.sin(u)
    om = om0 + (om_dot - BDS_OMEGA_E) * tk - BDS_OMEGA_E * toe
    return (
        x * math.cos(om) - y * math.cos(inc) * math.sin(om),
        x * math.sin(om) + y * math.cos(inc) * math.cos(om),
        y * math.sin(inc),
    )


def beidou_from_tle_and_brdc(tle_path: Path, brdc_path: Path) -> list[NavSat]:
    """BeiDou-3 orbits from the TLEs, numbered by the PRN each one actually broadcasts.

    Celestrak's (Cnn) names lag PRN reassignments, so each non-GEO TLE is matched to
    the broadcast ephemeris by position. TLEs with no broadcasting match are dropped.
    """
    brdc = parse_brdc_beidou(brdc_path)
    epochs = sorted(ep for recs in brdc.values() for ep, _ in recs)
    if not epochs:
        return parse_beidou3_tle(tle_path)
    t = epochs[len(epochs) // 2]
    bpos: dict[int, tuple[float, float, float]] = {}
    for prn, recs in brdc.items():
        ep, vals = min(recs, key=lambda r: abs((r[0] - t).total_seconds()))
        if abs((ep - t).total_seconds()) > 4 * 3600 or vals[15] < math.radians(20):
            continue
        bpos[prn] = bds_broadcast_ecef(vals, t)

    out: list[NavSat] = []
    pairs = []
    for name, rec in _parse_tle_file(tle_path):
        geo = math.degrees(rec.inclo) < 20
        if geo:
            prn = _prn_from_name(name, CONST_BDS)
            if "BEIDOU-3" in name.upper() and prn is not None and prn >= 19:
                out.append(NavSat(sid=_sid(CONST_BDS, prn), constellation=CONST_BDS, prn=prn,
                                  health=0, kind="tle", satrec=rec, source=tle_path.name))
            continue
        probe = NavSat(sid="", constellation=CONST_BDS, prn=0, health=0, kind="tle", satrec=rec)
        p = _tle_ecef(probe, t)
        for prn, b in bpos.items():
            d = math.dist(p, b)
            if d < BDS_MATCH_M:
                pairs.append((d, prn, name, rec))
    taken_prn: set[int] = set()
    taken_tle: set[str] = set()
    for _d, prn, name, rec in sorted(pairs, key=lambda x: x[0]):
        if prn in taken_prn or name in taken_tle:
            continue
        taken_prn.add(prn)
        taken_tle.add(name)
        if prn < 19 and "BEIDOU-3" not in name.upper():
            continue
        out.append(NavSat(sid=_sid(CONST_BDS, prn), constellation=CONST_BDS, prn=prn,
                          health=0, kind="tle", satrec=rec, source=tle_path.name))
    return out


def parse_qzss_tle(path: Path) -> list[NavSat]:
    out = []
    for name, rec in _parse_tle_file(path):
        u = name.upper()
        if not (u.startswith("QZS") or "QZSS" in u):
            continue
        prn = _prn_from_name(name, CONST_QZSS) or 0
        sid = f"J{prn}" if prn >= 100 else _sid(CONST_QZSS, prn or 0)
        out.append(
            NavSat(
                sid=sid,
                constellation=CONST_QZSS,
                prn=prn,
                health=0,
                kind="tle",
                satrec=rec,
                source=path.name,
            )
        )
    return out


def gps_from_yuma(path: Path) -> list[NavSat]:
    out = []
    for sat in parse_yuma(path):
        if sat.prn not in GPS_L5_PRNS:
            continue
        out.append(
            NavSat(
                sid=_sid(CONST_GPS, sat.prn),
                constellation=CONST_GPS,
                prn=sat.prn,
                health=sat.health,
                kind="gps",
                gps=sat,
                source=path.name,
            )
        )
    return out


def _latest(folder: Path, pattern: str) -> Path | None:
    hits = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


def load_l5_sats(data_dir: Path) -> list[NavSat]:
    sats: list[NavSat] = []
    yuma = data_dir / "current_yuma.alm"
    if yuma.exists():
        sats.extend(gps_from_yuma(yuma))
    gnss = data_dir / "gnss"
    gal = _latest(gnss, "galileo_*.xml") if gnss.exists() else None
    if gal:
        sats.extend(parse_galileo_xml(gal))
    bds = gnss / "celestrak_beidou.tle" if gnss.exists() else None
    if bds and bds.exists():
        brdc = _latest(gnss, "BRDC*.rnx.gz")
        sats.extend(beidou_from_tle_and_brdc(bds, brdc) if brdc else parse_beidou3_tle(bds))
    qz = gnss / "celestrak_qzss.tle" if gnss.exists() else None
    if qz and qz.exists():
        sats.extend(parse_qzss_tle(qz))
    elif gnss.exists() and (gnss / "celestrak_gnss.tle").exists():
        sats.extend(parse_qzss_tle(gnss / "celestrak_gnss.tle"))
    return sats


def filter_sats(sats: list[NavSat], constellations: list[str] | tuple[str, ...]) -> list[NavSat]:
    want = {c.upper() for c in constellations}
    return [s for s in sats if s.constellation in want]


def counts_by_const(sats: list[NavSat]) -> dict[str, int]:
    out = {CONST_GPS: 0, CONST_GAL: 0, CONST_BDS: 0, CONST_QZSS: 0}
    for s in sats:
        if s.constellation in out:
            out[s.constellation] += 1
    return out


def almanac_age_days(sats: list[NavSat], dt: datetime) -> float:
    gps = [s.gps for s in sats if s.gps is not None]
    if gps:
        return gps_age_days(gps, dt)
    gals = [s for s in sats if s.epoch is not None]
    if gals:
        return abs((dt - gals[0].epoch).total_seconds()) / 86400.0
    return 999.0
