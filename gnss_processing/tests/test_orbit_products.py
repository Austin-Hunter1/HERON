"""
Fetching orbit products: URL construction, cache handling, and record selection.

No network here.  What these tests cover is everything that goes wrong *around* the
download, which in practice is where the failures are:

  * the Earthdata redirect that silently returns a login page instead of a file,
    and the poisoned cache it leaves behind;
  * choosing the wrong ephemeris record out of the couple of dozen a daily file
    holds for each satellite.

The second is the quieter of the two.  A stale-but-plausible ephemeris does not
raise; it puts the satellite kilometres from where it was and the fix follows.
"""

from __future__ import annotations

import gzip
from datetime import datetime

import pytest

from utils import broadcast_ephemeris as be
from utils import cddis, precise_orbits


class FakeRecord:
    """The fields `select_ephemeris` reads, plus an id for assertions."""

    def __init__(self, toe, week_num=2258, sv_health_flag=0, tag="", transmit_time=None):
        self.toe = toe
        self.week_num = week_num
        self.sv_health_flag = sv_health_flag
        self.tag = tag
        # Records are broadcast starting about two hours before their own `toe`;
        # that is measured, not assumed -- see the note in `select_ephemeris`.
        self.transmit_time = toe - 7200.0 if transmit_time is None else transmit_time


# ---------------------------------------------------------------------------
# URL and path construction
# ---------------------------------------------------------------------------


def test_brdc_url_uses_day_of_year_and_two_digit_year():
    url, compressed, decompressed = be.brdc_url_and_path(datetime(2023, 4, 17), "/tmp/res")
    # 2023-04-17 is day 107.
    assert url.endswith("/gnss/data/daily/2023/107/23n/brdc1070.23n.gz")
    assert url.startswith(cddis.CDDIS_ARCHIVE_URL)
    assert compressed.name == "brdc1070.23n.gz"
    assert decompressed.name == "brdc1070.23n"


def test_brdc_compression_changed_at_the_end_of_2020():
    """CDDIS switched from .Z to .gz; a file requested with the wrong suffix 404s."""
    old, _, _ = be.brdc_url_and_path(datetime(2019, 6, 1), "/tmp/res")
    new, _, _ = be.brdc_url_and_path(datetime(2023, 6, 1), "/tmp/res")
    assert old.endswith(".Z")
    assert new.endswith(".gz")


def test_sp3_path_matches_where_gnss_tools_looks():
    """
    The whole trick in `utils.precise_orbits`: put the file exactly where
    `gnss_tools.orbits.sp3_utils` caches, so its own broken download never runs.
    """
    from gnss_tools.misc.data_utils import format_filepath

    day = datetime(2023, 4, 17)
    _, compressed, _ = precise_orbits.sp3_paths(day, "/tmp/res", precise_orbits._PRODUCTS[0])
    theirs = format_filepath(
        "gps/products/{wwww}/COD0MGXFIN_{yyyy}{ddd}0000_01D_05M_ORB.SP3.gz", day
    )
    assert str(compressed) == f"/tmp/res/cddis/{theirs}"


# ---------------------------------------------------------------------------
# Choosing an ephemeris record
# ---------------------------------------------------------------------------


def test_selects_the_nearest_toe_not_the_most_recent():
    """
    A daily file is not in broadcast order, so "the last record before now" can
    easily be a superseded one.  Nearest reference time is the right rule.
    """
    records = [
        FakeRecord(toe=7200, tag="stale"),
        FakeRecord(toe=14400, tag="right"),
        FakeRecord(toe=21600, tag="future"),
    ]
    chosen = be.select_ephemeris(records, tow_s=15000, week=2258)
    assert chosen.tag == "right"


def test_selects_across_a_week_boundary():
    """`toe` near the end of a week and a time just after the rollover must not
    look 604,800 seconds apart."""
    records = [FakeRecord(toe=604000, week_num=2257, tag="previous week")]
    chosen = be.select_ephemeris(records, tow_s=100.0, week=2258)
    assert chosen is not None and chosen.tag == "previous week"


def test_rejects_an_ephemeris_far_outside_its_fit_interval():
    """
    Returning the least-bad record would give a position that looks entirely
    plausible and is kilometres wrong.  None is the honest answer.
    """
    records = [FakeRecord(toe=0)]
    assert be.select_ephemeris(records, tow_s=50_000, week=2258) is None


def test_respects_the_max_age_argument():
    records = [FakeRecord(toe=0)]
    assert be.select_ephemeris(records, tow_s=5000, week=2258, max_age_s=1000) is None
    assert be.select_ephemeris(records, tow_s=5000, week=2258, max_age_s=10000) is not None


