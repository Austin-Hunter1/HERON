"""Tests for the loopback transport, the factory, and MAVLink chunking."""

import pytest
from heron_common.protocol import LinkConfig, LoopbackTransport, make_transport
from heron_common.protocol.framing import encode_frame
from heron_common.protocol.mavlink_transport import (
    TUNNEL_PAYLOAD_BYTES,
    ChunkReassembler,
    split_frame,
)


def test_loopback_pair_moves_bytes_both_ways():
    """Bytes sent on one end arrive on the other and nowhere else."""
    a, b = LoopbackTransport.pair()
    a.send(b"to-b")
    b.send(b"to-a")
    assert b.recv() == b"to-b"
    assert a.recv() == b"to-a"
    assert a.recv() == b""


def test_loopback_disconnect_drops_frames():
    """With connected=False the link is broken in both directions."""
    a, b = LoopbackTransport.pair()
    a.connected = False
    a.send(b"x")
    b.send(b"y")
    assert b.recv() == b"" and a.recv() == b""
    assert a.dropped_frames == 1 and b.dropped_frames == 1


def test_factory_default_is_loopback():
    """The default config builds a loopback transport, so tests need no hardware."""
    assert isinstance(make_transport(LinkConfig()), LoopbackTransport)


def test_split_and_reassemble_multi_chunk():
    """A frame larger than one chunk splits and rebuilds byte-exact."""
    frame = encode_frame(b"x" * 500)
    chunks = split_frame(frame, frame_id=9, chunk_bytes=125)
    assert len(chunks) == 5
    assert all(len(c) <= TUNNEL_PAYLOAD_BYTES for c in chunks)
    reasm = ChunkReassembler(timeout_s=5.0)
    result = None
    for c in chunks:
        result = reasm.feed(c, now=0.0)
    assert result == frame


def test_single_chunk_passes_straight_through():
    """A small frame is one chunk and needs no reassembly state."""
    chunks = split_frame(b"abc", frame_id=1, chunk_bytes=125)
    assert len(chunks) == 1
    assert ChunkReassembler(5.0).feed(chunks[0], now=0.0) == b"abc"


def test_reassembler_out_of_order_and_interleaved():
    """Chunks of two frames may interleave and arrive out of order."""
    f1 = split_frame(b"1" * 300, frame_id=1, chunk_bytes=125)
    f2 = split_frame(b"2" * 300, frame_id=2, chunk_bytes=125)
    reasm = ChunkReassembler(5.0)
    assert reasm.feed(f1[2], now=0.0) is None
    assert reasm.feed(f2[0], now=0.0) is None
    assert reasm.feed(f1[0], now=0.0) is None
    assert reasm.feed(f2[2], now=0.0) is None
    assert reasm.feed(f1[1], now=0.0) == b"1" * 300
    assert reasm.feed(f2[1], now=0.0) == b"2" * 300


def test_reassembler_times_out_partial_frames():
    """A frame with a missing chunk is dropped after the timeout and counted."""
    chunks = split_frame(b"z" * 300, frame_id=5, chunk_bytes=125)
    reasm = ChunkReassembler(timeout_s=1.0)
    reasm.feed(chunks[0], now=0.0)
    reasm.feed(split_frame(b"q", frame_id=6, chunk_bytes=125)[0], now=2.0)
    assert reasm.dropped_frames == 1
    # The late chunks start a fresh partial frame, they do not complete the old one.
    assert reasm.feed(chunks[1], now=2.0) is None
    assert reasm.feed(chunks[2], now=2.0) is None


def test_reassembler_ignores_malformed_chunks():
    """Short or inconsistent headers are ignored, not raised."""
    reasm = ChunkReassembler(5.0)
    assert reasm.feed(b"\x01", now=0.0) is None
    assert reasm.feed(bytes((1, 5, 2)) + b"x", now=0.0) is None  # index >= count


def test_split_rejects_oversize():
    """A frame that needs more than 255 chunks is refused."""
    with pytest.raises(ValueError):
        split_frame(b"x" * (125 * 256), frame_id=1, chunk_bytes=125)
