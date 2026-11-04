"""GPST and BDT: epochs, the 14 s offset, week rollover, and round trips."""

from datetime import datetime

import pytest

from utils import time_systems as ts


def test_bdt_epoch_is_gpst_week_1356_plus_14_seconds():
    """2006-01-01 UTC is GPS week 1356, second 0 plus 14 s of leap seconds -> 345,614."""
    gps_week, gps_tow = ts.gps_week_and_tow(datetime(2006, 1, 1))
    assert gps_week == 1356
    assert gps_tow == pytest.approx(0.0)  # a UTC midnight, if read as GPST
    # At that UTC instant GPST was 14 s ahead, i.e. it read 00:00:14.
    assert ts.gpst_to_bdt(1356, 14.0) == (0, 0.0)


def test_bdt_is_14_seconds_behind_gpst():
    week, tow = ts.gpst_to_bdt(2400, 100_000.0)
    back = ts.bdt_to_gpst(week, tow)
    assert back == (2400, 100_000.0)
    # Same instant: BDT reads 14 s less.
    gps_total = 2400 * ts.SECONDS_PER_WEEK + 100_000.0
    bdt_total = week * ts.SECONDS_PER_WEEK + tow
    assert gps_total - bdt_total == pytest.approx(
        1356 * ts.SECONDS_PER_WEEK + 14.0
    )


def test_the_offset_constant_is_named_and_is_14():
    assert ts.GPST_MINUS_BDT_S == 14


def test_week_numbers_differ_by_the_epoch_gap():
    """For the same instant the GPS week exceeds the BDT week by 1356, give or take the offset."""
    for tow in (50_000.0, 300_000.0, 600_000.0):
        bdt_week, _ = ts.gpst_to_bdt(2000, tow)
        assert bdt_week in (2000 - 1356, 2000 - 1356 - 1)


def test_conversion_across_the_week_rollover():
    # GPST 7 s into a week -> BDT is 7 s - 14 s: the end of the previous BDT week.
    week, tow = ts.gpst_to_bdt(2400, 7.0)
    assert tow == pytest.approx(ts.SECONDS_PER_WEEK - 7.0)
    assert ts.bdt_to_gpst(week, tow) == (2400, pytest.approx(7.0))
    # And the other way round.
    week, tow = ts.bdt_to_gpst(1000, ts.SECONDS_PER_WEEK - 5.0)
    assert tow == pytest.approx(9.0)  # 14 s past BDT's week end, less 5 s


@pytest.mark.parametrize("tow", [0.0, 1.5, 302_400.0, 604_799.999])
def test_round_trip_through_both_scales(tow):
    for week in (1356, 1500, 2300):
        assert ts.bdt_to_gpst(*ts.gpst_to_bdt(week, tow)) == (week, pytest.approx(tow))


def test_the_recording_week_is_1084_in_bdt():
    """The B2a frames decoded from the 2026-10-14 recording carry WN 1084 (D-052)."""
    utc = datetime(2026, 10, 14, 1, 18, 2)
    gpst = ts.utc_to_gpst(utc, 18)
    week, tow = ts.gpst_to_bdt(*ts.gps_week_and_tow(gpst))
    assert week == 1084
    assert tow == pytest.approx(263_886.0 - 0.0, abs=0.01)  # SOW seen in the decoded frames


def test_bdt_datetime_round_trip():
    epoch = datetime(2026, 10, 14, 1, 18, 6)
    assert ts.datetime_from_bdt(*ts.bdt_week_and_tow(epoch)) == epoch
    assert ts.bdt_week_and_tow(ts.BDT_EPOCH) == (0, 0.0)


def test_gps_datetime_round_trip_matches_the_existing_helpers():
    from utils import broadcast_ephemeris

    epoch = datetime(2026, 10, 14, 1, 18, 6)
    assert ts.gps_week_and_tow(epoch) == broadcast_ephemeris.gps_week_and_tow(epoch)
    assert ts.datetime_from_gps(2392, 5.0) == broadcast_ephemeris.datetime_from_gps(2392, 5.0)


def test_wrap_time_of_week():
    assert ts.wrap_time_of_week(10.0) == 10.0
    assert ts.wrap_time_of_week(400_000.0) == pytest.approx(400_000.0 - 604_800.0)
    assert ts.wrap_time_of_week(-400_000.0) == pytest.approx(-400_000.0 + 604_800.0)
