"""RTK base-station forwarding: RTCM framing, GPS_RTCM_DATA fragmenting, u-blox base setup.

No hardware: the base is an in-memory serial port and the aircraft is a recording link.
pyubx2 / pyrtcm (optional) cross-check the byte formats against independent decoders.
"""
import os
os.environ["MAVLINK20"] = "1"
import json
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path

from engine import rtk
from engine.rtk import (RtkError, legacy_setup, tmode3, RtkService, StreamParser, crc24q, decode_station_ecef, ecef_to_lla,
                        fixed_setup, output_setup, parse_nav_svin, rtcm_messages, survey_setup, ubx)

try:
    import pyubx2
except ImportError:
    pyubx2 = None
try:
    import pyrtcm
except ImportError:
    pyrtcm = None

# Boulder Reservoir-ish station, ECEF metres
STATION = (-1271060.1234, -4723870.5678, 4098230.9012)


def put_bits(fields):
    bits = "".join(format(v & ((1 << n) - 1), f"0{n}b") for n, v in fields)
    bits += "0" * (-len(bits) % 8)
    return bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))


def frame(payload: bytes) -> bytes:
    head = bytes((0xD3, (len(payload) >> 8) & 0x03, len(payload) & 0xFF)) + payload
    return head + crc24q(head).to_bytes(3, "big")


def rtcm_1005(ecef=STATION, station_id=7):
    x, y, z = (int(round(v * 1e4)) for v in ecef)
    return frame(put_bits([(12, 1005), (12, station_id), (6, 0), (1, 1), (1, 1), (1, 1), (1, 0),
                           (38, x), (1, 0), (1, 0), (38, y), (2, 0), (38, z)]))


def rtcm_blob(msg_type, size):
    """A CRC-valid frame of a given message type and payload size (contents are filler)."""
    body = bytes(((msg_type >> 4) & 0xFF, (msg_type & 0x0F) << 4)) + bytes((i * 37) & 0xFF for i in range(size - 2))
    return frame(body)


def nav_svin(dur=95, acc_m=1.25, valid=0, active=1, ecef=STATION):
    def split(m):
        tenth = int(round(m * 1e4))
        cm = int(tenth / 100)
        return cm, tenth - cm * 100
    (x, xh), (y, yh), (z, zh) = (split(v) for v in ecef)
    p = struct.pack("<B3xIIiiibbbxIIBB2x", 0, 0, dur, x, y, z, xh, yh, zh, int(acc_m * 1e4), dur, valid, active)
    return ubx(0x01, 0x3B, p)


def ack(ok=True, msg_id=0x8A):
    return ubx(0x05, 0x01 if ok else 0x00, bytes((0x06, msg_id)))


def reassemble(messages):
    """ArduPilot AP_GPS::handle_gps_rtcm_fragment, reduced to what matters for correctness."""
    out, buf, seq, got, count = [], {}, None, 0, 0
    for flags, length, data in messages:
        data = data[:length]
        if not flags & 1:
            out.append(data)
            continue
        frag, s = (flags >> 1) & 3, (flags >> 3) & 0x1F
        if s != seq:
            buf, seq, got, count = {}, s, 0, 0
        buf[frag] = data
        got |= 1 << frag
        if length < 180:
            count = frag + 1
        if count and got == (1 << count) - 1:
            out.append(b"".join(buf[i] for i in range(count)))
            buf, seq, got, count = {}, None, 0, 0
    return out


class FakeSerial:
    def __init__(self, chunks=()):
        self.chunks = list(chunks)
        self.written = bytearray()
        self.closed = False
        self.cv = threading.Condition()

    def push(self, data):
        with self.cv:
            self.chunks.append(data)
            self.cv.notify_all()

    def read(self, n):
        with self.cv:
            if not self.chunks:
                self.cv.wait(0.05)
            return self.chunks.pop(0) if self.chunks else b""

    def write(self, data):
        self.written += data
        return len(data)

    def close(self):
        self.closed = True