def test_skips_unhealthy_satellites():
    """An unhealthy flag is the control segment saying not to use this satellite --
    not a hint to de-weight it."""
    records = [
        FakeRecord(toe=14400, sv_health_flag=63, tag="unhealthy"),
        FakeRecord(toe=7200, sv_health_flag=0, tag="healthy but older"),
    ]
    chosen = be.select_ephemeris(records, tow_s=14400, week=2258)
    assert chosen.tag == "healthy but older"


def test_can_be_asked_to_ignore_health():
    records = [FakeRecord(toe=14400, sv_health_flag=63, tag="unhealthy")]
    assert be.select_ephemeris(records, 14400, 2258) is None
    assert be.select_ephemeris(records, 14400, 2258, require_healthy=False).tag == "unhealthy"


def test_no_records_at_all():
    assert be.select_ephemeris([], 14400, 2258) is None


# ---------------------------------------------------------------------------
# Ionospheric coefficients from a RINEX 2 header
# ---------------------------------------------------------------------------


HEADER = [
    "     2              NAVIGATION DATA                         RINEX VERSION / TYPE\n",
    "    0.3073D-07  0.1490D-07 -0.1788D-06 -0.5960D-07          ION ALPHA           \n",
    "    0.1331D+06  0.3277D+05 -0.2621D+06  0.3277D+06          ION BETA            \n",
    "    18                                                      LEAP SECONDS        \n",
    "                                                            END OF HEADER       \n",
]


def test_parses_klobuchar_coefficients():
    alpha, beta = be.parse_iono_header(HEADER)
    assert alpha == pytest.approx((3.073e-08, 1.490e-08, -1.788e-07, -5.960e-08))
    assert beta == pytest.approx((1.331e05, 3.277e04, -2.621e05, 3.277e05))


def test_fortran_exponents_are_handled():
    """RINEX writes exponents with `D`, which Python's float() rejects outright."""
    assert be._parse_fortran_float(" 0.3073D-07") == pytest.approx(3.073e-8)
    assert be._parse_fortran_float("-0.1788D-06") == pytest.approx(-1.788e-7)


def test_missing_iono_lines_return_none():
    assert be.parse_iono_header([HEADER[0], HEADER[-1]]) is None
    assert be.parse_iono_header([HEADER[0], HEADER[1], HEADER[-1]]) is None  # alpha only


def test_split_header_finds_the_boundary():
    lines = HEADER + ["data line 1\n", "data line 2\n"]
    header, data = be._split_header(lines)
    assert len(header) == len(HEADER)
    assert data == ["data line 1\n", "data line 2\n"]


def test_split_header_with_no_marker_treats_everything_as_data():
    header, data = be._split_header(["a\n", "b\n"])
    assert header == []
    assert len(data) == 2


# ---------------------------------------------------------------------------
# Time conversion
# ---------------------------------------------------------------------------


def test_gps_week_and_tow_round_trip():
    epoch = datetime(2023, 4, 17, 16, 32, 48)
    week, tow = be.gps_week_and_tow(epoch)
    assert week == 2258
    assert be.datetime_from_gps(week, tow) == epoch


def test_gps_epoch_is_week_zero():
    week, tow = be.gps_week_and_tow(datetime(1980, 1, 6))
    assert (week, tow) == (0, 0.0)


def test_time_of_week_stays_inside_a_week():
    for day in range(14):
        _, tow = be.gps_week_and_tow(datetime(2023, 4, 1 + day, 13, 7, 11))
        assert 0.0 <= tow < 604800.0


# ---------------------------------------------------------------------------
# The poisoned cache
# ---------------------------------------------------------------------------


def test_download_returns_a_cached_file_without_network(tmp_path):
    target = tmp_path / "cached.gz"
    target.write_bytes(b"\x1f\x8bcached")
    assert cddis.download("https://example.invalid/nope", target) == target
    assert target.read_bytes() == b"\x1f\x8bcached"


def test_credentials_error_names_the_setting(monkeypatch):
    monkeypatch.setattr(
        "utils.environment_variables.get_earthdata_credentials", lambda: (None, None)
    )
    with pytest.raises(RuntimeError, match="EARTHDATA_USERNAME"):
        cddis.credentials()


