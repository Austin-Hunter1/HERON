"""Tests for message encoding and decoding."""

import pytest
from heron_common.protocol import (
    Ack,
    Command,
    CommandName,
    FrameParser,
    SdrStatus,
    Telemetry,
    decode_message,
    encode_message,
)


def test_command_round_trip():
    """A command survives encode -> frame parse -> decode unchanged."""
    cmd = Command(seq=7, name=CommandName.START, args={"flight_id": "lake_a"}, sent_at=1.5)
    payloads = FrameParser().feed(encode_message(cmd))
    assert len(payloads) == 1
    back = decode_message(payloads[0])
    assert isinstance(back, Command)
    assert back == cmd


def test_telemetry_round_trip_and_defaults():
    """Telemetry with default fields omitted still decodes with defaults filled."""
    tlm = Telemetry(
        seq=1,
        sent_at=2.0,
        uptime_s=10.0,
        state="RECORDING",
        sdrs=[SdrStatus(id="b210_1", present=True, streaming=True, overflows=2)],
        disk_free_gb=100.0,
    )
    frame = encode_message(tlm)
    assert b"cpu_pct" not in frame  # Default fields are omitted to save bandwidth.
    back = decode_message(FrameParser().feed(frame)[0])
    assert isinstance(back, Telemetry)
    assert back.cpu_pct == 0.0
    assert back.sdrs[0].overflows == 2
    assert back.sdrs[0].ref_locked is None


def test_ack_round_trip():
    """An ack decodes to an Ack model."""
    ack = Ack(seq=3, ok=False, message="disk low", state="IDLE", sent_at=1.0)
    back = decode_message(FrameParser().feed(encode_message(ack))[0])
    assert isinstance(back, Ack)
    assert back.ok is False and back.message == "disk low"


def test_telemetry_size_budget():
    """A full four-SDR telemetry frame stays under 1 kB (link budget)."""
    tlm = Telemetry(
        seq=123456,
        sent_at=1_700_000_000.123,
        uptime_s=3600.5,
        state="RECORDING",
        flight_id="20260917T120000Z",
        recording_s=1234.5,
        sdrs=[
            SdrStatus(
                id=f"sdr{i}",
                present=True,
                streaming=True,
                ref_locked=True,
                overflows=i,
                host_drops=0,
                samples=10**9,
                rate_mbps=44.0,
                segment=40,
            )
            for i in range(4)
        ],
        data_rate_mbps=176.0,
        disk_free_gb=1500.25,
        disk_free_pct=75.5,
        cpu_pct=55.5,
        temps_c={"cpu": 65.0, "nvme": 40.0},
        last_cmd_seq=9,
        version="abc1234-dirty",
        link_good_frames=1000,
        link_bad_frames=3,
    )
    assert len(encode_message(tlm)) < 1024


@pytest.mark.parametrize(
    "payload",
    [b"not json", b"[1,2]", b'{"kind":"nope"}', b'{"kind":"cmd","seq":-1}'],
)
def test_decode_rejects_bad_payloads(payload):
    """Bad payloads raise ValueError with a message, never another type."""
    with pytest.raises(ValueError):
        decode_message(payload)