class RecordingLink:
    def __init__(self, fix=6, h_acc=0.014, pos=(40.0702, -105.2246)):
        self.sent = []
        self.s = {"transport": "udp"}
        self.state = {"connected": True, "fresh": {"gps": True}, "gps_fix": fix, "satellites": 22,
                      "gps_h_acc": h_acc, "gps_v_acc": 0.021, "transport": "udp",
                      "position": {"lat": pos[0], "lon": pos[1], "alt_msl": 1600}}

    def send_rtcm(self, flags, length, data):
        assert len(data) == 180 and 0 <= length <= 180
        self.sent.append((flags, length, data))

    def snapshot(self):
        return dict(self.state)


class Flight:
    def __init__(self, link=None):
        self.link = link


class FramingTests(unittest.TestCase):
    def test_parser_finds_rtcm_and_ubx_among_nmea_and_split_reads(self):
        f1, f2 = rtcm_1005(), rtcm_blob(1074, 140)
        stream = b"$GNGGA,junk*00\r\n" + f1 + b"\x00\xd3\x00" + nav_svin() + f2 + ack()
        parser, got = StreamParser(), []
        for i in range(0, len(stream), 7):          # dribble 7 bytes at a time
            got += parser.feed(stream[i:i + 7])
        kinds = [(g[0], g[1]) for g in got]
        self.assertEqual(kinds, [("rtcm", 1005), ("ubx", (0x01, 0x3B)), ("rtcm", 1074), ("ubx", (0x05, 0x01))])
        self.assertEqual(got[0][2], f1)
        self.assertEqual(got[2][2], f2)

    def test_corrupted_frame_is_dropped_and_next_one_kept(self):
        bad = bytearray(rtcm_1005())
        bad[10] ^= 0xFF
        good = rtcm_blob(1084, 60)
        parser = StreamParser()
        got = parser.feed(bytes(bad) + good)
        self.assertEqual([g[1] for g in got if g[0] == "rtcm"], [1084])
        self.assertGreaterEqual(parser.bad_crc, 1)

    def test_station_position_round_trips(self):
        ecef = decode_station_ecef(rtcm_1005())
        for a, b in zip(ecef, STATION):
            self.assertAlmostEqual(a, b, places=4)
        lat, lon, h = ecef_to_lla(*STATION)
        self.assertTrue(39 < lat < 41 and -106 < lon < -104, (lat, lon, h))

    @unittest.skipIf(pyrtcm is None, "pyrtcm not installed")
    def test_1005_matches_pyrtcm(self):
        msg = pyrtcm.RTCMReader.parse(rtcm_1005())
        self.assertEqual(msg.identity, "1005")
        self.assertAlmostEqual(msg.DF025, STATION[0], places=4)
        self.assertAlmostEqual(msg.DF026, STATION[1], places=4)
        self.assertAlmostEqual(msg.DF027, STATION[2], places=4)


class FragmentTests(unittest.TestCase):
    def test_every_size_reassembles_exactly(self):
        seq = 0
        for size in (6, 100, 179, 180, 181, 359, 360, 361, 540, 541, 719, 720, 721, 900, 1029, 1500):
            data = bytes((i * 7 + size) & 0xFF for i in range(size))
            msgs, seq = rtcm_messages(data, seq)
            for flags, length, payload in msgs:
                self.assertLessEqual(length, 180)
                self.assertEqual(len(payload), 180)
            self.assertEqual(b"".join(reassemble(msgs)), data, size)

    def test_sequence_wraps_at_32(self):
        seq = 30
        msgs, seq = rtcm_messages(bytes(300), seq)
        self.assertEqual(seq, 31)
        msgs, seq = rtcm_messages(bytes(300), seq)
        self.assertEqual(seq, 0)
        self.assertEqual({(f >> 3) & 0x1F for f, _, _ in msgs}, {31})

    def test_pymavlink_carries_the_fragments(self):
        from pymavlink.dialects.v20 import ardupilotmega as mav
        data = rtcm_blob(1124, 400)
        msgs, _ = rtcm_messages(data, 5)
        sender, receiver = mav.MAVLink(None, 255, 190), mav.MAVLink(None)
        decoded = []
        for flags, length, payload in msgs:
            wire = sender.gps_rtcm_data_encode(flags, length, payload).pack(sender)
            m = receiver.parse_buffer(wire)[0]
            decoded.append((m.flags, m.len, bytes(m.data)))
        self.assertEqual(reassemble(decoded), [data])


