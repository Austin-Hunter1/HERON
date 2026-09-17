"""Tests for the frame format and CRC."""

from heron_common.protocol.framing import FrameParser, crc16, encode_frame


def test_crc16_known_vector():
    """CRC-16/CCITT-FALSE of "123456789" is 0x29B1 (standard check value)."""
    assert crc16(b"123456789") == 0x29B1


def test_encode_frame_layout():
    """A frame is payload, '*', four hex digits, newline."""
    frame = encode_frame(b'{"a":1}')
    assert frame.endswith(b"\n")
    body, crc = frame[:-1].rsplit(b"*", 1)
    assert body == b'{"a":1}'
    assert len(crc) == 4
    assert int(crc, 16) == crc16(b'{"a":1}')


def test_encode_rejects_newline():
    """A payload with a newline would split the frame, so it is refused."""
    import pytest

    with pytest.raises(ValueError):
        encode_frame(b"a\nb")


def test_parser_round_trip_in_pieces():
    """The parser rebuilds frames from bytes fed in arbitrary chunks."""
    frames = [encode_frame(b"one"), encode_frame(b"two*with*stars"), encode_frame(b"")]
    stream = b"".join(frames)
    parser = FrameParser()
    got = []
    for i in range(0, len(stream), 3):
        got.extend(parser.feed(stream[i : i + 3]))
    assert got == [b"one", b"two*with*stars"]  # The empty payload is dropped.
    assert parser.bad_frames == 1
    assert parser.good_frames == 2


def test_parser_rejects_corruption_and_recovers():
    """A bit error in one frame is counted and the next frame still parses."""
    good = encode_frame(b"hello")
    bad = bytearray(encode_frame(b"hello"))
    bad[1] ^= 0x01
    parser = FrameParser()
    got = parser.feed(bytes(bad) + good)
    assert got == [b"hello"]
    assert parser.bad_frames == 1


def test_parser_tolerates_crlf_and_noise():
    """CRLF line ends parse. Noise glued to a frame costs that frame only."""
    parser = FrameParser()
    frame = encode_frame(b"x")
    assert parser.feed(frame[:-1] + b"\r\n") == [b"x"]
    assert parser.feed(b"\x00\xff garbage") == []
    # The noise has no newline, so it merges with the next frame: that
    # frame fails its CRC and is dropped. The one after it parses.
    assert parser.feed(frame) == []
    assert parser.bad_frames == 1
    assert parser.feed(frame) == [b"x"]


def test_parser_caps_runaway_buffer():
    """Noise with no newline is bounded so memory does not grow."""
    from heron_common.protocol.framing import MAX_FRAME_BYTES

    parser = FrameParser()
    parser.feed(b"a" * (MAX_FRAME_BYTES * 3))
    assert len(parser._buffer) <= MAX_FRAME_BYTES
    assert parser.bad_frames >= 1
