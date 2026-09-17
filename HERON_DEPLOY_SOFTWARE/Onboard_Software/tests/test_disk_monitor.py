"""Tests for the disk threshold math and the monitor wrapper."""

from pathlib import Path

from heron_onboard.disk_monitor import (
    GB,
    DiskMonitor,
    is_below_threshold,
    seconds_until_threshold,
)


def test_gb_limit_trips():
    assert is_below_threshold(
        free_bytes=19 * GB, total_bytes=2000 * GB, min_free_gb=20, min_free_pct=0
    )
    assert not is_below_threshold(
        free_bytes=21 * GB, total_bytes=2000 * GB, min_free_gb=20, min_free_pct=0
    )


def test_percent_limit_trips():
    assert is_below_threshold(
        free_bytes=4 * GB, total_bytes=100 * GB, min_free_gb=0, min_free_pct=5
    )
    assert not is_below_threshold(
        free_bytes=6 * GB, total_bytes=100 * GB, min_free_gb=0, min_free_pct=5
    )


def test_seconds_until_threshold():
    """At 200 MB/s with 220 GB above the limit, about 1100 s remain."""
    secs = seconds_until_threshold(240 * GB, 2000 * GB, 200e6, min_free_gb=20, min_free_pct=0)
    assert abs(secs - 1100) < 1
    assert seconds_until_threshold(1, 1, 0.0, 0, 0) == float("inf")


def test_monitor_uses_injected_usage(tmp_path: Path):
    mon = DiskMonitor(
        tmp_path, min_free_gb=10, min_free_pct=0, usage_fn=lambda p: (100 * GB, 95 * GB, 5 * GB)
    )
    status = mon.check()
    assert status.below_threshold and status.free_gb == 5 and status.free_pct == 5


def test_monitor_unreadable_path_counts_as_low(tmp_path: Path):
    def boom(_p):
        raise OSError("gone")

    status = DiskMonitor(tmp_path, 0, 0, usage_fn=boom).check()
    assert status.below_threshold and status.error
