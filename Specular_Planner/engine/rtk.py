"""RTK base station: read RTCM3 corrections from a GNSS base on this computer and send them
to the aircraft over the existing MAVLink link (GPS_RTCM_DATA), like Mission Planner's
"RTK/GPS Inject".

Path:  base receiver --USB--> this laptop --telemetry radio (MAVLink)--> Cube --DroneCAN--> Here4

ArduPilot reassembles GPS_RTCM_DATA fragments and forwards the bytes to every GPS selected by
GPS_INJECT_TO (127 = all). Nothing here writes aircraft parameters.

For u-blox F9P-class bases (Here4 base, ArduSimple simpleRTK2B, etc.) the service can also put
the base into survey-in or fixed-position mode and turn on the RTCM messages a rover needs.
Settings go to RAM + battery-backed RAM only, never the receiver's flash.
"""
from __future__ import annotations

import json
import math
import struct
import threading
import time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------- RTCM3 framing

def _crc24q_table():
    table = []
    for i in range(256):
        crc = i << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
        table.append(crc & 0xFFFFFF)
    return table


_CRC24Q = _crc24q_table()


def crc24q(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = ((crc << 8) & 0xFFFFFF) ^ _CRC24Q[(crc >> 16) ^ b]
    return crc


def ubx_checksum(body: bytes) -> bytes:
    a = b = 0
    for x in body:
        a = (a + x) & 0xFF
        b = (b + a) & 0xFF
    return bytes((a, b))


def ubx(cls: int, msg_id: int, payload: bytes = b"") -> bytes:
    body = bytes((cls, msg_id)) + struct.pack("<H", len(payload)) + payload
    return b"\xb5\x62" + body + ubx_checksum(body)


class StreamParser:
    """Splits a mixed byte stream into RTCM3 frames and UBX messages. NMEA and noise are skipped."""

    def __init__(self):
        self.buf = bytearray()
        self.bad_crc = 0

    def feed(self, data: bytes):
        self.buf += data
        out = []
        buf = self.buf
        i = 0
        while i < len(buf):
            b0 = buf[i]
            if b0 == 0xD3:
                if len(buf) - i < 3:
                    break
                length = ((buf[i + 1] & 0x03) << 8) | buf[i + 2]
                if buf[i + 1] & 0xFC:          # 6 reserved bits must be zero
                    i += 1
                    continue
                end = i + 3 + length + 3
                if end > len(buf):
                    break
                frame = bytes(buf[i:end])
                if crc24q(frame[:-3]) == int.from_bytes(frame[-3:], "big"):
                    msg_type = (frame[3] << 4) | (frame[4] >> 4) if length >= 2 else 0
                    out.append(("rtcm", msg_type, frame))
                    i = end
                else:
                    self.bad_crc += 1
                    i += 1
            elif b0 == 0xB5:
                if len(buf) - i < 2:
                    break
                if buf[i + 1] != 0x62:
                    i += 1
                    continue
                if len(buf) - i < 6:
                    break
                length = buf[i + 4] | (buf[i + 5] << 8)
                if length > 4096:
                    i += 1
                    continue
                end = i + 6 + length + 2
                if end > len(buf):
                    break
                body = bytes(buf[i + 2:end - 2])
                if ubx_checksum(body) == bytes(buf[end - 2:end]):
                    out.append(("ubx", (body[0], body[1]), body[4:]))
                    i = end
                else:
                    i += 1
            else:
                i += 1
        del buf[:i]
        if len(buf) > 8192:        # never let junk grow without bound
            del buf[:-2048]
        return out


def _bits(data: bytes, start: int, n: int, signed=False) -> int:
    v = 0
    for k in range(start, start + n):
        v = (v << 1) | ((data[k >> 3] >> (7 - (k & 7))) & 1)
    if signed and v & (1 << (n - 1)):
        v -= 1 << n
    return v


def decode_station_ecef(frame: bytes):
    """ECEF (m) of the reference station from RTCM 1005/1006, else None."""
    payload = frame[3:-3]
    msg = _bits(payload, 0, 12)
    if msg not in (1005, 1006) or len(payload) < 19:
        return None
    x = _bits(payload, 34, 38, True) * 1e-4
    y = _bits(payload, 74, 38, True) * 1e-4
    z = _bits(payload, 114, 38, True) * 1e-4
    return x, y, z


def ecef_to_lla(x, y, z):
    a, f = 6378137.0, 1 / 298.257223563
    e2 = f * (2 - f)
    b = a * (1 - f)
    ep2 = (a * a - b * b) / (b * b)
    p = math.hypot(x, y)
    th = math.atan2(z * a, p * b)
    lat = math.atan2(z + ep2 * b * math.sin(th) ** 3, p - e2 * a * math.cos(th) ** 3)
    lon = math.atan2(y, x)
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    h = p / math.cos(lat) - n
    return math.degrees(lat), math.degrees(lon), h


def haversine_m(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


# ---------------------------------------------------------------- MAVLink GPS_RTCM_DATA

RTCM_CHUNK = 180          # GPS_RTCM_DATA.data length
MAX_FRAGMENTS = 4         # ArduPilot reassembles at most 4 fragments per message


def rtcm_messages(data: bytes, seq: int):
    """Split RTCM bytes into GPS_RTCM_DATA (flags, len, 180-byte data) tuples.

    flags bit 0 = fragmented, bits 1-2 = fragment id, bits 3-7 = sequence id.
    A message that fits in 180 bytes is sent unfragmented. A message that is an exact multiple
    of 180 bytes gets an empty closing fragment so ArduPilot knows it ended (ArduPilot treats the
    first short fragment as the last one). Anything longer than 719 bytes is sent as several sequences; ArduPilot passes the bytes straight through to the
    GPS, so frame boundaries do not have to line up with sequences.
    Returns (messages, next_seq).
    """
    out = []
    span = RTCM_CHUNK * MAX_FRAGMENTS - 1   # a full 720-byte set would never complete in ArduPilot
    for start in range(0, len(data), span):
        block = data[start:start + span]
        if len(block) <= RTCM_CHUNK and len(data) <= RTCM_CHUNK:
            out.append(((seq & 0x1F) << 3, len(block), block.ljust(RTCM_CHUNK, b"\0")))
        else:
            pieces = [block[k:k + RTCM_CHUNK] for k in range(0, len(block), RTCM_CHUNK)]
            if len(block) % RTCM_CHUNK == 0 and len(pieces) < MAX_FRAGMENTS:
                pieces.append(b"")
            for frag, piece in enumerate(pieces):
                flags = 1 | (frag << 1) | ((seq & 0x1F) << 3)
                out.append((flags, len(piece), piece.ljust(RTCM_CHUNK, b"\0")))
        seq = (seq + 1) & 0x1F
    return out, seq


# ---------------------------------------------------------------- u-blox gen-9 configuration

# Key IDs from the u-blox F9 interface description (checked against pyubx2's database in tests).
KEYS = {
    "TMODE_MODE": (0x20030001, "<B"),
    "TMODE_POS_TYPE": (0x20030002, "<B"),
    "TMODE_ECEF_X": (0x40030003, "<i"), "TMODE_ECEF_Y": (0x40030004, "<i"), "TMODE_ECEF_Z": (0x40030005, "<i"),
    "TMODE_ECEF_X_HP": (0x20030006, "<b"), "TMODE_ECEF_Y_HP": (0x20030007, "<b"), "TMODE_ECEF_Z_HP": (0x20030008, "<b"),
    "TMODE_FIXED_POS_ACC": (0x4003000F, "<I"),
    "TMODE_SVIN_MIN_DUR": (0x40030010, "<I"),
    "TMODE_SVIN_ACC_LIMIT": (0x40030011, "<I"),
    "USBOUTPROT_UBX": (0x10780001, "<B"), "USBOUTPROT_RTCM3X": (0x10780004, "<B"),
    "UART1OUTPROT_UBX": (0x10740001, "<B"), "UART1OUTPROT_RTCM3X": (0x10740004, "<B"),
    "MSGOUT_UBX_NAV_SVIN_USB": (0x2091008B, "<B"), "MSGOUT_UBX_NAV_SVIN_UART1": (0x20910089, "<B"),
}
# Station position + MSM4 for GPS, GLONASS, Galileo, BeiDou + GLONASS code-phase biases.
RTCM_OUT = {
    1005: (0x209102C0, 0x209102BE), 1074: (0x20910361, 0x2091035F), 1084: (0x20910366, 0x20910364),
    1094: (0x2091036B, 0x20910369), 1124: (0x20910370, 0x2091036E), 1230: (0x20910306, 0x20910304),
}
LAYER_RAM, LAYER_BBR = 1, 2


def valset(items, layers=LAYER_RAM | LAYER_BBR) -> bytes:
    payload = bytes((0, layers, 0, 0))
    for key, value in items:
        key_id, fmt = KEYS[key] if isinstance(key, str) else key
        payload += struct.pack("<I", key_id) + struct.pack(fmt, value)
    return ubx(0x06, 0x8A, payload)


def output_setup(rate_s=1):
    """Turn on UBX + RTCM3 output and the rover messages on USB and UART1."""
    items = [("USBOUTPROT_UBX", 1), ("USBOUTPROT_RTCM3X", 1), ("UART1OUTPROT_UBX", 1), ("UART1OUTPROT_RTCM3X", 1),
             ("MSGOUT_UBX_NAV_SVIN_USB", 1), ("MSGOUT_UBX_NAV_SVIN_UART1", 1)]
    for msg, (usb, uart) in RTCM_OUT.items():
        rate = 5 * rate_s if msg == 1005 else rate_s     # station position every 5 s is plenty
        items += [((usb, "<B"), rate), ((uart, "<B"), rate)]
    return valset(items)


def survey_setup(min_s: int, acc_m: float) -> bytes:
    return valset([("TMODE_MODE", 0)]) + valset([
        ("TMODE_MODE", 1), ("TMODE_SVIN_MIN_DUR", int(min_s)), ("TMODE_SVIN_ACC_LIMIT", int(round(acc_m * 1e4))),
    ])


def fixed_setup(ecef, acc_m: float) -> bytes:
    items = [("TMODE_MODE", 2), ("TMODE_POS_TYPE", 0), ("TMODE_FIXED_POS_ACC", int(round(max(acc_m, 0.0001) * 1e4)))]
    for axis, metres in zip("XYZ", ecef):
        tenth_mm = int(round(metres * 1e4))
        cm = int(tenth_mm / 100)               # truncate toward zero; HP part keeps the sign
        hp = tenth_mm - cm * 100
        items += [(f"TMODE_ECEF_{axis}", cm), (f"TMODE_ECEF_{axis}_HP", hp)]
    return valset([("TMODE_MODE", 0)]) + valset(items)


# ---- u-blox M8 (Here3, Here+ base, M8P boards): no CFG-VALSET, so use the older messages.

LEGACY_RTCM = {1005: 0x05, 1077: 0x4D, 1087: 0x57, 1127: 0x7F, 1230: 0xE6}   # MSM7, as u-blox recommends for M8P


def cfg_msg(cls: int, msg_id: int, rate: int) -> bytes:
    """UBX-CFG-MSG: same output rate on UART1 and USB (I2C, UART1, UART2, USB, SPI, reserved)."""
    return ubx(0x06, 0x01, bytes((cls, msg_id, 0, rate, 0, rate, 0, 0)))


def tmode3(mode: int, min_s=0, acc_m=0.0, ecef=None, fixed_acc_m=0.0) -> bytes:
    """UBX-CFG-TMODE3: 0 = off, 1 = survey-in, 2 = fixed ECEF position."""
    xyz, hp = (0, 0, 0), (0, 0, 0)
    if ecef is not None:
        parts = []
        for metres in ecef:
            tenth_mm = int(round(metres * 1e4))
            cm = int(tenth_mm / 100)
            parts.append((cm, tenth_mm - cm * 100))
        xyz, hp = tuple(c for c, _ in parts), tuple(h for _, h in parts)
    payload = struct.pack("<BBHiiibbbBIII8x", 0, 0, mode, *xyz, *hp, 0,
                          int(round(fixed_acc_m * 1e4)), int(min_s), int(round(acc_m * 1e4)))
    return ubx(0x06, 0x71, payload)


def legacy_setup(mode, min_s=120, acc_m=2.0, ecef=None, fixed_acc_m=0.02) -> bytes:
    out = b"".join(cfg_msg(0xF5, mid, 5 if msg == 1005 else 1) for msg, mid in LEGACY_RTCM.items())
    out += cfg_msg(0x01, 0x3B, 1)                         # NAV-SVIN, for the survey progress bars
    out += tmode3(0)
    if mode == "survey":
        out += tmode3(1, min_s=min_s, acc_m=acc_m)
    elif mode == "saved":
        out += tmode3(2, ecef=ecef, fixed_acc_m=fixed_acc_m)
    return out


CFG_IDS = (0x8A, 0x01, 0x71)       # VALSET, MSG, TMODE3: the settings HERON writes and waits to hear back on


def _count_cfg(blob: bytes) -> int:
    return sum(blob.count(bytes((0xB5, 0x62, 0x06, i))) for i in CFG_IDS)


def parse_nav_svin(p: bytes):
    if len(p) < 40:
        return None
    dur, mx, my, mz = struct.unpack_from("<Iiii", p, 8)
    hx, hy, hz = struct.unpack_from("<bbb", p, 24)
    acc, obs = struct.unpack_from("<II", p, 28)
    return {"duration_s": dur, "ecef": (mx * 0.01 + hx * 1e-4, my * 0.01 + hy * 1e-4, mz * 0.01 + hz * 1e-4),
            "acc_m": acc * 1e-4, "obs": obs, "valid": bool(p[36]), "active": bool(p[37])}


# ---------------------------------------------------------------- service

FIX_NAMES = {0: "No GPS", 1: "No fix", 2: "2D fix", 3: "3D fix", 4: "DGPS", 5: "RTK Float", 6: "RTK Fixed"}
SILENT_AFTER_S = 5.0
MODES = ("survey", "saved", "as_is")


class RtkError(RuntimeError):
    pass


class RtkService:
    def __init__(self, flight, store_path=None, opener=None, clock=time.monotonic):
        self.flight = flight
        self.store_path = Path(store_path) if store_path else None
        self.opener = opener
        self.clock = clock
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.port = None
        self.seq = 0
        self._reset()

    def _reset(self):
        self.running = False
        self.options = {}
        self.error = None
        self.started_at = None
        self.last_rx = None
        self.rx_bytes = 0
        self.window = deque()            # (time, bytes) of forwarded RTCM, for the rate
        self.rx_window = deque()         # (time, bytes) of RTCM read from the base
        self.counts = Counter()
        self.sent_frames = 0
        self.unsent_frames = 0
        self.station = None              # {"ecef", "lat", "lon", "h"}
        self.early = None                # survey stopped by hand: {"ecef", "acc_m", "duration_s"}
        self.svin = None
        self.acks = {"ack": 0, "nak": 0, "sent": 0}
        self.config_at = None
        self.receiver = None             # "gen9" (F9P, Here4) or "m8" (M8P, Here3) once the base answers
        self.legacy = b""                # M8 version of the setup, tried if the base refuses CFG-VALSET

    # ---- saved base positions
    def _load(self):
        if not self.store_path:
            return []
        try:
            return json.loads(self.store_path.read_text(encoding="utf-8")).get("positions", [])
        except (OSError, ValueError, AttributeError):
            return []

    def _store(self, positions):
        if self.store_path:
            self.store_path.write_text(json.dumps({"positions": positions}, indent=2), encoding="utf-8")

    def saved(self):
        return self._load()

    def save_position(self, name):
        name = str(name or "").strip()[:40]
        if not name:
            raise RtkError("Give the spot a name, e.g. 'Reservoir north lot'.")
        with self.lock:
            if self.early:
                ecef, acc, source = self.early["ecef"], self.early["acc_m"], "survey-in (stopped early)"
            elif self.svin and self.svin["valid"]:
                ecef, acc, source = self.svin["ecef"], self.svin["acc_m"], "survey-in"
            elif self.station:
                ecef, acc, source = self.station["ecef"], None, "base report"
            else:
                raise RtkError("No base position yet. Let survey-in finish first.")
        lat, lon, h = ecef_to_lla(*ecef)
        positions = [p for p in self._load() if p.get("name") != name]
        positions.insert(0, {"name": name, "ecef": [round(v, 4) for v in ecef], "acc_m": acc,
                             "lat": round(lat, 8), "lon": round(lon, 8), "h_m": round(h, 3), "source": source,
                             "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        self._store(positions[:20])
        return self.snapshot()

    def finish_survey(self):
        """Stop a survey-in now and send corrections from the position averaged so far.

        Only the absolute position of the whole flight depends on this; the drone's position
        relative to the base is RTK-accurate either way.
        """
        with self.lock:
            sv = self.svin
            if not (self.running and self.port is not None and sv and sv["active"] and sv["duration_s"] >= 10):
                raise RtkError("There is no survey in progress to finish yet.")
            ecef, acc = sv["ecef"], max(sv["acc_m"], 0.01)
            blob = (legacy_setup("saved", ecef=ecef, fixed_acc_m=acc) if self.receiver == "m8"
                    else fixed_setup(ecef, acc))
            try:
                self.port.write(blob)
            except Exception as exc:
                raise RtkError(f"Could not reach the base: {exc}") from exc
            self.early = {"ecef": ecef, "acc_m": acc, "duration_s": sv["duration_s"]}
            self.svin = dict(sv, active=False, valid=True)
            self.acks = {"ack": 0, "nak": 0, "sent": _count_cfg(blob)}
            self.config_at = self.clock()
            self.legacy = b""
        return self.snapshot()

    def forget_position(self, name):
        self._store([p for p in self._load() if p.get("name") != name])
        return self.snapshot()

    # ---- start / stop
    def start(self, options):
        port = str(options.get("port") or "")
        if not port:
            raise RtkError("Pick the base station's serial port.")
        baud = int(options.get("baud") or 115200)
        if baud not in {9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600}:
            raise RtkError("Unsupported baud rate.")
        mode = options.get("mode", "survey")
        if mode not in MODES:
            raise RtkError("Choose survey-in, a saved spot, or leave the base as it is.")
        setup = legacy = b""
        if mode == "survey":
            min_s = int(options.get("min_s", 120))
            acc_m = float(options.get("acc_m", 2.0))
            if not (30 <= min_s <= 86400 and 0.01 <= acc_m <= 50):
                raise RtkError("Survey-in needs 30 s–24 h and 0.01–50 m.")
            setup = output_setup() + survey_setup(min_s, acc_m)
            legacy = legacy_setup("survey", min_s=min_s, acc_m=acc_m)
        elif mode == "saved":
            spot = next((p for p in self._load() if p.get("name") == options.get("spot")), None)
            if not spot:
                raise RtkError("That saved spot no longer exists.")
            setup = output_setup() + fixed_setup(spot["ecef"], spot.get("acc_m") or 0.02)
            legacy = legacy_setup("saved", ecef=spot["ecef"], fixed_acc_m=spot.get("acc_m") or 0.02)
        link = getattr(self.flight, "link", None)
        if link is not None and getattr(link, "s", {}).get("transport") == "serial" and \
                getattr(getattr(link, "conn", None), "device", None) == port:
            raise RtkError("That port is the aircraft link. Pick the base station's port.")
        self.stop()
        reserved = getattr(self.flight, "reserved_ports", None)
        if reserved is not None:
            reserved.add(port)
        try:
            opener = self.opener
            if opener is None:
                import serial
                opener = lambda p, b: serial.Serial(p, b, timeout=0.2)
            handle = opener(port, baud)
        except Exception as exc:
            if reserved is not None:
                reserved.discard(port)
            raise RtkError(f"Could not open {port}: {exc}") from exc
        with self.lock:
            self._reset()
            self.port = handle
            self.running = True
            self.options = {"port": port, "baud": baud, "mode": mode, "spot": options.get("spot"),
                            "min_s": options.get("min_s", 120), "acc_m": options.get("acc_m", 2.0)}
            self.started_at = self.clock()
            self.legacy = legacy
            self.stop_event.clear()
        if setup:
            try:
                handle.write(setup)
                with self.lock:
                    self.acks["sent"] = _count_cfg(setup)
                    self.config_at = self.clock()
            except Exception as exc:
                self.stop()
                raise RtkError(f"Could not configure the base: {exc}") from exc
        self.thread = threading.Thread(target=self._loop, args=(handle,), daemon=True, name="rtk-base")
        self.thread.start()
        return self.snapshot()

    def stop(self):
        self.stop_event.set()
        thread, self.thread = self.thread, None
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)
        with self.lock:
            handle, self.port = self.port, None
            self.running = False
            port_name = self.options.get("port")
        reserved = getattr(self.flight, "reserved_ports", None)
        if reserved is not None and port_name:
            reserved.discard(port_name)
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        return self.snapshot()

    def close(self):
        self.stop()

    # ---- data path
    def _loop(self, handle):
        parser = StreamParser()
        while not self.stop_event.is_set():
            try:
                data = handle.read(4096)
            except Exception as exc:
                with self.lock:
                    self.error = f"Base station disconnected: {exc}"
                    self.running = False
                break
            if data:
                for item in parser.feed(data):
                    self.handle(item)

    def handle(self, item):
        kind = item[0]
        now = self.clock()
        if kind == "rtcm":
            _, msg_type, frame = item
            with self.lock:
                self.last_rx = now
                self.rx_bytes += len(frame)
                self.counts[msg_type] += 1
                self.rx_window.append((now, len(frame)))
                while self.rx_window and now - self.rx_window[0][0] > 10:
                    self.rx_window.popleft()
                if msg_type in (1005, 1006):
                    ecef = decode_station_ecef(frame)
                    if ecef:
                        lat, lon, h = ecef_to_lla(*ecef)
                        self.station = {"ecef": ecef, "lat": lat, "lon": lon, "h": h}
            self._forward(frame, now)
        elif kind == "ubx":
            _, (cls, mid), payload = item
            with self.lock:
                if (cls, mid) == (0x01, 0x3B):
                    sv = parse_nav_svin(payload)
                    if self.early and sv and not sv["active"]:
                        sv = dict(sv, ecef=self.early["ecef"], acc_m=self.early["acc_m"],
                                  duration_s=self.early["duration_s"], valid=True)
                    self.svin = sv
                elif cls == 0x05 and len(payload) >= 2 and payload[0] == 0x06 and payload[1] in CFG_IDS:
                    if mid == 0x01:
                        self.acks["ack"] += 1
                        self.receiver = self.receiver or ("gen9" if payload[1] == 0x8A else "m8")
                    elif payload[1] == 0x8A and self.legacy and self.port is not None:
                        # An M8 receiver (Here3 generation) doesn't know CFG-VALSET. Use its older messages.
                        blob, self.legacy = self.legacy, b""
                        self.receiver = "m8"
                        self.acks = {"ack": 0, "nak": 0, "sent": _count_cfg(blob)}
                        self.config_at = now
                        try:
                            self.port.write(blob)
                        except Exception as exc:
                            self.error = f"Could not configure the base: {exc}"
                    elif not (payload[1] == 0x8A and self.receiver == "m8"):
                        self.acks["nak"] += 1

    def _forward(self, frame, now):
        link = getattr(self.flight, "link", None)
        send = getattr(link, "send_rtcm", None)
        if send is None:
            with self.lock:
                self.unsent_frames += 1
            return
        with self.lock:
            messages, self.seq = rtcm_messages(frame, self.seq)
        try:
            for flags, length, data in messages:
                send(flags, length, data)
        except Exception:
            with self.lock:
                self.unsent_frames += 1
            return
        with self.lock:
            self.sent_frames += 1
            self.window.append((now, len(frame)))
            while self.window and now - self.window[0][0] > 10:
                self.window.popleft()

    # ---- status
    def snapshot(self):
        now = self.clock()
        tel = {}
        link = getattr(self.flight, "link", None)
        if link is not None:
            try:
                tel = link.snapshot()
            except Exception:
                tel = {}
        connected = bool(tel.get("connected"))
        with self.lock:
            age = None if self.last_rx is None else now - self.last_rx
            span = max(1.0, min(10.0, now - self.window[0][0])) if self.window else 1.0
            rate = sum(n for _, n in self.window) / span if self.window else 0.0
            fresh_rx = [(t, n) for t, n in self.rx_window if now - t <= 10]
            rx_span = max(1.0, min(10.0, now - fresh_rx[0][0])) if fresh_rx else 1.0
            rx_rate = sum(n for _, n in fresh_rx) / rx_span if fresh_rx else 0.0
            fresh_tx = [(t, n) for t, n in self.window if now - t <= 10]
            if not fresh_tx:
                rate = 0.0
            if not self.running:
                base_state = "error" if self.error else "off"
            elif self.svin and self.svin["active"]:
                base_state = "surveying"
            elif age is None or age > SILENT_AFTER_S:
                base_state = "silent"
            else:
                base_state = "sending" if self.sent_frames and rate > 0 else "receiving"
            config = None
            if self.acks["sent"]:
                if self.acks["nak"]:
                    config = "rejected"
                elif self.acks["ack"] >= self.acks["sent"]:
                    config = "accepted"
                elif self.config_at is not None and now - self.config_at > 3:
                    config = "no_reply"
                else:
                    config = "pending"
            fix = tel.get("gps_fix") if connected and tel.get("fresh", {}).get("gps") else None
            aircraft = {"connected": connected, "fix": fix, "fix_name": FIX_NAMES.get(fix, "—") if fix is not None else "—",
                        "satellites": tel.get("satellites") if fix is not None else None,
                        "h_acc_m": tel.get("gps_h_acc") if fix is not None else None,
                        "v_acc_m": tel.get("gps_v_acc") if fix is not None else None,
                        "transport": tel.get("transport"), "baseline_m": None}
            base_pos = None
            if self.svin and (self.svin["valid"] or self.svin["active"]) and self.svin["duration_s"]:
                lat, lon, h = ecef_to_lla(*self.svin["ecef"])
                base_pos = {"lat": lat, "lon": lon, "h": h, "source": "survey-in"}
            elif self.station:
                base_pos = {k: self.station[k] for k in ("lat", "lon", "h")} | {"source": "base report"}
            pos = tel.get("position")
            if base_pos and pos and pos.get("lat") is not None:
                aircraft["baseline_m"] = round(haversine_m((base_pos["lat"], base_pos["lon"]), (pos["lat"], pos["lon"])), 1)
            survey = None
            if self.svin:
                survey = {k: self.svin[k] for k in ("duration_s", "obs", "valid", "active")}
                survey["acc_m"] = round(self.svin["acc_m"], 3)
            return {
                "running": self.running, "base_state": base_state, "error": self.error, "options": dict(self.options),
                "config": config, "receiver": self.receiver, "survey": survey, "base_position": base_pos,
                "rx_age_s": None if age is None else round(age, 1),
                "rate_bps": round(rate), "rx_rate_bps": round(rx_rate), "messages": {str(k): v for k, v in sorted(self.counts.items())},
                "sent_frames": self.sent_frames, "unsent_frames": self.unsent_frames,
                "aircraft": aircraft, "saved": self._load(),
                "can_save": bool((self.svin and self.svin["valid"]) or self.station),
                "finished_early": self.early is not None,
                "can_finish": bool(self.running and self.svin and self.svin["active"] and self.svin["duration_s"] >= 10),
            }


# ---------------------------------------------------------------- HTTP

def rtk_api(service, prefix="/api/rtk"):
    import secrets
    from urllib.parse import urlsplit

    from flask import Blueprint, jsonify, request

    bp = Blueprint("rtk", __name__, url_prefix=prefix)
    token = secrets.token_urlsafe(32)

    @bp.before_request
    def local_only():
        if request.remote_addr not in {"127.0.0.1", "::1"} or urlsplit(request.host_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
            return jsonify(error="RTK control is only available on this computer."), 403
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if origin and origin != request.host_url.rstrip("/"):
                return jsonify(error="Cross-origin commands are blocked."), 403
            if not secrets.compare_digest(request.headers.get("X-RTK-Token", ""), token):
                return jsonify(error="Reload the planner before changing the base station."), 403
            if not request.is_json:
                return jsonify(error="A JSON request is required."), 400

    @bp.after_request
    def no_cache(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @bp.errorhandler(RtkError)
    @bp.errorhandler(ValueError)
    def rtk_error(exc):
        return jsonify(error=str(exc)), 409

    @bp.get("/session")
    def session():
        return jsonify(token=token)

    @bp.get("/status")
    def status():
        return jsonify(service.snapshot())

    @bp.get("/ports")
    def ports():
        try:
            from engine.flight import serial_port_records
            return jsonify(ports=serial_port_records())
        except ImportError:
            return jsonify(ports=[], error="Install requirements.txt to use serial ports.")

    @bp.post("/start")
    def start():
        return jsonify(service.start(request.get_json() or {}))

    @bp.post("/stop")
    def stop():
        return jsonify(service.stop())

    @bp.post("/save")
    def save():
        return jsonify(service.save_position((request.get_json() or {}).get("name")))

    @bp.post("/finish")
    def finish():
        return jsonify(service.finish_survey())

    @bp.post("/forget")
    def forget():
        return jsonify(service.forget_position((request.get_json() or {}).get("name")))

    return bp
