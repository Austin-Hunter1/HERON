"""End-to-end tests of the supervisor loop over a loopback link.

A fake clock drives ``step(now)``. The "ground" side is a plain
loopback transport plus the protocol functions, so these tests do not
depend on the base software.
"""

from pathlib import Path

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
from heron_onboard.capture import CaptureManager, FakeCaptureBackend
from heron_onboard.config import validate_onboard_dict
from heron_onboard.disk_monitor import GB, DiskMonitor
from heron_onboard.health import HealthMonitor
from heron_onboard.state_machine import State
from heron_onboard.supervisor import Supervisor
from heron_onboard.testing import minimal_config_dict


class Ground:
    """A minimal ground end for tests."""

    def __init__(self, transport: LoopbackTransport) -> None:
        self.t = transport
        self.parser = FrameParser()
        self.seq = 0
        self.telemetry: list[Telemetry] = []
        self.acks: list[Ack] = []

    def send(self, name: CommandName, now: float, **args) -> int:
        self.seq += 1
        self.t.send(encode_message(Command(seq=self.seq, name=name, args=args, sent_at=now)))
        return self.seq

    def drain(self) -> None:
        for payload in self.parser.feed(self.t.recv()):
            msg = decode_message(payload)
            if isinstance(msg, Telemetry):
                self.telemetry.append(msg)
            elif isinstance(msg, Ack):
                self.acks.append(msg)


def make(tmp_path: Path, free_gb: float = 500.0, **overrides):
    cfg = validate_onboard_dict(minimal_config_dict(tmp_path, **overrides))
    clock = {"now": 1000.0}
    backend = FakeCaptureBackend(cfg.sdr, clock=lambda: clock["now"])
    capture = CaptureManager(cfg, backend, "test")
    disk = DiskMonitor(
        cfg.disk.data_root,
        cfg.disk.min_free_gb,
        cfg.disk.min_free_pct,
        usage_fn=lambda p: (1000 * GB, int((1000 - free_gb) * GB), int(free_gb * GB)),
    )
    health = HealthMonitor([], start_time=1000.0)
    onboard_t, ground_t = LoopbackTransport.pair()
    sup = Supervisor(cfg, onboard_t, capture, disk, health, "test", clock=lambda: clock["now"])
    return sup, Ground(ground_t), backend, clock


def advance(sup, ground, clock, seconds: float, step: float = 0.1):
    end = clock["now"] + seconds
    while clock["now"] < end:
        clock["now"] = round(clock["now"] + step, 3)
        sup.step(clock["now"])
        ground.drain()


def test_start_stop_cycle_with_ack_and_telemetry(tmp_path):
    sup, ground, backend, clock = make(tmp_path)
    advance(sup, ground, clock, 2.0)
    assert ground.telemetry and ground.telemetry[-1].state == "IDLE"
    seq = ground.send(CommandName.START, clock["now"], flight_id="bench1")
    advance(sup, ground, clock, 1.0)
    assert (
        ground.acks[-1].seq == seq and ground.acks[-1].ok and ground.acks[-1].state == "RECORDING"
    )
    assert backend.start_calls[0][0].name == "bench1"
    assert ground.telemetry[-1].state == "RECORDING" and ground.telemetry[-1].flight_id == "bench1"
    assert all(s.streaming for s in ground.telemetry[-1].sdrs)
    assert ground.telemetry[-1].data_rate_mbps > 0
    ground.send(CommandName.STOP, clock["now"])
    advance(sup, ground, clock, 1.0)
    assert ground.acks[-1].ok and ground.telemetry[-1].state == "IDLE"
    assert backend.stop_calls == 1


def test_link_loss_keeps_recording(tmp_path):
    """D-011 through the whole loop: break the loopback, recording continues."""
    sup, ground, backend, clock = make(tmp_path, control={"link_lost_s": 5})
    advance(sup, ground, clock, 1.0)
    ground.send(CommandName.START, clock["now"])
    advance(sup, ground, clock, 1.0)
    ground.t.connected = False
    advance(sup, ground, clock, 30.0)
    assert sup.controller.link_state == "LOST"
    assert sup.controller.state == State.RECORDING and backend.running_count() == 2
    ground.t.connected = True
    advance(sup, ground, clock, 2.0)
    assert ground.telemetry[-1].state == "RECORDING"  # Telemetry resumes.


def test_fallback_starts_without_link(tmp_path):
    sup, ground, backend, clock = make(tmp_path, control={"link_grace_s": 20})
    ground.t.connected = False
    advance(sup, ground, clock, 19.0)
    assert sup.controller.state == State.IDLE
    advance(sup, ground, clock, 2.0)
    assert sup.controller.state == State.RECORDING and backend.start_calls


def test_status_command_returns_immediate_telemetry(tmp_path):
    sup, ground, backend, clock = make(tmp_path, telemetry={"rate_hz": 0.1})
    advance(sup, ground, clock, 0.5)
    count = len(ground.telemetry)
    ground.send(CommandName.STATUS, clock["now"])
    advance(sup, ground, clock, 0.2)
    assert len(ground.telemetry) == count + 1


def test_disk_low_stops_recording_and_alerts(tmp_path):
    sup, ground, backend, clock = make(tmp_path, free_gb=5.0, disk={"min_free_gb": 10})
    advance(sup, ground, clock, 0.5)
    ground.send(CommandName.START, clock["now"])
    advance(sup, ground, clock, 0.5)
    assert not ground.acks[-1].ok  # The disk check already ran once at boot.
    assert ground.telemetry[-1].disk_alert


def test_all_recorders_dead_gives_fault(tmp_path):
    sup, ground, backend, clock = make(tmp_path)
    ground.send(CommandName.START, clock["now"])
    advance(sup, ground, clock, 1.0)
    backend.fail("b210_1", "usb gone")
    advance(sup, ground, clock, 1.0)
    assert sup.controller.state == State.RECORDING  # One of two still runs (O15).
    assert any(s.fault == "usb gone" for s in ground.telemetry[-1].sdrs)
    backend.fail("b200mini_1", "usb gone too")
    advance(sup, ground, clock, 1.0)
    assert sup.controller.state == State.FAULT
    assert ground.telemetry[-1].fault and "usb gone" in ground.telemetry[-1].fault
    ground.send(CommandName.STOP, clock["now"])
    advance(sup, ground, clock, 1.0)
    assert sup.controller.state == State.IDLE


def test_bad_frames_are_counted_not_fatal(tmp_path):
    sup, ground, backend, clock = make(tmp_path)
    ground.t.send(b"garbage*ZZZZ\n")
    ground.t.send(b'{"kind":"cmd"}*0000\n')
    advance(sup, ground, clock, 1.5)
    assert ground.telemetry[-1].link_bad_frames >= 1
    assert sup.controller.state == State.IDLE
