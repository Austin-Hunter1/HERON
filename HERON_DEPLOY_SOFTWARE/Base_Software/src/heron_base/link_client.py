"""The ground end of the payload link.

``LinkClient`` receives telemetry and acks, sends commands, retries a
command that gets no ack, and reports the link health. The display is
a thin layer over this class (B6); a headless CLI uses it the same way.

All time comes from ``clock`` so tests can drive it.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

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

from heron_base.config import LinkHealthConfig
from heron_base.flight_log import FlightLog
from heron_base.link_health import LinkHealth, compute_link_health

log = logging.getLogger(__name__)

LINK_RETRY_S = 5.0


@dataclass
class PendingCommand:
    """One command in flight, until its ack arrives or retries run out."""

    command: Command
    sent_at: float
    attempts: int = 1
    done: bool = False
    ack: Ack | None = None

    @property
    def failed(self) -> bool:
        return self.done and self.ack is None

    def summary(self) -> str:
        if not self.done:
            return f"{self.command.name} seq={self.command.seq} waiting (try {self.attempts})"
        if self.ack is None:
            return f"{self.command.name} seq={self.command.seq} FAILED: no ack"
        status = "ok" if self.ack.ok else "REFUSED"
        return f"{self.command.name} seq={self.command.seq} {status}: {self.ack.message}"


class LinkClient:
    """Receive telemetry, send commands, track link health."""

    def __init__(
        self,
        transport: Transport,
        config: LinkHealthConfig,
        flight_log: FlightLog | None = None,
        clock: Callable[[], float] = time.time,
        event_lines: int = 200,
    ) -> None:
        self._transport = transport
        self._cfg = config
        self._log = flight_log
        self._clock = clock
        self._parser = FrameParser()
        # Seed the sequence from the clock so a new ground session (for
        # example each `heron-base send` call) does not reuse the seq
        # numbers of the last one. The payload also compares name and
        # args, so a collision is harmless.
        self._seq = int(clock()) & 0x3FFFFFFF
        self._last_link_try = 0.0
        self.latest: Telemetry | None = None
        self.last_rx: float | None = None
        self.pending: PendingCommand | None = None
        self.history: deque[PendingCommand] = deque(maxlen=50)
        self.events: deque[str] = deque(maxlen=event_lines)
        self.telemetry_count = 0
        self._last_health = LinkHealth.NO_DATA

    # ----- lifecycle -----------------------------------------------------------

    def open(self) -> None:
        try:
            self._transport.open()
            self.event(f"link open: {self._transport.describe()}")
        except TransportError as exc:
            self.event(f"link open failed: {exc}")

    def close(self) -> None:
        self._transport.close()
        if self._log:
            self._log.close()

    def event(self, text: str) -> None:
        """Record an operator-visible event (also logged)."""
        stamp = time.strftime("%H:%M:%S", time.gmtime(self._clock()))
        self.events.append(f"{stamp}Z {text}")
        log.info(text)
        if self._log:
            self._log.write("event", {"text": text}, self._clock())

    # ----- health --------------------------------------------------------------

    def telemetry_age(self, now: float | None = None) -> float | None:
        if self.last_rx is None:
            return None
        return max(0.0, (self._clock() if now is None else now) - self.last_rx)

    def health(self, now: float | None = None) -> LinkHealth:
        return compute_link_health(self.telemetry_age(now), self._cfg.degraded_s, self._cfg.lost_s)

    @property
    def bad_frames(self) -> int:
        return self._parser.bad_frames

    # ----- main poll -----------------------------------------------------------

    def poll(self) -> None:
        """Receive everything available, then service the pending command."""
        now = self._clock()
        if not self._transport.is_open and now - self._last_link_try >= LINK_RETRY_S:
            self._last_link_try = now
            self.open()
        data = self._transport.recv()
        for payload in self._parser.feed(data) if data else []:
            try:
                msg = decode_message(payload)
            except ValueError as exc:
                log.warning("bad frame: %s", exc)
                continue
            self.last_rx = now
            if isinstance(msg, Telemetry):
                self.latest = msg
                self.telemetry_count += 1
                if self._log:
                    self._log.write("tlm", msg.model_dump(mode="json"), now)
            elif isinstance(msg, Ack):
                self._handle_ack(msg, now)
        self._retry_pending(now)
        health = self.health(now)
        if health != self._last_health:
            self.event(f"link {self._last_health} -> {health}")
            self._last_health = health

    def _handle_ack(self, ack: Ack, now: float) -> None:
        if self._log:
            self._log.write("ack", ack.model_dump(mode="json"), now)
        pending = self.pending
        if pending is None or pending.command.seq != ack.seq:
            log.debug("ack for seq %d has no pending command", ack.seq)
            return
        pending.ack = ack
        pending.done = True
        self.event(pending.summary())
        self.history.append(pending)
        self.pending = None

    def _retry_pending(self, now: float) -> None:
        pending = self.pending
        if pending is None or pending.done:
            return
        if now - pending.sent_at < self._cfg.command_timeout_s:
            return
        if pending.attempts <= self._cfg.command_retries:
            pending.attempts += 1
            pending.sent_at = now
            self._transport.send(encode_message(pending.command))
            self.event(
                f"resend {pending.command.name} seq={pending.command.seq} (try {pending.attempts})"
            )
            return
        pending.done = True
        self.event(pending.summary())
        self.history.append(pending)
        self.pending = None

    # ----- commands ------------------------------------------------------------

    def send_command(self, name: CommandName, args: dict[str, str] | None = None) -> PendingCommand:
        """Send a command. A still-pending older command is abandoned."""
        now = self._clock()
        if self.pending is not None and not self.pending.done:
            self.pending.done = True
            self.event(f"abandoned {self.pending.command.name} seq={self.pending.command.seq}")
            self.history.append(self.pending)
        self._seq += 1
        cmd = Command(seq=self._seq, name=name, args=args or {}, sent_at=now)
        self.pending = PendingCommand(command=cmd, sent_at=now)
        self._transport.send(encode_message(cmd))
        self.event(f"sent {name} seq={cmd.seq} {args or ''}".rstrip())
        if self._log:
            self._log.write("cmd", cmd.model_dump(mode="json"), now)
        return self.pending

    def wait_for_ack(self, pending: PendingCommand, poll_s: float | None = None) -> Ack | None:
        """Block (polling) until the command is done. For the headless CLI."""
        poll_s = poll_s or self._cfg.poll_s
        while not pending.done:
            self.poll()
            time.sleep(poll_s)
        return pending.ack
