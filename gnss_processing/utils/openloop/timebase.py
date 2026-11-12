"""
Sample time ("uptime", ms from the start of the sample stream) to GPS time.

Plan section 3.3 and D-100: the time comes from the notebook 02 decoded time
anchor and the receiver clock series; the PPS is a cross-check.

    t_GPST(u) = t_anchor + (u - u_anchor) / 1000 - (dt_rx(u) - dt_rx(u_anchor))

In the direct-referenced model the clock cancels, and the geometry only needs
the time to about 1 ms (a drone at 10 m/s moves 1 cm). So a linear map is
enough; the clock drift term is kept for the absolute model (Mode B).

Notebook 02 does not save its anchors to a file yet (concern C-2). `TimeBase`
can be built by hand from the numbers notebook 02 prints, or from a small YAML
file with the keys `uptime_anchor_ms`, `gpst_anchor_s` and `clock_drift_s_per_s`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml


@dataclass(frozen=True)
class TimeBase:
    """A linear map from uptime (ms) to continuous GPS seconds."""

    uptime_anchor_ms: float
    gpst_anchor_s: float
    clock_drift_s_per_s: float = 0.0
    clock_offset_at_anchor_s: float = 0.0
    source: str = ""

    def gpst(self, uptime_ms) -> np.ndarray:
        """GPS time (s) of the samples at `uptime_ms`."""
        du = (np.asarray(uptime_ms, dtype=float) - self.uptime_anchor_ms) * 1e-3
        return self.gpst_anchor_s + du * (1.0 - self.clock_drift_s_per_s)

    def receiver_clock_s(self, uptime_ms) -> np.ndarray:
        """Receiver clock offset dt_rx (s) at `uptime_ms` (for Mode B)."""
        du = (np.asarray(uptime_ms, dtype=float) - self.uptime_anchor_ms) * 1e-3
        return self.clock_offset_at_anchor_s + self.clock_drift_s_per_s * du

    def uptime(self, gpst_s) -> np.ndarray:
        """The inverse map."""
        dt = (np.asarray(gpst_s, dtype=float) - self.gpst_anchor_s) / (
            1.0 - self.clock_drift_s_per_s)
        return self.uptime_anchor_ms + 1e3 * dt


def load_timebase(path: str | Path) -> TimeBase:
    """Read a TimeBase from YAML (see the module docstring for the keys)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    for key in ("uptime_anchor_ms", "gpst_anchor_s"):
        if key not in data:
            raise ValueError(f"{path}: missing '{key}'")
    return TimeBase(float(data["uptime_anchor_ms"]), float(data["gpst_anchor_s"]),
                    float(data.get("clock_drift_s_per_s", 0.0)),
                    float(data.get("clock_offset_at_anchor_s", 0.0)), f"file:{Path(path).name}")
