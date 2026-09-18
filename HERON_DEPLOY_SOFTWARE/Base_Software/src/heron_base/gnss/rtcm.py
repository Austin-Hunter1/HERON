"""RTCM3 frame splitting with CRC-24Q check.

An RTCM3 frame is: ``0xD3``, 6 reserved bits + 10-bit length, payload,
3-byte CRC-24Q over everything before the CRC. The splitter finds
whole, valid frames in a mixed byte stream and hands them to the
correction sink. Bytes that are not RTCM are skipped.
"""

from __future__ import annotations

RTCM_PREAMBLE = 0xD3
MAX_RTCM_PAYLOAD = 1023


def crc24q(data: bytes) -> int:
    """CRC-24Q (polynomial 0x1864CFB, init 0), as used by RTCM3."""
    crc = 0
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
        crc &= 0xFFFFFF
    return crc


def rtcm_message_type(frame: bytes) -> int | None:
    """Return the 12-bit message number of a frame, or None."""
    if len(frame) < 5:
        return None
    return (frame[3] << 4) | (frame[4] >> 4)


class Rtcm3Splitter:
    """Extract valid RTCM3 frames from a byte stream."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.frames = 0
        self.crc_errors = 0
        self.resyncs = 0  # False preambles skipped by the reserved-bits check.

    def feed(self, data: bytes) -> list[bytes]:
        self._buf.extend(data)
        out = []
        while True:
            start = self._buf.find(bytes([RTCM_PREAMBLE]))
            if start < 0:
                self._buf.clear()
                break
            if start:
                del self._buf[:start]
            if len(self._buf) < 6:
                break
            if self._buf[1] & 0xFC:
                # The 6 reserved bits after the preamble are always zero in
                # RTCM3. A non-zero value means this 0xD3 is not a frame start.
                self.resyncs += 1
                del self._buf[0]
                continue
            length = ((self._buf[1] & 0x03) << 8) | self._buf[2]
            total = 3 + length + 3
            if len(self._buf) < total:
                if length > MAX_RTCM_PAYLOAD:
                    del self._buf[0]
                    continue
                break
            frame = bytes(self._buf[:total])
            if crc24q(frame[:-3]) == int.from_bytes(frame[-3:], "big"):
                out.append(frame)
                self.frames += 1
                del self._buf[:total]
            else:
                # Not a real frame start (a 0xD3 inside other data). Skip one byte.
                self.crc_errors += 1
                del self._buf[0]
        return out
