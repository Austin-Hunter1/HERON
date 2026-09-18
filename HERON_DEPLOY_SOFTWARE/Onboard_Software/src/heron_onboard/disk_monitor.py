"""Free-space monitoring (REQUIREMENTS O11).

The threshold math is a pure function so it has a unit test. The
``DiskMonitor`` wraps ``shutil.disk_usage`` behind a small interface
so tests can replace it with a fake.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

GB = 1_000_000_000


@dataclass(frozen=True)
class DiskStatus:
    """One free-space sample."""

    total_bytes: int
    free_bytes: int
    below_threshold: bool
    error: str | None = None

    @property
    def free_gb(self) -> float:
        return self.free_bytes / GB

    @property
    def free_pct(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return 100.0 * self.free_bytes / self.total_bytes


def is_below_threshold(
    free_bytes: int, total_bytes: int, min_free_gb: float, min_free_pct: float
) -> bool:
    """True when free space is under the GB limit or the percent limit.

    Either limit alone trips the rule, so a small disk uses the percent
    limit and a large disk uses the GB limit.
    """
    if free_bytes < min_free_gb * GB:
        return True
    if total_bytes > 0 and (100.0 * free_bytes / total_bytes) < min_free_pct:
        return True
    return False


def seconds_until_threshold(
    free_bytes: int,
    total_bytes: int,
    rate_bytes_per_s: float,
    min_free_gb: float,
    min_free_pct: float,
) -> float:
    """Estimate recording seconds left before the threshold trips."""
    if rate_bytes_per_s <= 0:
        return float("inf")
    limit = max(min_free_gb * GB, total_bytes * min_free_pct / 100.0)
    return max(0.0, (free_bytes - limit) / rate_bytes_per_s)


class DiskMonitor:
    """Sample free space on the data disk."""

    def __init__(
        self,
        path: Path,
        min_free_gb: float,
        min_free_pct: float,
        usage_fn: Callable[[Path], tuple[int, int, int]] | None = None,
    ) -> None:
        self._path = path
        self._min_free_gb = min_free_gb
        self._min_free_pct = min_free_pct
        self._usage_fn = usage_fn or (lambda p: tuple(shutil.disk_usage(p)))  # type: ignore[return-value]

    def check(self) -> DiskStatus:
        """Return the current status. An unreadable path counts as low."""
        try:
            total, _used, free = self._usage_fn(self._path)
        except OSError as exc:
            return DiskStatus(0, 0, True, error=f"cannot read {self._path}: {exc}")
        low = is_below_threshold(free, total, self._min_free_gb, self._min_free_pct)
        return DiskStatus(total, free, low)