class UbloxTests(unittest.TestCase):
    @unittest.skipIf(pyubx2 is None, "pyubx2 not installed")
    def test_key_ids_match_ublox_database(self):
        from pyubx2.ubxtypes_configdb import UBX_CONFIG_DATABASE as DB
        for name, (key_id, _) in rtk.KEYS.items():
            self.assertEqual(DB["CFG_" + name][0], key_id, name)
        for msg, (usb, uart) in rtk.RTCM_OUT.items():
            self.assertEqual(DB[f"CFG_MSGOUT_RTCM_3X_TYPE{msg}_USB"][0], usb)
            self.assertEqual(DB[f"CFG_MSGOUT_RTCM_3X_TYPE{msg}_UART1"][0], uart)

    @unittest.skipIf(pyubx2 is None, "pyubx2 not installed")
    def test_survey_and_fixed_commands_decode(self):
        def values(blob):
            out = {}
            for kind, _, payload in StreamParser().feed(blob):
                msg = pyubx2.UBXReader.parse(ubx(0x06, 0x8A, payload), msgmode=pyubx2.SET)
                out.update({k: v for k, v in msg.__dict__.items() if k.startswith("CFG_")})
            return out
        v = values(survey_setup(180, 1.5))
        self.assertEqual((v["CFG_TMODE_MODE"], v["CFG_TMODE_SVIN_MIN_DUR"], v["CFG_TMODE_SVIN_ACC_LIMIT"]), (1, 180, 15000))
        v = values(fixed_setup(STATION, 0.02))
        self.assertEqual(v["CFG_TMODE_MODE"], 2)
        for axis, metres in zip("XYZ", STATION):
            self.assertAlmostEqual(v[f"CFG_TMODE_ECEF_{axis}"] * 0.01 + v[f"CFG_TMODE_ECEF_{axis}_HP"] * 1e-4, metres, places=4)
        v = values(output_setup())
        self.assertEqual(v["CFG_MSGOUT_RTCM_3X_TYPE1074_USB"], 1)
        self.assertEqual(v["CFG_MSGOUT_RTCM_3X_TYPE1005_USB"], 5)

    @unittest.skipIf(pyubx2 is None, "pyubx2 not installed")
    def test_nav_svin_matches_pyubx2(self):
        blob = nav_svin(dur=123, acc_m=0.875, valid=1, active=0)
        mine = parse_nav_svin(StreamParser().feed(blob)[0][2])
        ref = pyubx2.UBXReader.parse(blob)
        self.assertEqual(mine["duration_s"], ref.dur)
        self.assertAlmostEqual(mine["acc_m"], ref.meanAcc * 1e-4, places=4)          # meanAcc is in 0.1 mm
        self.assertAlmostEqual(mine["ecef"][0], ref.meanX * 0.01 + ref.meanXHP * 1e-4, places=4)
        self.assertTrue(mine["valid"])
        for a, b in zip(mine["ecef"], STATION):
            self.assertAlmostEqual(a, b, places=4)


