"""The onboard main loop.

One single-threaded loop (period ``general.tick_s``) does, in order:

1. Keep the link transport open (retry after a failure, O14).
2. Read frames, decode commands, apply them to the controller, send
   acks.
3. Run the controller timers (link timeout, fallback).
4. Execute controller actions: start or stop capture.
5. Poll the recorders; report a total capture loss.
6. Check the disk on its interval.
7. Send telemetry on its interval.

Everything that needs a clock takes ``now`` from ``self._clock`` so
tests can drive the loop with ``step(now)`` and a fake clock.
"""

from __future__ import annotations

import logging
import signal
import time
from collections.abc import Callable

from heron_common.protocol import (
    Ack,
    Command,
    CommandName,
    FrameParser,
    Telemetry,
    Transport,
    TransportError,
    decode_message,
    encode_message,
)

from heron_onboard.capture.manager import CaptureManager
from heron_onboard.config import OnboardConfig
from heron_onboard.disk_monitor import DiskMonitor, DiskStatus
from heron_onboard.health import HealthMonitor
from heron_onboard.state_machine import ActionKind, RecordingController, State

log = logging.getLogger(__name__)

LINK_RETRY_S = 5.0


class Supervisor:
    """Join the link, the controller, capture, disk, and health."""

    def __init__(
        self,
        config: OnboardConfig,
        transport: Transport,
        capture: CaptureManager,
        disk: DiskMonitor,
        health: HealthMonitor,
        version: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._cfg = config
        self._transport = transport
        self._capture = capture
        self._disk = disk
        self._health = health
        self._version = version
        self._clock = clock
        now = clock()
        self._controller = RecordingController(config.control, now)
        self._parser = FrameParser()
        self._tlm_seq = 0
        self._last_tlm = 0.0
        self._last_disk_check = 0.0
        self._last_link_try = 0.0
        self._disk_status = DiskStatus(0, 0, False)
        self._statuses = capture.poll()
        self._stop_requested = False
        self.telemetry_sent = 0
        self._ensure_data_root()

    def _ensure_data_root(self) -> None:
        """Create the data directory so a fresh install can record.

        The disk check reads free space at this path. A missing path
        counts as "disk low" and blocks recording, so create it here.
        A failure is logged; the disk check then reports it each cycle.
        """
        try:
            self._cfg.disk.data_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.error("cannot create data root %s: %s", self._cfg.disk.data_root, exc)

    # ----- public --------------------------------------------------------------

    @property
    def controller(self) -> RecordingController:
        return self._controller

    def request_stop(self) -> None:
        """Ask the loop to end (from a signal handler)."""
        self._stop_requested = True

    def run(self, max_seconds: float = 0.0) -> None:
        """Run until a signal or ``request_stop``; then stop capture cleanly.

        ``max_seconds`` > 0 ends the loop after that time (bench and
        soak runs). 0 means run forever (flight).
        """
        self._install_signals()
        log.info(
            "supervisor start; version %s; transport %s", self._version, self._transport.describe()
        )
        deadline = self._clock() + max_seconds if max_seconds > 0 else None
        try:
            while not self._stop_requested:
                now = self._clock()
                if deadline is not None and now >= deadline:
                    log.info("max run time reached")
                    break
                self.step(now)
                time.sleep(self._cfg.general.tick_s)
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Stop any recording and close the link."""
        if self._capture.active:
            self._capture.stop(self._clock(), "supervisor shutdown")
        self._transport.close()
        log.info("supervisor stopped")

    def step(self, now: float) -> None:
        """One loop iteration at time ``now``."""
        self._ensure_link(now)
        self._handle_incoming(now)
        self._run_actions(self._controller.tick(now), now)
        self._poll_capture(now)
        self._check_disk(now)
        self._send_telemetry_if_due(now)

    # ----- link ----------------------------------------------------------------

    def _ensure_link(self, now: float) -> None:
        if self._transport.is_open:
            return
        if now - self._last_link_try < LINK_RETRY_S:
            return
        self._last_link_try = now
        try:
            self._transport.open()
        except TransportError as exc:
            log.warning("link open failed (retry in %.0f s): %s", LINK_RETRY_S, exc)

    def _handle_incoming(self, now: float) -> None:
        data = self._transport.recv()
        if not data:
            return
        for payload in self._parser.feed(data):
            try:
                msg = decode_message(payload)
            except ValueError as exc:
                log.warning("bad frame ignored: %s", exc)
                continue
            self._run_actions(self._controller.on_frame_received(now), now)
            if isinstance(msg, Command):
                self._handle_command(msg, now)
            else:
                log.debug("ignored %s frame from ground", msg.kind)

    def _handle_command(self, cmd: Command, now: float) -> None:
        log.info("command %s seq=%d args=%s", cmd.name, cmd.seq, cmd.args)
        result = self._controller.handle_command(cmd, now)
        self._run_actions(result.actions, now)
        ack = Ack(
            seq=cmd.seq,
            ok=result.ok,
            message=result.message,
            state=self._controller.state,
            sent_at=now,
        )
        self._send(encode_message(ack))
        if cmd.name == CommandName.STATUS:
            self._send_telemetry(now)

    def _send(self, frame: bytes) -> None:
        try:
            self._transport.send(frame)
        except Exception as exc:  # A transport bug must not stop recording.
            log.warning("link send failed: %s", exc)

    # ----- actions -------------------------------------------------------------

    def _run_actions(self, actions, now: float) -> None:
        for action in actions:
            if action.kind == ActionKind.START_CAPTURE:
                self._start_capture(action.flight_id or "unnamed", now, action.reason)
            elif action.kind == ActionKind.STOP_CAPTURE:
                self._capture.stop(now, action.reason)
            elif action.kind == ActionKind.ALERT:
                log.warning("ALERT: %s", action.reason)

    def _start_capture(self, flight_id: str, now: float, reason: str) -> None:
        try:
            self._capture.start(flight_id, now, reason)
        except OSError as exc:
            log.error("cannot start capture: %s", exc)
            self._run_actions(self._controller.on_capture_lost(now, f"start failed: {exc}"), now)

    # ----- capture and disk ----------------------------------------------------

    def _poll_capture(self, now: float) -> None:
        self._statuses = self._capture.poll()
        if self._controller.state == State.RECORDING and self._capture.running_count() == 0:
            faults = [f"{s.id}: {s.fault}" for s in self._statuses if s.fault]
            reason = "; ".join(faults) or "all recorders exited"
            self._run_actions(self._controller.on_capture_lost(now, reason), now)

    def _check_disk(self, now: float) -> None:
        if now - self._last_disk_check < self._cfg.disk.check_interval_s:
            return
        self._last_disk_check = now
        self._disk_status = self._disk.check()
        if self._disk_status.error:
            log.error("disk check: %s", self._disk_status.error)
        if self._disk_status.below_threshold:
            self._run_actions(self._controller.on_disk_low(now, self._disk_status.free_gb), now)
        else:
            self._controller.on_disk_ok()

    # ----- telemetry -----------------------------------------------------------

    def _send_telemetry_if_due(self, now: float) -> None:
        if now - self._last_tlm >= 1.0 / self._cfg.telemetry.rate_hz:
            self._send_telemetry(now)

    def build_telemetry(self, now: float) -> Telemetry:
        """Assemble the O10 health set from the current samples."""
        health = self._health.sample(now)
        ctrl = self._controller
        self._tlm_seq += 1
        return Telemetry(
            seq=self._tlm_seq,
            sent_at=now,
            uptime_s=health.uptime_s,
            state=ctrl.state,
            flight_id=ctrl.flight_id if ctrl.state == State.RECORDING else None,
            recording_s=ctrl.recording_seconds(now),
            sdrs=self._statuses,
            data_rate_mbps=self._capture.data_rate_mbps(self._statuses),
            disk_free_gb=round(self._disk_status.free_gb, 2),
            disk_free_pct=round(self._disk_status.free_pct, 1),
            disk_alert=self._disk_status.below_threshold,
            cpu_pct=health.cpu_pct,
            temps_c=health.temps_c if self._cfg.telemetry.include_temps else {},
            last_cmd_seq=ctrl.last_cmd_seq,
            fault=ctrl.fault,
            version=self._version,
            link_good_frames=self._parser.good_frames,
            link_bad_frames=self._parser.bad_frames,
        )

    def _send_telemetry(self, now: float) -> None:
        self._last_tlm = now
        self._send(encode_message(self.build_telemetry(now)))
        self.telemetry_sent += 1

    # ----- signals -------------------------------------------------------------

    def _install_signals(self) -> None:
        def handler(signum, _frame):
            log.info("signal %d received; shutting down", signum)
            self.request_stop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):  # Not on the main thread (demo mode).
                pass
