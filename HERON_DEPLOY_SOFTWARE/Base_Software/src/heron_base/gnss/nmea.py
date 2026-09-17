"""NMEA 0183 parsing: enough to show the fix state on the display.

We parse ``GGA`` only (fix quality, satellites, HDOP, position,
altitude). The raw stream is logged in full, so nothing is lost by
parsing little.
"""

from __future__ import annotations

from dataclasses import dataclass

GGA_QUALITY = {
    0: "NO FIX",
    1: "GPS",
    2: "DGPS",
    4: "RTK FIX",
    5: "RTK FLOAT",
    6: "DEAD RECK",
}


@dataclass(frozen=True)
class GgaFix:
    utc: str
    quality: int
    satellites: int
    hdop: float | None
    latitude: float | None
    longitude: float | None
    altitude_m: float | None

    @property
    def quality_text(self) -> str:
        return GGA_QUALITY.get(self.quality, f"Q{self.quality}")


def nmea_checksum_ok(sentence: str) -> bool:
    """Verify ``$...*hh``. Return False for any malformed sentence."""
    if not sentence.startswith("$") or "*" not in sentence:
        return False
    body, _, tail = sentence[1:].partition("*")
    if len(tail) < 2:
        return False
    try:
        expected = int(tail[:2], 16)
    except ValueError:
        return False
    actual = 0
    for ch in body:
        actual ^= ord(ch)
    return actual == expected


def _coord(value: str, hemisphere: str, degrees_digits: int) -> float | None:
    if not value:
        return None
    try:
        degrees = float(value[:degrees_digits])
        minutes = float(value[degrees_digits:])
    except ValueError:
        return None
    result = degrees + minutes / 60.0
    if hemisphere in ("S", "W"):
        result = -result
    return result


def parse_gga(sentence: str) -> GgaFix | None:
    """Parse a ``$GxGGA`` sentence. Return ``None`` if it is not one."""
    sentence = sentence.strip()
    if not nmea_checksum_ok(sentence):
        return None
    body = sentence[1:].split("*", 1)[0]
    fields = body.split(",")
    if len(fields) < 10 or not fields[0].endswith("GGA"):
        return None
    try:
        quality = int(fields[6] or 0)
        sats = int(fields[7] or 0)
        hdop = float(fields[8]) if fields[8] else None
        alt = float(fields[9]) if fields[9] else None
    except ValueError:
        return None
    return GgaFix(
        utc=fields[1],
        quality=quality,
        satellites=sats,
        hdop=hdop,
        latitude=_coord(fields[2], fields[3], 2),
        longitude=_coord(fields[4], fields[5], 3),
        altitude_m=alt,
    )


class NmeaExtractor:
    """Pull ``$...\\n`` sentences out of a mixed binary/text stream.

    u-blox receivers can send UBX, RTCM3, and NMEA on one port. Binary
    bytes can contain ``$``; the checksum check in ``parse_gga`` rejects
    the false starts.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[str]:
        self._buf.extend(data)
        sentences = []
        while True:
            start = self._buf.find(b"$")
            if start < 0:
                self._buf.clear()
                break
            end = self._buf.find(b"\n", start)
            if end < 0:
                del self._buf[:start]
                if len(self._buf) > 512:  # No NMEA here; drop noise.
                    self._buf.clear()
                break
            # A sentence never contains '$', so start at the last one before
            # the newline: binary bytes before it are not part of it.
            start = self._buf.rfind(b"$", start, end)
            raw = bytes(self._buf[start:end])
            del self._buf[: end + 1]
            try:
                sentences.append(raw.decode("ascii").strip())
            except UnicodeDecodeError:
                continue
        return sentences
