"""Recording decision logic (pure, no I/O).

The controller decides when to start and stop recording. It takes
commands from the ground, link events, disk events, and capture
events, and it returns actions for the supervisor to execute. It never
touches hardware, so every rule here has a fast unit test.

Rules it implements:

- D-010: ground commands are primary. A fallback starts recording when
  no ground link appears (D-017 sets the grace-period form).
- D-011: a link loss during recording does not stop recording.
- O11: a disk-low event stops recording and blocks a new start.
- O15: one recorder fault is reported, the others keep going. Only when
  every recorder is gone does the payload enter FAULT.

States:

- ``IDLE``: ready, not recording.
- ``RECORDING``: recorders are running.
- ``FAULT``: recording failed completely. A STOP command clears it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from heron_common.protocol import Command, CommandName
from heron_common.timeutil import utc_stamp

from heron_onboard.config import ControlConfig

FLIGHT_ID_PATTERN = re.compile(r"[^A-Za-z0-9_\-]+")
FLIGHT_ID_MAX_LEN = 64


class State(StrEnum):
    """Recording state, as reported in telemetry."""

    IDLE = "IDLE"
    RECORDING = "RECORDING"
    FAULT = "FAULT"


class LinkState(StrEnum):
    """What the payload knows about the ground link."""

    NEVER = "NEVER"  # No valid frame since boot.
    UP = "UP"
    LOST = "LOST"


class ActionKind(StrEnum):
    START_CAPTURE = "START_CAPTURE"
    STOP_CAPTURE = "STOP_CAPTURE"
    ALERT = "ALERT"


@dataclass(frozen=True)
class Action:
    """One thing the supervisor must do now."""

    kind: ActionKind
    flight_id: str | None = None
    reason: str = ""


@dataclass
class CommandResult:
    """The outcome of one command, used to build the Ack."""

    ok: bool
    message: str
    actions: list[Action] = field(default_factory=list)


def make_flight_id(now: float, prefix: str = "", requested: str | None = None) -> str:
    """Build a safe flight id.

    Use the operator's name when the START command carries one, else
    the UTC start time. Strip characters that are not safe in a path.
    """
    base = requested.strip() if requested else ""
    base = FLIGHT_ID_PATTERN.sub("_", base).strip("_")
    if not base:
        base = utc_stamp(now)
    if prefix:
        base = f"{prefix}_{base}"
    return base[:FLIGHT_ID_MAX_LEN]


class RecordingController:
    """Decide start/stop from commands, link, disk, and capture events.

    All methods take ``now`` (UNIX seconds) from the caller, so tests
    control time.
    """

    def __init__(self, config: ControlConfig, now: float) -> None:
        self._cfg = config
        self.state = State.IDLE
        self.link_state = LinkState.NEVER
        self.boot_time = now
        self.last_link_seen: float | None = None
        self.link_lost_at: float | None = None
        self.fallback_armed = config.fallback_mode != "none"
        self.disk_low = False
        self.fault: str | None = None
        self.flight_id: str | None = None
        self.recording_since: float | None = None
        self.last_cmd_seq: int | None = None
        self._last_cmd: Command | None = None
        self._last_result: CommandResult | None = None

    # ----- link -----------------------------------------------------------

    def on_frame_received(self, now: float) -> list[Action]:
        """Record that a valid ground frame arrived."""
        self.last_link_seen = now
        actions: list[Action] = []
        if self.link_state != LinkState.UP:
            actions.append(Action(ActionKind.ALERT, reason=f"link {self.link_state} -> UP"))
            self.link_state = LinkState.UP
            self.link_lost_at = None
            if self._cfg.rearm_fallback_on_link_loss and self._cfg.fallback_mode != "none":
                self.fallback_armed = True
        return actions

    # ----- commands ---------------------------------------------------------

    def handle_command(self, cmd: Command, now: float) -> CommandResult:
        """Apply one ground command and return its result.

        A retry after a lost ack repeats the same ``seq``, name, and
        args. It returns the stored result without acting again, so a
        retried START cannot restart a recording. A different command
        with the same ``seq`` (a new ground session that restarted its
        counter) is a new command.
        """
        if (
            self._last_result is not None
            and self._last_cmd is not None
            and cmd.seq == self._last_cmd.seq
            and cmd.name == self._last_cmd.name
            and cmd.args == self._last_cmd.args
        ):
            return CommandResult(self._last_result.ok, self._last_result.message, [])
        self.last_cmd_seq = cmd.seq
        self._last_cmd = cmd
        if cmd.name == CommandName.START:
            result = self._start(now, cmd.args.get("flight_id"), "ground START")
        elif cmd.name == CommandName.STOP:
            result = self._stop(now, "ground STOP")
        elif cmd.name in (CommandName.STATUS, CommandName.PING):
            result = CommandResult(True, f"state {self.state}")
        else:  # pragma: no cover - pydantic blocks unknown names.
            result = CommandResult(False, f"unknown command {cmd.name}")
        self._last_result = result
        return result

    def _start(self, now: float, requested_id: str | None, reason: str) -> CommandResult:
        if self.state == State.RECORDING:
            return CommandResult(True, f"already recording {self.flight_id}")
        if self.state == State.FAULT:
            return CommandResult(False, f"in FAULT ({self.fault}); send STOP to clear")
        if self.disk_low:
            return CommandResult(False, "disk free space below threshold")
        self.flight_id = make_flight_id(now, self._cfg.flight_id_prefix, requested_id)
        self.state = State.RECORDING
        self.recording_since = now
        self.fallback_armed = False  # A running recording satisfies the fallback.
        action = Action(ActionKind.START_CAPTURE, flight_id=self.flight_id, reason=reason)
        return CommandResult(True, f"starting {self.flight_id}", [action])

    def _stop(self, now: float, reason: str) -> CommandResult:
        if self.state == State.IDLE:
            return CommandResult(True, "already idle")
        was_fault = self.state == State.FAULT
        self.state = State.IDLE
        self.fault = None
        self.recording_since = None
        # A ground STOP cancels the fallback: the operator is in control.
        self.fallback_armed = False
        actions = [Action(ActionKind.STOP_CAPTURE, reason=reason)]
        msg = "fault cleared" if was_fault else f"stopping {self.flight_id}"
        return CommandResult(True, msg, actions)

    # ----- events -----------------------------------------------------------

    def on_disk_low(self, now: float, free_gb: float) -> list[Action]:
        """Stop recording before the disk fills (O11)."""
        actions: list[Action] = []
        if not self.disk_low:
            actions.append(Action(ActionKind.ALERT, reason=f"disk low: {free_gb:.1f} GB free"))
        self.disk_low = True
        if self.state == State.RECORDING:
            actions.extend(self._stop(now, "disk low").actions)
        return actions

    def on_disk_ok(self) -> None:
        """Clear the disk-low block when space returns (after offload)."""
        self.disk_low = False

    def on_capture_lost(self, now: float, reason: str) -> list[Action]:
        """Every recorder has exited while we wanted to record."""
        if self.state != State.RECORDING:
            return []
        self.state = State.FAULT
        self.fault = reason
        self.recording_since = None
        return [
            Action(ActionKind.STOP_CAPTURE, reason=reason),
            Action(ActionKind.ALERT, reason=f"capture lost: {reason}"),
        ]

    def tick(self, now: float) -> list[Action]:
        """Advance timers: link timeout and the fallback rule."""
        actions: list[Action] = []
        if (
            self.link_state == LinkState.UP
            and self.last_link_seen is not None
            and now - self.last_link_seen > self._cfg.link_lost_s
        ):
            self.link_state = LinkState.LOST
            self.link_lost_at = now
            actions.append(Action(ActionKind.ALERT, reason="link LOST (recording continues)"))

        if self.fallback_armed and self.state == State.IDLE and not self.disk_low:
            mode = self._cfg.fallback_mode
            grace = self._cfg.link_grace_s
            if mode == "record_at_boot":
                actions.extend(self._start(now, None, "fallback: record at boot").actions)
            elif mode == "record_if_no_link":
                if self.link_state == LinkState.NEVER and now - self.boot_time >= grace:
                    actions.extend(self._start(now, None, "fallback: no link after boot").actions)
                elif (
                    self.link_state == LinkState.LOST
                    and self._cfg.rearm_fallback_on_link_loss
                    and self.link_lost_at is not None
                    and now - self.link_lost_at >= grace
                ):
                    actions.extend(self._start(now, None, "fallback: link lost").actions)
        return actions

    def recording_seconds(self, now: float) -> float:
        """Seconds since the current recording started, or 0."""
        if self.recording_since is None:
            return 0.0
        return max(0.0, now - self.recording_since)
