"""Frame format for the payload link.

One frame is::

    <payload>*<CRC>\\n

- ``payload`` is UTF-8 text with no newline (JSON from ``messages``).
- ``*`` separates the payload from the checksum, as in NMEA sentences.
- ``CRC`` is CRC-16/CCITT-FALSE of the payload bytes, as four upper
  case hex digits.
- ``\\n`` ends the frame.

Why this format: a radio link can drop or corrupt bytes. The newline
lets the receiver find the next frame after a bad one. The CRC lets
it reject a frame with a bit error instead of acting on it. The text
form makes a serial dump readable on the bench.
"""

from __future__ import annotations

FRAME_END = b"\n"
CRC_SEPARATOR = b"*"
MAX_FRAME_BYTES = 8192  # Larger than any message we send. Protects memory.


def crc16(data: bytes) -> int:
    """Return CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) of ``data``."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def encode_frame(payload: bytes) -> bytes:
    """Wrap ``payload`` in a frame with its CRC and the end marker.

    Raise ``ValueError`` when the payload contains a newline, because
    that would split the frame on the wire.
    """
    if FRAME_END in payload:
        raise ValueError("Frame payload must not contain a newline")
    return payload + CRC_SEPARATOR + f"{crc16(payload):04X}".encode("ascii") + FRAME_END


class FrameParser:
    """Split a byte stream into verified frame payloads.

    Feed it bytes as they arrive, in any chunk size. It returns complete
    payloads with a good CRC. It counts and discards bad frames instead
    of raising, because one bad frame must not stop the link.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.bad_frames = 0
        self.good_frames = 0

    def feed(self, data: bytes) -> list[bytes]:
        """Add ``data`` to the buffer and return all complete payloads."""
        self._buffer.extend(data)
        payloads: list[bytes] = []
        while True:
            end = self._buffer.find(FRAME_END)
            if end < 0:
                break
            line = bytes(self._buffer[:end])
            del self._buffer[: end + 1]
            payload = self._check(line)
            if payload is not None:
                payloads.append(payload)
                self.good_frames += 1
            else:
                self.bad_frames += 1
        # Drop a runaway buffer that never sees a newline (noise on a
        # serial line). Keep the tail so a frame in progress survives.
        if len(self._buffer) > MAX_FRAME_BYTES:
            del self._buffer[:-MAX_FRAME_BYTES]
            self.bad_frames += 1
        return payloads

    @staticmethod
    def _check(line: bytes) -> bytes | None:
        """Return the payload when the CRC matches, else ``None``."""
        line = line.rstrip(b"\r")  # Tolerate CRLF from serial tools.
        if not line:
            return None
        sep = line.rfind(CRC_SEPARATOR)
        if sep < 0 or len(line) - sep - 1 != 4:
            return None
        payload = line[:sep]
        if not payload:
            return None  # An empty payload is never a message.
        try:
            expected = int(line[sep + 1 :], 16)
        except ValueError:
            return None
        if crc16(payload) != expected:
            return None
        return payload
