"""
GPS time and BeiDou time, and the conversion between them.

Two time scales matter when GPS and BeiDou satellites are used together:

* **GPST** counts weeks from 1980-01-06 00:00:00 UTC.
* **BDT** counts weeks from 2006-01-01 00:00:00 UTC (BDS-SIS-ICD-B2a-1.0, 3.3).

Neither has leap seconds.  They differ by a fixed whole number of seconds, because
both are the UTC second count from their own epoch, and the two epochs are
different amounts of leap seconds apart from UTC.  At the BDT epoch (2006-01-01)
GPST was 14 s ahead of UTC, and BDT was zero ahead of it.  So:

    BDT = GPST - 14 s            (a constant, since neither scale is ever stepped)

The week numbers do not agree either, so converting is a count of seconds from
one epoch, not a subtraction of 14 from the time of week.  `gpst_to_bdt` does it
through the epochs, which handles a week rollover that the 14 s offset crosses.

This module does not convert from UTC: that needs the leap-second count of the
day, which is a fact about the date and not about these two scales.  The caller
supplies it (see `utc_to_gpst`).

All functions are pure and take week and time of week in seconds.
"""

from __future__ import annotations

from datetime import datetime, timedelta

SECONDS_PER_WEEK = 604_800

GPS_EPOCH = datetime(1980, 1, 6)
BDT_EPOCH = datetime(2006, 1, 1)

# Source: both epochs are UTC midnights.  Between the GPS epoch and the BDT epoch
# the UTC second count and GPST drifted apart by 14 leap seconds (the 14th was
# inserted at the end of 2005), so at 2006-01-01 00:00:00 UTC GPST read
# 2006-01-01 00:00:14.  BDT started at zero there.  The BDSBAS ICD (3.3) states
# the same offset.  The offset has been constant since.
GPST_MINUS_BDT_S = 14

# The two epochs as a whole number of seconds, for the week arithmetic below.
_BDT_EPOCH_IN_GPST_S = int((BDT_EPOCH - GPS_EPOCH).total_seconds()) + GPST_MINUS_BDT_S


def _normalise(total_seconds: float) -> tuple[int, float]:
    """`(week, time_of_week)` for a count of seconds from an epoch."""
    week = int(total_seconds // SECONDS_PER_WEEK)
    return week, float(total_seconds - week * SECONDS_PER_WEEK)


def gpst_to_bdt(gps_week: int, gps_tow_s: float) -> tuple[int, float]:
    """BDT `(week, time_of_week)` of a GPST `(week, time_of_week)`."""
    seconds_since_gps_epoch = gps_week * SECONDS_PER_WEEK + gps_tow_s
    return _normalise(seconds_since_gps_epoch - _BDT_EPOCH_IN_GPST_S)


def bdt_to_gpst(bdt_week: int, bdt_tow_s: float) -> tuple[int, float]:
    """GPST `(week, time_of_week)` of a BDT `(week, time_of_week)`."""
    seconds_since_bdt_epoch = bdt_week * SECONDS_PER_WEEK + bdt_tow_s
    return _normalise(seconds_since_bdt_epoch + _BDT_EPOCH_IN_GPST_S)


def bdt_week_and_tow(epoch: datetime) -> tuple[int, float]:
    """
    `(week, time_of_week)` of a datetime that already is BDT.

    Like `broadcast_ephemeris.gps_week_and_tow`, this cannot tell whether the
    datetime is BDT, GPST or UTC; it takes it as BDT.
    """
    return _normalise((epoch - BDT_EPOCH).total_seconds())


def datetime_from_bdt(week: int, tow_s: float) -> datetime:
    """Inverse of `bdt_week_and_tow`."""
    return BDT_EPOCH + timedelta(weeks=week, seconds=tow_s)


def gps_week_and_tow(epoch: datetime) -> tuple[int, float]:
    """`(week, time_of_week)` of a datetime that already is GPST."""
    return _normalise((epoch - GPS_EPOCH).total_seconds())


def datetime_from_gps(week: int, tow_s: float) -> datetime:
    """Inverse of `gps_week_and_tow`."""
    return GPS_EPOCH + timedelta(weeks=week, seconds=tow_s)


def utc_to_gpst(utc: datetime, gps_minus_utc_s: int) -> datetime:
    """
    GPST of a UTC datetime, given GPST - UTC for that date.

    `gps_minus_utc_s` is 18 for any date since 2017-01-01.  It is an argument, not
    a constant here, because it changes whenever a leap second is inserted and a
    wrong default would be wrong silently.
    """
    return utc + timedelta(seconds=gps_minus_utc_s)


def wrap_time_of_week(delta_s: float) -> float:
    """
    Fold a time difference into +/- half a week.

    `t - toe` from two times of week is wrong by a whole week when a week rollover
    lies between them; folding fixes it (BDS-SIS-ICD-B2a-1.0, Table 7-9 note).
    """
    half = SECONDS_PER_WEEK / 2
    if delta_s > half:
        return delta_s - SECONDS_PER_WEEK
    if delta_s < -half:
        return delta_s + SECONDS_PER_WEEK
    return delta_s
