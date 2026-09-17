"""Tests for the recording controller (pure decision logic)."""

from heron_common.protocol import Command, CommandName
from heron_onboard.config import ControlConfig
from heron_onboard.state_machine import (
    ActionKind,
    LinkState,
    RecordingController,
    State,
    make_flight_id,
)


def cmd(seq: int, name: CommandName, **args) -> Command:
    return Command(seq=seq, name=name, args=args, sent_at=0.0)


def kinds(actions):
    return [a.kind for a in actions]


def test_start_stop_cycle_from_ground():
    """A ground START starts capture; a ground STOP stops it."""
    c = RecordingController(ControlConfig(), now=0.0)
    c.on_frame_received(0.0)
    r = c.handle_command(cmd(1, CommandName.START, flight_id="lake a"), 1.0)
    assert r.ok and kinds(r.actions) == [ActionKind.START_CAPTURE]
    assert c.state == State.RECORDING and c.flight_id == "lake_a"
    r = c.handle_command(cmd(2, CommandName.STOP), 2.0)
    assert r.ok and kinds(r.actions) == [ActionKind.STOP_CAPTURE]
    assert c.state == State.IDLE


def test_repeated_seq_is_idempotent():
    """A retried command (same seq) returns the same result and acts once."""
    c = RecordingController(ControlConfig(), now=0.0)
    first = c.handle_command(cmd(5, CommandName.START), 1.0)
    again = c.handle_command(cmd(5, CommandName.START), 1.5)
    assert first.ok and again.ok
    assert kinds(first.actions) == [ActionKind.START_CAPTURE]
    assert again.actions == []


def test_same_seq_different_command_is_new():
    """A new ground session can restart its seq counter without harm."""
    c = RecordingController(ControlConfig(), now=0.0)
    c.on_disk_low(0.0, 1.0)
    refused = c.handle_command(cmd(1, CommandName.START), 1.0)
    assert not refused.ok
    ping = c.handle_command(cmd(1, CommandName.PING), 2.0)
    assert ping.ok and "state" in ping.message
    # Same seq and name but different args is also a new command.
    c.on_disk_ok()
    started = c.handle_command(cmd(1, CommandName.START, flight_id="x"), 3.0)
    assert started.ok and kinds(started.actions) == [ActionKind.START_CAPTURE]


def test_start_while_recording_is_ok_no_action():
    c = RecordingController(ControlConfig(), now=0.0)
    c.handle_command(cmd(1, CommandName.START), 1.0)
    r = c.handle_command(cmd(2, CommandName.START), 2.0)
    assert r.ok and r.actions == [] and "already" in r.message


def test_link_loss_does_not_stop_recording():
    """D-011: the payload keeps recording when the link drops."""
    cfg = ControlConfig(link_lost_s=10)
    c = RecordingController(cfg, now=0.0)
    c.on_frame_received(0.0)
    c.handle_command(cmd(1, CommandName.START), 1.0)
    actions = c.tick(50.0)
    assert c.link_state == LinkState.LOST
    assert ActionKind.STOP_CAPTURE not in kinds(actions)
    assert c.state == State.RECORDING


def test_fallback_records_when_no_link_after_grace():
    """D-017: no link within the grace period -> start recording."""
    cfg = ControlConfig(fallback_mode="record_if_no_link", link_grace_s=120)
    c = RecordingController(cfg, now=0.0)
    assert c.tick(119.0) == []
    actions = c.tick(120.0)
    assert kinds(actions) == [ActionKind.START_CAPTURE]
    assert c.state == State.RECORDING
    assert c.tick(200.0) == []  # Fires once.


def test_fallback_cancelled_by_link():
    """A link seen inside the grace period cancels the boot fallback."""
    cfg = ControlConfig(fallback_mode="record_if_no_link", link_grace_s=120)
    c = RecordingController(cfg, now=0.0)
    c.on_frame_received(30.0)
    assert c.tick(500.0) == [] or ActionKind.START_CAPTURE not in kinds(c.tick(500.0))
    assert c.state == State.IDLE


