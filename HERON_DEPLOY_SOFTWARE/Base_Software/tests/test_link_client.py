"""LinkClient against a scripted payload on a loopback link."""

from pathlib import Path

from heron_base.config import LinkHealthConfig
from heron_base.flight_log import FlightLog, read_flight_log
from heron_base.link_client import LinkClient
from heron_base.link_health import LinkHealth
from heron_common.protocol import (
    Ack,
    Command,
    CommandName,
    FrameParser,
    LoopbackTransport,
    Telemetry,
    decode_message,
    encode_message,
)


class FakePayload:
    """Answers commands with acks and sends telemetry on demand."""

    def __init__(self, transport: LoopbackTransport, answer: bool = True) -> None:
        self.t = transport
        self.parser = FrameParser()
        self.answer = answer
        self.received: list[Command] = []
        self.seq = 0

    def service(self, now: float) -> None:
        for payload in self.parser.feed(self.t.recv()):
            msg = decode_message(payload)
            if isinstance(msg, Command):
                self.received.append(msg)
                if self.answer:
                    self.t.send(
                        encode_message(
                            Ack(seq=msg.seq, ok=True, message="fine", state="IDLE", sent_at=now)
                        )
                    )

    def telemetry(self, now: float, state: str = "IDLE") -> None:
        self.seq += 1
        self.t.send(encode_message(Telemetry(seq=self.seq, sent_at=now, uptime_s=1, state=state)))


def make(tmp_path: Path, **cfg):
    clock = {"now": 100.0}
    ground_t, payload_t = LoopbackTransport.pair()
    log = FlightLog(tmp_path, session_time=clock["now"])
    client = LinkClient(ground_t, LinkHealthConfig(**cfg), log, clock=lambda: clock["now"])
    return client, FakePayload(payload_t), clock, log


def test_health_follows_telemetry_age(tmp_path):
    client, payload, clock, _ = make(tmp_path, degraded_s=3, lost_s=10)
    client.poll()
    assert client.health() == LinkHealth.NO_DATA
    payload.telemetry(clock["now"])
    client.poll()
    assert client.health() == LinkHealth.CONNECTED and client.latest.seq == 1
    clock["now"] += 5
    assert client.health() == LinkHealth.DEGRADED
    clock["now"] += 6
    client.poll()
    assert client.health() == LinkHealth.LOST
    assert any("LOST" in e for e in client.events)


def test_command_ack_matching(tmp_path):
    client, payload, clock, _ = make(tmp_path)
    pending = client.send_command(CommandName.START, {"flight_id": "x"})
    payload.service(clock["now"])
    client.poll()
    assert pending.done and pending.ack.ok and pending.ack.message == "fine"
    assert payload.received[0].args == {"flight_id": "x"}
    assert client.pending is None and client.history[-1] is pending


def test_command_retry_then_fail(tmp_path):
    client, payload, clock, _ = make(tmp_path, command_timeout_s=2, command_retries=2)
    payload.answer = False
    pending = client.send_command(CommandName.STOP)
    for _ in range(4):
        clock["now"] += 2.5
        client.poll()
        payload.service(clock["now"])
    assert pending.done and pending.failed and pending.attempts == 3
    assert len(payload.received) == 3  # Same seq each time: idempotent on the payload.
    assert all(c.seq == pending.command.seq for c in payload.received)


def test_new_command_abandons_pending(tmp_path):
    client, payload, clock, _ = make(tmp_path)
    first = client.send_command(CommandName.PING)
    second = client.send_command(CommandName.STATUS)
    assert first.done and first.failed and not second.done
    assert second.command.seq == first.command.seq + 1


def test_flight_log_records_everything(tmp_path):
    client, payload, clock, log = make(tmp_path)
    payload.telemetry(clock["now"])
    client.poll()
    client.send_command(CommandName.PING)
    payload.service(clock["now"])
    client.poll()
    client.close()
    records = read_flight_log(log.path)
    kinds = [r["kind"] for r in records]
    assert "tlm" in kinds and "cmd" in kinds and "ack" in kinds and "event" in kinds
    assert all("t" in r for r in records)
