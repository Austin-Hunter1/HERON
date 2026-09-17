"""``CaptureManager``: one flight at a time, from start to metadata.

The manager owns the flight directory, the backend, and the metadata
file. The supervisor calls ``start``, ``stop``, and ``poll``; it never
touches the recorder processes directly.
"""

from __future__ import annotations

import logging
from pathlib import Path

from heron_common.protocol import SdrStatus
from heron_common.timeutil import utc_stamp

from heron_onboard.capture.backend import CaptureBackend
from heron_onboard.capture.metadata import build_metadata, finalize_metadata, write_metadata
from heron_onboard.capture.metadata_yaml import write_metadata_yaml
from heron_onboard.config import OnboardConfig

log = logging.getLogger(__name__)


class CaptureManager:
    """Start, watch, and stop one recording."""

    def __init__(self, config: OnboardConfig, backend: CaptureBackend, version: str) -> None:
        self._config = config
        self._backend = backend
        self._version = version
        self.flight_id: str | None = None
        self.flight_dir: Path | None = None
        self._meta: dict | None = None
        self._last_statuses: list[SdrStatus] = [SdrStatus(id=s.id) for s in config.sdr]

    @property
    def active(self) -> bool:
        return self.flight_id is not None

    def start(self, flight_id: str, now: float, reason: str) -> None:
        """Create the flight directory, write metadata, start recorders.

        Raise ``OSError`` when the directory cannot be created; the
        supervisor reports that as a capture fault.
        """
        flight_dir = self._config.disk.data_root / flight_id
        flight_dir.mkdir(parents=True, exist_ok=True)
        file_prefix = utc_stamp(now)
        timing = self._backend.start(flight_dir, file_prefix, now)
        self.flight_id = flight_id
        self.flight_dir = flight_dir
        self._meta = build_metadata(
            self._config, flight_id, file_prefix, now, self._version, timing, reason
        )
        path = write_metadata(flight_dir, self._meta)
        self._write_yaml(flight_dir, file_prefix)
        log.info("recording %s started (%s); metadata %s", flight_id, reason, path)

    def stop(self, now: float, reason: str) -> None:
        """Stop the recorders and write the final metadata."""
        if self.flight_id is None:
            return
        log.info("recording %s stopping (%s)", self.flight_id, reason)
        self._backend.stop()
        statuses = self._backend.poll()
        if self._meta is not None and self.flight_dir is not None:
            self._meta = finalize_metadata(self._meta, now, statuses)
            self._meta["stop_reason"] = reason
            try:
                write_metadata(self.flight_dir, self._meta)
            except OSError as exc:
                log.error("cannot write final metadata: %s", exc)
            self._write_yaml(self.flight_dir, self._meta["file_prefix"])
        clear = getattr(self._backend, "clear", None)
        if clear is not None:
            clear()
        self.flight_id = None
        self.flight_dir = None
        self._meta = None

    def _write_yaml(self, flight_dir: Path, file_prefix: str) -> None:
        """Write the SDR team's metadata.yml; never let it stop a recording."""
        try:
            write_metadata_yaml(flight_dir, self._config, file_prefix)
        except (OSError, ValueError) as exc:
            log.error("cannot write metadata.yml: %s", exc)

    def poll(self) -> list[SdrStatus]:
        """Return the current per-SDR status list."""
        self._last_statuses = self._backend.poll()
        return self._last_statuses

    def running_count(self) -> int:
        return self._backend.running_count()

    def data_rate_mbps(self, statuses: list[SdrStatus]) -> float:
        """Sum of the reported per-SDR rates."""
        return sum(s.rate_mbps for s in statuses if s.streaming)
