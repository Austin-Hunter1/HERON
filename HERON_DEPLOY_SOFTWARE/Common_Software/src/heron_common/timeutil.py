"""UTC time helpers.

All HERON timestamps are UTC. File names use the compact form
``YYYYMMDDTHHMMSSZ`` so they sort by time and have no ``:`` characters.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime


def utc_now() -> float:
    """Return the current UNIX time in seconds (float)."""
    return time.time()


def utc_stamp(unix_seconds: float | None = None) -> str:
    """Format a UNIX time as ``YYYYMMDDTHHMMSSZ``.

    Use the current time when ``unix_seconds`` is ``None``.
    """
    if unix_seconds is None:
        unix_seconds = utc_now()
    return datetime.fromtimestamp(unix_seconds, tz=UTC).strftime("%Y%m%dT%H%M%SZ")


def utc_iso(unix_seconds: float | None = None) -> str:
    """Format a UNIX time as ISO 8601 with millisecond precision."""
    if unix_seconds is None:
        unix_seconds = utc_now()
    return datetime.fromtimestamp(unix_seconds, tz=UTC).isoformat(timespec="milliseconds")
