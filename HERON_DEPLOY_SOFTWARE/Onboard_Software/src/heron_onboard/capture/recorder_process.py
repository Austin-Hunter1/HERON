"""One ``heron_recorder`` process: command line, launch, status, stop.

The C++ recorder (``Onboard_Software/recorder/``) records one SDR. It
takes every setting on its command line, so it has no config file of
its own. It prints one JSON line per second on stdout::

    {"event":"status","t":1700000000.5,"streaming":true,"ref_locked":true,
     "samples":22000000,"overflows":0,"host_drops":0,"segment":3,
     "rate_mbps":44.0}

and event lines (``ready``, ``started``, ``segment``, ``error``,
``stopped``). This module builds the command line, reads those lines
on a thread, and turns them into ``SdrStatus`` for telemetry.

Lines that are not JSON (UHD prints ``O`` on overflow and its own log
text) go to the debug log.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO

from heron_common.protocol import SdrStatus

from heron_onboard.config import CaptureConfig, SdrConfig

log = logging.getLogger(__name__)


def build_recorder_command(
    binary: Path,
    sdr: SdrConfig,
    capture: CaptureConfig,
    outdir: Path,
    file_prefix: str,
    sync_epoch: float,
    start_time: float,
) -> list[str]:
    """Build the argv for one recorder process.

    ``sync_epoch`` is the common integer UNIX second at which every
    recorder aligns its device clock to the PPS edge. ``start_time`` is
    the common UNIX second of the first sample. Both are 0 when
    ``time_source`` is ``none`` (then each recorder starts on its own).
    """
    argv = [
        str(binary),
        "--args",
        sdr.uhd_args,
        "--rate",
        repr(sdr.sample_rate_hz),
        "--clock-source",
        sdr.clock_source,
        "--time-source",
        sdr.time_source,
        "--sync-epoch",
        repr(sync_epoch),
        "--start-time",
        repr(start_time),
        "--segment-seconds",
        repr(capture.segment_seconds),
        "--cpu-format",
        capture.cpu_format,
        "--wire-format",
        capture.wire_format,
        "--wire-peak",
        repr(capture.wire_peak),
        "--buffer-seconds",
        repr(capture.buffer_seconds),
        "--status-interval",
        repr(capture.status_interval_s),
        "--lock-timeout",
        repr(capture.lock_timeout_s),
        "--outdir",
        str(outdir),
        "--file-prefix",
        file_prefix,
        "--file-extension",
        capture.file_extension,
        "--sdr-id",
        sdr.id,
    ]
    for ch in sdr.channels:
        spec = (
            f"index={ch.index},id={ch.id},freq={ch.center_freq_hz!r},gain={ch.gain_db!r},"
            f"bw={ch.bandwidth_hz!r},antenna={ch.antenna}"
        )
        if ch.subdev:
            spec += f",subdev={ch.subdev}"
        argv += ["--channel", spec]
    return argv


def parse_status_line(line: str) -> dict | None:
    """Return the JSON object in ``line``, or ``None`` when it is not one."""
    stripped = line.strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def apply_status(status: SdrStatus, event: dict) -> SdrStatus:
    """Merge one recorder event into an ``SdrStatus`` (returns a new one)."""
    kind = event.get("event")
    updates: dict = {}
    if kind == "status":
        updates = {
            "present": True,
            "streaming": bool(event.get("streaming", False)),
            "overflows": int(event.get("overflows", status.overflows)),
            "host_drops": int(event.get("host_drops", status.host_drops)),
            "samples": int(event.get("samples", status.samples)),
            "rate_mbps": float(event.get("rate_mbps", status.rate_mbps)),
            "segment": int(event.get("segment", status.segment)),
        }
        if "ref_locked" in event:
            updates["ref_locked"] = bool(event["ref_locked"])
    elif kind == "ready":
        updates = {"present": True}
        if "ref_locked" in event:
            updates["ref_locked"] = bool(event["ref_locked"])
    elif kind == "started":
        updates = {"present": True, "streaming": True}
    elif kind == "error":
        updates = {"fault": str(event.get("message", "recorder error"))}
    elif kind == "stopped":
        updates = {"streaming": False}
    return status.model_copy(update=updates)


class RecorderProcess:
    """Launch, watch, and stop one recorder process."""

    def __init__(
        self,
        sdr_id: str,
        argv: list[str],
        env_extra: dict[str, str] | None = None,
        log_path: Path | None = None,
    ) -> None:
        self.sdr_id = sdr_id
        self.argv = argv
        self._env_extra = env_extra or {}
        # Every console line (UHD messages and our JSON events) goes to
        # this file next to the data, like the SDR team's capture logs.
        self.log_path = log_path
        self._log_file: IO[str] | None = None
        self._proc: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._status = SdrStatus(id=sdr_id)
        self.ready = False
        self.started_at: float | None = None
        self.exit_code: int | None = None
        self.last_lines: list[str] = []

    def start(self) -> None:
        """Launch the process and its stdout reader thread."""
        env = dict(os.environ)
        # Send UHD's fast-path "O"/"D" characters to stderr, not into our JSON.
        env.setdefault("UHD_LOG_FASTPATH_DISABLE", "1")
        env.update(self._env_extra)
        log.info("[%s] launching: %s", self.sdr_id, " ".join(self.argv))
        if self.log_path is not None:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                self._log_file = self.log_path.open("a", encoding="utf-8")
                self._log_file.write("# " + " ".join(self.argv) + "\n")
                self._log_file.flush()
            except OSError as exc:
                log.warning("[%s] cannot open recorder log %s: %s", self.sdr_id, self.log_path, exc)
                self._log_file = None
        try:
            self._proc = subprocess.Popen(
                self.argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
        except OSError as exc:
            self.exit_code = -1
            with self._lock:
                self._status = self._status.model_copy(update={"fault": f"cannot launch: {exc}"})
            log.error("[%s] cannot launch recorder: %s", self.sdr_id, exc)
            return
        self.started_at = time.time()
        self._reader = threading.Thread(
            target=self._read_stdout, name=f"rec-{self.sdr_id}", daemon=True
        )
        self._reader.start()

    def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            if self._log_file is not None:
                try:
                    self._log_file.write(line)
                    self._log_file.flush()
                except OSError:
                    self._log_file = None
            event = parse_status_line(line)
            if event is None:
                text = line.rstrip()
                if text:
                    log.debug("[%s] %s", self.sdr_id, text)
                    self.last_lines = (self.last_lines + [text])[-20:]
                continue
            kind = event.get("event")
            if kind == "ready":
                self.ready = True
            if kind == "error":
                log.error("[%s] recorder error: %s", self.sdr_id, event.get("message"))
            elif kind in ("ready", "started", "stopped", "segment"):
                log.info("[%s] %s %s", self.sdr_id, kind, _brief(event))
            with self._lock:
                self._status = apply_status(self._status, event)
        self._proc.stdout.close()
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    def status(self) -> SdrStatus:
        """Return the latest status (thread safe)."""
        with self._lock:
            status = self._status
        if self.exit_code is not None and self.exit_code != 0 and status.fault is None:
            status = status.model_copy(
                update={"fault": f"recorder exited with code {self.exit_code}"}
            )
        return status

    def poll(self) -> bool:
        """True while the process runs. Records the exit code when it ends."""
        if self._proc is None:
            return False
        code = self._proc.poll()
        if code is None:
            return True
        if self.exit_code is None:
            self.exit_code = code
            with self._lock:
                self._status = self._status.model_copy(update={"streaming": False})
            level = logging.INFO if code == 0 else logging.ERROR
            log.log(level, "[%s] recorder exited with code %d", self.sdr_id, code)
            if code != 0 and self.last_lines:
                log.error("[%s] last output: %s", self.sdr_id, " | ".join(self.last_lines[-5:]))
        return False

    def stop(self, timeout_s: float) -> None:
        """Ask the recorder to stop (SIGTERM), then kill it if it hangs."""
        if self._proc is None or self._proc.poll() is not None:
            self.poll()
            return
        log.info("[%s] stopping recorder", self.sdr_id)
        try:
            if sys.platform == "win32":
                self._proc.terminate()
            else:
                self._proc.send_signal(signal.SIGTERM)
        except OSError:
            pass
        try:
            self._proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            log.error("[%s] recorder did not exit in %.0f s; killing", self.sdr_id, timeout_s)
            self._proc.kill()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                log.critical("[%s] recorder cannot be killed", self.sdr_id)
        self.poll()


def _brief(event: dict) -> str:
    """Short text of an event for the log, without the event key."""
    return " ".join(f"{k}={v}" for k, v in event.items() if k != "event")