def test_fallback_rearm_on_link_loss():
    """With rearm on, an IDLE payload that loses its link starts after the grace."""
    cfg = ControlConfig(
        fallback_mode="record_if_no_link",
        link_grace_s=60,
        link_lost_s=10,
        rearm_fallback_on_link_loss=True,
    )
    c = RecordingController(cfg, now=0.0)
    c.on_frame_received(5.0)
    c.tick(20.0)  # Link LOST at 20.
    assert c.link_state == LinkState.LOST
    assert ActionKind.START_CAPTURE not in kinds(c.tick(70.0))
    assert ActionKind.START_CAPTURE in kinds(c.tick(80.0))


def test_fallback_none_never_starts():
    c = RecordingController(ControlConfig(fallback_mode="none", link_grace_s=0), now=0.0)
    assert c.tick(1e6) == []
    assert c.state == State.IDLE


def test_fallback_record_at_boot():
    c = RecordingController(ControlConfig(fallback_mode="record_at_boot"), now=0.0)
    assert kinds(c.tick(0.1)) == [ActionKind.START_CAPTURE]


def test_ground_stop_disarms_fallback():
    """After a ground STOP the payload stays idle even with no link."""
    cfg = ControlConfig(fallback_mode="record_if_no_link", link_grace_s=10, link_lost_s=5)
    c = RecordingController(cfg, now=0.0)
    c.tick(10.0)  # Fallback start.
    c.on_frame_received(11.0)
    c.handle_command(cmd(1, CommandName.STOP), 11.0)
    c.tick(100.0)  # Link lost long ago.
    assert c.state == State.IDLE


def test_disk_low_stops_and_blocks_start():
    """O11: disk low stops recording and refuses a new start until space returns."""
    c = RecordingController(ControlConfig(), now=0.0)
    c.handle_command(cmd(1, CommandName.START), 1.0)
    actions = c.on_disk_low(2.0, free_gb=3.2)
    assert kinds(actions) == [ActionKind.ALERT, ActionKind.STOP_CAPTURE]
    assert c.state == State.IDLE
    r = c.handle_command(cmd(2, CommandName.START), 3.0)
    assert not r.ok and "disk" in r.message
    c.on_disk_ok()
    assert c.handle_command(cmd(3, CommandName.START), 4.0).ok


def test_disk_low_blocks_fallback():
    cfg = ControlConfig(fallback_mode="record_at_boot")
    c = RecordingController(cfg, now=0.0)
    c.on_disk_low(0.0, 1.0)
    assert ActionKind.START_CAPTURE not in kinds(c.tick(1.0))


def test_capture_lost_enters_fault_and_stop_clears():
    """O15: all recorders gone -> FAULT with alert; STOP clears it."""
    c = RecordingController(ControlConfig(), now=0.0)
    c.handle_command(cmd(1, CommandName.START), 1.0)
    actions = c.on_capture_lost(2.0, "all recorders exited")
    assert set(kinds(actions)) == {ActionKind.STOP_CAPTURE, ActionKind.ALERT}
    assert c.state == State.FAULT
    r = c.handle_command(cmd(2, CommandName.START), 3.0)
    assert not r.ok and "FAULT" in r.message
    r = c.handle_command(cmd(3, CommandName.STOP), 4.0)
    assert r.ok and c.state == State.IDLE and c.fault is None


def test_capture_lost_when_idle_is_ignored():
    c = RecordingController(ControlConfig(), now=0.0)
    assert c.on_capture_lost(1.0, "x") == []
    assert c.state == State.IDLE


def test_status_and_ping_have_no_side_effects():
    c = RecordingController(ControlConfig(), now=0.0)
    assert c.handle_command(cmd(1, CommandName.PING), 1.0).ok
    assert c.handle_command(cmd(2, CommandName.STATUS), 1.0).ok
    assert c.state == State.IDLE


def test_make_flight_id():
    """Flight ids are path safe and fall back to the UTC stamp."""
    assert make_flight_id(0.0) == "19700101T000000Z"
    assert make_flight_id(0.0, prefix="heron") == "heron_19700101T000000Z"
    assert make_flight_id(0.0, requested="Lake A / run 1") == "Lake_A_run_1"
    assert make_flight_id(0.0, requested="///") == "19700101T000000Z"
    assert len(make_flight_id(0.0, requested="x" * 200)) == 64
