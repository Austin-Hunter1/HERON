"""Capture backends: the real recorder processes and a fake.

``CaptureBackend`` is the narrow interface the supervisor uses. The
real backend launches one ``heron_recorder`` per SDR (D-018), so one
SDR fault cannot take the others down (O15). The fake backend runs in
memory for unit tests, the ``demo`` mode, and bench tests of the link
without SDRs.
"""

from __future__ import annotations

import logging
import math
import time
from abc import ABC, abstractmethod
from pathlib import Path

from heron_common.protocol import SdrStatus

from heron_onboard.capture.recorder_process import RecorderProcess, build_recorder_command
from heron_onboard.config import CaptureConfig, SdrConfig

log = logging.getLogger(__name__)


class CaptureBackend(ABC):
    """Start and stop the recorders and report their status."""

    @abstractmethod
    def start(self, flight_dir: Path, file_prefix: str, now: float) -> dict:
        """Start all recorders. Return timing info for the metadata."""

    @abstractmethod
    def stop(self) -> None:
        """Stop all recorders and wait for them to exit."""

    @abstractmethod
    def poll(self) -> list[SdrStatus]:
        """Return the current status of every SDR (running or not)."""

    @abstractmethod
    def running_count(self) -> int:
        """How many recorders are still running."""

    @property
    def is_active(self) -> bool:
        """True between ``start`` and ``stop``."""
        return self.running_count() > 0


class RecorderBackend(CaptureBackend):
    """One ``heron_recorder`` process per SDR."""

    def __init__(self, sdrs: list[SdrConfig], capture: CaptureConfig) -> None:
        self._sdrs = sdrs
        self._capture = capture
        self._procs: dict[str, RecorderProcess] = {}
        self._idle_status = {s.id: SdrStatus(id=s.id) for s in sdrs}

    def start(self, flight_dir: Path, file_prefix: str, now: float) -> dict:
        """Launch every recorder with the same sync epoch and start time.

        Units with a PPS time source align their device clocks at
        ``sync_epoch`` (the same PPS edge). Units without PPS (the
        B200minis, D-020) set their clock from the host time. All units
        start streaming at the same ``start_time``, so the non-PPS
        units are within host-clock accuracy (milliseconds) of the PPS
        units; their exact offset is found after the flight (Q-013).
        """
        sync_epoch = float(math.ceil(now + self._capture.sync_lead_s))
        start_time = sync_epoch + self._capture.start_lead_s
        self._procs = {}
        for sdr in self._sdrs:
            argv = build_recorder_command(
                self._capture.recorder_binary,
                sdr,
                self._capture,
                flight_dir,
                file_prefix,
                sync_epoch,
                start_time,
            )
            log_path = flight_dir / sdr.id / f"{file_prefix}_recorder_log.txt"
            proc = RecorderProcess(sdr.id, argv, log_path=log_path)
            proc.start()
            self._procs[sdr.id] = proc
        return {"sync_epoch": sync_epoch, "start_time": start_time}

    def stop(self) -> None:
        for proc in self._procs.values():
            proc.stop(self._capture.stop_timeout_s)

    def poll(self) -> list[SdrStatus]:
        statuses = []
        for sdr in self._sdrs:
            proc = self._procs.get(sdr.id)
            if proc is None:
                statuses.append(self._idle_status[sdr.id])
                continue
            proc.poll()
            status = proc.status()
            # A recorder that never reported ready in time is a fault.
            if (
                not proc.ready
                and proc.started_at is not None
                and proc.exit_code is None
                and time.time() - proc.started_at > self._capture.ready_timeout_s
                and status.fault is None
            ):
                status = status.model_copy(update={"fault": "recorder not ready in time"})
            statuses.append(status)
        return statuses

    def running_count(self) -> int:
        return sum(1 for p in self._procs.values() if p.poll())

    def clear(self) -> None:
        """Forget finished processes so ``poll`` reports idle again."""
        self._procs = {}


class FakeCaptureBackend(CaptureBackend):
    """In-memory stand-in for tests and the demo.

    It reports every configured SDR as present and streaming, and
    grows the sample count at the configured rate. Tests can inject
    faults with ``fail(sdr_id)`` and ``kill_all()``.
    """

    def __init__(self, sdrs: list[SdrConfig], clock=time.time) -> None:
        self._sdrs = sdrs
        self._clock = clock
        self._running: dict[str, float] = {}
        self._faults: dict[str, str] = {}
        self.start_calls: list[tuple[Path, str]] = []
        self.stop_calls = 0

    def start(self, flight_dir: Path, file_prefix: str, now: float) -> dict:
        self.start_calls.append((flight_dir, file_prefix))
        self._running = {s.id: now for s in self._sdrs}
        self._faults = {}
        return {"sync_epoch": 0.0, "start_time": now}

    def stop(self) -> None:
        self.stop_calls += 1
        self._running = {}

    def poll(self) -> list[SdrStatus]:
        now = self._clock()
        out = []
        for sdr in self._sdrs:
            fault = self._faults.get(sdr.id)
            since = self._running.get(sdr.id)
            if since is None:
                out.append(SdrStatus(id=sdr.id, present=True, fault=fault))
                continue
            elapsed = max(0.0, now - since)
            out.append(
                SdrStatus(
                    id=sdr.id,
                    present=True,
                    streaming=True,
                    ref_locked=True,
                    samples=int(elapsed * sdr.sample_rate_hz),
                    rate_mbps=sdr.bytes_per_second / 1e6,
                    segment=int(elapsed // 30),
                )
            )
        return out

    def running_count(self) -> int:
        return len(self._running)

    def fail(self, sdr_id: str, reason: str = "fake fault") -> None:
        """Simulate one recorder exiting with an error."""
        self._running.pop(sdr_id, None)
        self._faults[sdr_id] = reason

    def kill_all(self, reason: str = "fake: all recorders died") -> None:
        for sdr in self._sdrs:
            self.fail(sdr.id, reason)
