"""Host health: CPU load, temperatures, uptime (part of O10).

Wraps ``psutil`` behind one class so the supervisor and the tests do
not depend on the platform. On a machine without temperature sensors
(a laptop on the bench, Windows) the temperature dict is empty.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class HealthSample:
    cpu_pct: float = 0.0
    temps_c: dict[str, float] = field(default_factory=dict)
    uptime_s: float = 0.0


class HealthMonitor:
    """Sample CPU load and temperatures with psutil."""

    def __init__(self, temp_labels: list[str], start_time: float | None = None) -> None:
        self._labels = temp_labels
        self._start = start_time if start_time is not None else time.time()
        self._warned = False
        try:
            import psutil

            self._psutil = psutil
            psutil.cpu_percent(interval=None)  # Prime the non-blocking average.
        except ImportError:  # pragma: no cover - psutil is a hard dependency.
            self._psutil = None

    def sample(self, now: float | None = None) -> HealthSample:
        """Return a sample. Never raise: health must not stop the loop."""
        now = time.time() if now is None else now
        result = HealthSample(uptime_s=max(0.0, now - self._start))
        if self._psutil is None:
            return result
        try:
            result.cpu_pct = float(self._psutil.cpu_percent(interval=None))
        except Exception as exc:  # pragma: no cover
            log.debug("cpu_percent failed: %s", exc)
        try:
            sensors = getattr(self._psutil, "sensors_temperatures", None)
            if sensors is not None:
                for name, entries in (sensors() or {}).items():
                    if self._labels and name not in self._labels:
                        continue
                    for entry in entries:
                        label = f"{name}:{entry.label}" if entry.label else name
                        if entry.current is not None:
                            result.temps_c[label] = round(float(entry.current), 1)
        except Exception as exc:
            if not self._warned:
                log.info("temperature sensors not available: %s", exc)
                self._warned = True
        return result