def test_sp3_treats_an_html_cache_as_a_miss(tmp_path, monkeypatch):
    """
    The failure this guards against: a failed Earthdata login writes an HTML page
    where the archive should be, and every later run then dies on a gzip error
    that names no cause.  It has to re-fetch, not re-read.
    """
    day = datetime(2023, 4, 17)
    _, compressed, decompressed = precise_orbits.sp3_paths(
        day, tmp_path, precise_orbits._PRODUCTS[0]
    )
    compressed.parent.mkdir(parents=True, exist_ok=True)
    compressed.write_bytes(b"<!DOCTYPE html><html>login</html>")
    decompressed.write_bytes(b"")  # what decompressing it leaves behind

    calls = []

    def fake_download(url, destination, *, overwrite=False):
        calls.append(overwrite)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(destination, "wb") as f:
            f.write(b"# real sp3 content\n")
        return destination

    monkeypatch.setattr(cddis, "download", fake_download)
    result = precise_orbits.download_sp3(day, tmp_path)

    assert calls == [True], "a poisoned cache must force a re-download"
    assert result.read_bytes() == b"# real sp3 content\n"


def test_sp3_uses_a_good_cache_without_downloading(tmp_path, monkeypatch):
    day = datetime(2023, 4, 17)
    _, _, decompressed = precise_orbits.sp3_paths(day, tmp_path, precise_orbits._PRODUCTS[0])
    decompressed.parent.mkdir(parents=True, exist_ok=True)
    decompressed.write_bytes(b"# already here\n")

    def fail(*args, **kwargs):
        raise AssertionError("should not download when the cache is good")

    monkeypatch.setattr(cddis, "download", fail)
    assert precise_orbits.download_sp3(day, tmp_path) == decompressed


def test_sp3_falls_back_to_the_second_product(tmp_path, monkeypatch):
    """CODE and IGS publish on different latencies, so a day may have only one."""
    day = datetime(2023, 4, 17)
    attempted = []

    def fake_download(url, destination, *, overwrite=False):
        attempted.append(url)
        if "COD0MGXFIN" in url:
            raise RuntimeError("CDDIS returned 404")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(destination, "wb") as f:
            f.write(b"# igs\n")
        return destination

    monkeypatch.setattr(cddis, "download", fake_download)
    result = precise_orbits.download_sp3(day, tmp_path)
    assert len(attempted) == 2
    assert "IGS0OPSFIN" in attempted[1]
    assert result.read_bytes() == b"# igs\n"


def test_sp3_reports_every_product_it_tried(tmp_path, monkeypatch):
    def always_fail(url, destination, *, overwrite=False):
        raise RuntimeError("404")

    monkeypatch.setattr(cddis, "download", always_fail)
    with pytest.raises(RuntimeError, match="No precise orbit product") as info:
        precise_orbits.download_sp3(datetime(2023, 4, 17), tmp_path)
    assert "COD0MGXFIN" in str(info.value)
    assert "IGS0OPSFIN" in str(info.value)


def test_a_near_duplicate_toe_is_broken_by_transmission_time():
    """
    A merged daily file holds pairs whose `toe` differ by one or two LSBs of the
    16 s field, with different parameters.  Nearest-`toe` alone flips between them
    as the query crosses the 8 s midpoint, stepping any series computed across it.
    The later-transmitted record is the fresh upload and measures better, so it
    wins the whole window rather than half of it.
    """
    stale = FakeRecord(toe=453_600, transmit_time=453_600 - 7200, tag="superseded")
    fresh = FakeRecord(toe=453_584, transmit_time=453_600 - 3060, tag="fresh")
    records = [stale, fresh]
    # Either side of the 8 s midpoint between the two `toe` values.
    for tow in (453_580.0, 453_592.0, 453_600.0, 460_000.0):
        assert be.select_ephemeris(records, tow_s=tow, week=2258).tag == "fresh"


def test_records_a_real_upload_apart_are_still_chosen_by_toe():
    """The tie-break must not reach across two-hour slots: their ages differ by
    thousands of seconds, far outside the tolerance."""
    records = [
        FakeRecord(toe=446_400, transmit_time=446_400 - 60, tag="older slot"),
        FakeRecord(toe=453_600, transmit_time=453_600 - 7200, tag="nearer slot"),
    ]
    # The older slot was transmitted *later*, and must still lose on `toe`.
    assert be.select_ephemeris(records, tow_s=453_000, week=2258).tag == "nearer slot"


def test_the_duplicate_tolerance_can_be_turned_off():
    """Zero tolerance restores pure nearest-`toe`, which is what the comparison in
    notebook 02 uses to show what the tie-break is worth."""
    stale = FakeRecord(toe=453_600, transmit_time=453_600 - 7200, tag="nearer")
    fresh = FakeRecord(toe=453_584, transmit_time=453_600 - 3060, tag="fresh")
    chosen = be.select_ephemeris(
        [stale, fresh], tow_s=453_600.0, week=2258, duplicate_toe_tolerance_s=0.0
    )
    assert chosen.tag == "nearer"