class LegacyM8Tests(unittest.TestCase):
    @unittest.skipIf(pyubx2 is None, "pyubx2 not installed")
    def test_tmode3_and_cfg_msg_decode(self):
        msg = pyubx2.UBXReader.parse(tmode3(1, min_s=300, acc_m=0.5), msgmode=pyubx2.SET)
        self.assertEqual((msg.rcvrMode, msg.svinMinDur, msg.svinAccLimit), (1, 300, 5000))
        msg = pyubx2.UBXReader.parse(tmode3(2, ecef=STATION, fixed_acc_m=0.02), msgmode=pyubx2.SET)
        self.assertEqual(msg.rcvrMode, 2)
        self.assertAlmostEqual(msg.ecefXOrLat * 0.01 + msg.ecefXOrLatHP * 1e-4, STATION[0], places=4)
        self.assertAlmostEqual(msg.ecefZOrAlt * 0.01 + msg.ecefZOrAltHP * 1e-4, STATION[2], places=4)
        rates = {}
        for kind, (cls, mid), payload in StreamParser().feed(legacy_setup("survey")):
            if (cls, mid) == (0x06, 0x01):
                m = pyubx2.UBXReader.parse(ubx(cls, mid, payload), msgmode=pyubx2.SET)
                rates[(m.msgClass, m.msgID)] = (m.rateUART1, m.rateUSB)
        self.assertEqual(rates[(0xF5, 0x4D)], (1, 1))      # RTCM 1077
        self.assertEqual(rates[(0xF5, 0x05)], (5, 5))      # RTCM 1005 every 5 s
        self.assertEqual(rates[(0x01, 0x3B)], (1, 1))      # NAV-SVIN


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name) / "rtk_base.json"
        self.serial = FakeSerial()
        self.link = RecordingLink()
        self.svc = RtkService(Flight(self.link), self.store, opener=lambda port, baud: self.serial)

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def wait(self, predicate, timeout=2):
        end = time.time() + timeout
        while time.time() < end:
            s = self.svc.snapshot()
            if predicate(s):
                return s
            time.sleep(0.02)
        self.fail(f"timed out: {self.svc.snapshot()}")

    def test_survey_in_then_forward_then_save_spot(self):
        self.svc.start({"port": "/dev/base", "mode": "survey", "min_s": 120, "acc_m": 2})
        self.assertEqual(self.serial.written.count(b"\xb5\x62\x06\x8a"), 3)   # outputs + disable + survey
        for _ in range(3):
            self.serial.push(ack())
        self.serial.push(nav_svin(dur=40, acc_m=3.1, valid=0, active=1))
        s = self.wait(lambda s: s["base_state"] == "surveying")
        self.assertEqual(s["config"], "accepted")
        self.assertFalse(s["can_save"])

        self.serial.push(nav_svin(dur=150, acc_m=1.4, valid=1, active=0))
        self.serial.push(rtcm_1005() + rtcm_blob(1074, 300) + rtcm_blob(1084, 200))
        s = self.wait(lambda s: s["sent_frames"] == 3)
        self.assertEqual(s["base_state"], "sending")
        self.assertEqual(s["messages"], {"1005": 1, "1074": 1, "1084": 1})
        self.assertEqual(s["aircraft"]["fix_name"], "RTK Fixed")
        self.assertIsNotNone(s["aircraft"]["baseline_m"])
        self.assertEqual(len(reassemble(self.link.sent)), 3)

        self.svc.save_position("Reservoir north lot")
        saved = json.loads(self.store.read_text())["positions"][0]
        self.assertEqual(saved["name"], "Reservoir north lot")
        self.assertAlmostEqual(saved["acc_m"], 1.4, places=3)
        for a, b in zip(saved["ecef"], STATION):
            self.assertAlmostEqual(a, b, places=3)

    def test_saved_spot_sets_fixed_mode(self):
        self.store.write_text(json.dumps({"positions": [{"name": "Lot", "ecef": list(STATION), "acc_m": 0.5}]}))
        self.svc.start({"port": "/dev/base", "mode": "saved", "spot": "Lot"})
        blobs = [p for k, _, p in StreamParser().feed(bytes(self.serial.written)) if k == "ubx"]
        modes = [p[8] for p in blobs if struct.unpack_from("<I", p, 4)[0] == 0x20030001]
        self.assertEqual(modes, [0, 2])
        with self.assertRaises(RtkError):
            self.svc.start({"port": "/dev/base", "mode": "saved", "spot": "Gone"})

    def test_as_is_writes_nothing_and_no_aircraft_counts_unsent(self):
        svc = RtkService(Flight(None), self.store, opener=lambda port, baud: self.serial)
        try:
            svc.start({"port": "/dev/base", "mode": "as_is"})
            self.assertEqual(bytes(self.serial.written), b"")
            self.serial.push(rtcm_blob(1094, 120))
            end = time.time() + 2
            while svc.snapshot()["unsent_frames"] < 1 and time.time() < end:
                time.sleep(0.02)
            s = svc.snapshot()
            self.assertEqual((s["sent_frames"], s["unsent_frames"]), (0, 1))
            self.assertEqual(s["aircraft"]["fix_name"], "—")
        finally:
            svc.close()

    def test_m8_base_gets_the_older_setup_after_refusing_valset(self):
        self.svc.start({"port": "/dev/base", "mode": "survey", "min_s": 90, "acc_m": 1.5})
        before = len(self.serial.written)
        self.serial.push(ack(False))                                   # M8 doesn't know CFG-VALSET
        self.wait(lambda s: s["receiver"] == "m8")
        legacy = bytes(self.serial.written[before:])
        self.assertIn(b"\xb5\x62\x06\x71", legacy)                    # CFG-TMODE3 sent
        self.assertNotIn(b"\xb5\x62\x06\x8a", legacy)
        self.serial.push(ack(False) + ack(False))                      # the other two VALSETs, still refused
        for _ in range(legacy.count(b"\xb5\x62\x06\x01")):
            self.serial.push(ack(True, 0x01))
        for _ in range(legacy.count(b"\xb5\x62\x06\x71")):
            self.serial.push(ack(True, 0x71))
        s = self.wait(lambda s: s["config"] == "accepted")
        self.assertEqual(s["receiver"], "m8")

    def test_rejected_settings_are_reported(self):
        self.svc.start({"port": "/dev/base", "mode": "survey"})
        self.serial.push(ack(False))                                   # VALSET refused -> older setup tried
        self.wait(lambda s: s["receiver"] == "m8")
        self.serial.push(ack(False, 0x71))                             # ...and refused too
        s = self.wait(lambda s: s["config"] == "rejected")
        self.assertTrue(s["running"])

    def test_base_port_is_kept_away_from_aircraft_auto_connect(self):
        flight = Flight(self.link)
        flight.reserved_ports = set()
        svc = RtkService(flight, self.store, opener=lambda port, baud: self.serial)
        try:
            svc.start({"port": "/dev/cu.usbmodem-base", "mode": "as_is"})
            self.assertEqual(flight.reserved_ports, {"/dev/cu.usbmodem-base"})
            svc.stop()
            self.assertEqual(flight.reserved_ports, set())
            def broken(port, baud):
                raise OSError("busy")
            svc.opener = broken
            with self.assertRaises(RtkError):
                svc.start({"port": "/dev/cu.usbmodem-base", "mode": "as_is"})
            self.assertEqual(flight.reserved_ports, set())
        finally:
            svc.close()

    def test_refuses_the_aircraft_port(self):
        class Conn:
            device = "/dev/cu.usbmodem-cube"
        self.link.s = {"transport": "serial"}
        self.link.conn = Conn()
        with self.assertRaises(RtkError):
            self.svc.start({"port": "/dev/cu.usbmodem-cube", "mode": "as_is"})

    def test_bad_requests(self):
        for opts in ({}, {"port": "/dev/x", "mode": "nope"}, {"port": "/dev/x", "baud": 1},
                     {"port": "/dev/x", "mode": "survey", "min_s": 5}):
            with self.assertRaises((RtkError, ValueError)):
                self.svc.start(opts)
        with self.assertRaises(RtkError):
            self.svc.save_position("No position yet")


class ApiTests(unittest.TestCase):
    def test_token_and_round_trip(self):
        from flask import Flask
        serial = FakeSerial()
        with tempfile.TemporaryDirectory() as tmp:
            svc = RtkService(Flight(RecordingLink()), Path(tmp) / "b.json", opener=lambda p, b: serial)
            app = Flask(__name__)
            app.register_blueprint(rtk.rtk_api(svc))
            c = app.test_client()
            self.assertEqual(c.post("/api/rtk/start", json={"port": "/dev/b"}).status_code, 403)
            token = c.get("/api/rtk/session").get_json()["token"]
            h = {"X-RTK-Token": token}
            r = c.post("/api/rtk/start", json={"port": "/dev/b", "mode": "as_is"}, headers=h)
            self.assertEqual(r.status_code, 200, r.get_json())
            self.assertTrue(c.get("/api/rtk/status").get_json()["running"])
            self.assertEqual(c.post("/api/rtk/start", json={"port": ""}, headers=h).status_code, 409)
            self.assertFalse(c.post("/api/rtk/stop", json={}, headers=h).get_json()["running"])
            svc.close()


if __name__ == "__main__":
    unittest.main()
