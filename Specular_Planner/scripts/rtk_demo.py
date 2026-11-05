#!/usr/bin/env python3
"""Try the RTK base flow with no hardware: a simulated u-blox base and a simulated drone.

Runs the normal planner with two stand-ins:
  * Base: answers the u-blox setup, surveys in (accelerated), then sends RTCM 1005/1074/1084/
    1094/1124/1230 every second, exactly like a real F9P base on USB.
  * Drone: a real MAVLink 2 decoder. Every GPS_RTCM_DATA the planner sends is packed by pymavlink,
    parsed back from the raw bytes, and reassembled the way ArduPilot does it. The drone's GPS fix
    improves only while complete RTCM keeps arriving: 3D fix -> RTK Float -> RTK Fixed.

The RTCM payload bytes are filler: real framing, lengths and checksums, but no real satellite data.
Nothing opens a serial port or talks to a flight controller.

    .venv/bin/python scripts/rtk_demo.py                 # open http://127.0.0.1:5055 -> Fly
    .venv/bin/python scripts/rtk_demo.py --show-packets  # also print every MAVLink packet sent
    .venv/bin/python scripts/rtk_demo.py --drone-after 40   # drone "connects" 40 s after start

In Fly, open the RTK bar, pick "Simulated base (demo)" and press Start base. The rest of the Fly
page still says it is looking for an aircraft: only the RTK path is simulated.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
import threading
import time
from pathlib import Path

os.environ["MAVLINK20"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as planner                                          # noqa: E402  (the normal planner)
from engine import flight as flight_mod                        # noqa: E402
from engine.rtk import crc24q, ecef_to_lla, ubx                 # noqa: E402

DEMO_PORT = "demo-base"
BASE_ECEF = (-1283411.52, -4728125.08, 4088062.46)             # near the Boulder Reservoir pad
NAMES = {1005: "base position", 1074: "GPS", 1084: "GLONASS", 1094: "Galileo", 1124: "BeiDou", 1230: "GLONASS bias"}
SIZES = {1074: 226, 1084: 147, 1094: 201, 1124: 183, 1230: 6}  # typical MSM4 payload sizes, bytes


# ---------------------------------------------------------------- simulated base receiver

def _bits(fields):
    bits = "".join(format(v & ((1 << n) - 1), f"0{n}b") for n, v in fields)
    bits += "0" * (-len(bits) % 8)
    return bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))


def rtcm_frame(payload: bytes) -> bytes:
    head = bytes((0xD3, (len(payload) >> 8) & 0x03, len(payload) & 0xFF)) + payload
    return head + crc24q(head).to_bytes(3, "big")


def rtcm_1005(ecef):
    x, y, z = (int(round(v * 1e4)) for v in ecef)
    return rtcm_frame(_bits([(12, 1005), (12, 7), (6, 0), (1, 1), (1, 1), (1, 1), (1, 0),
                             (38, x), (1, 0), (1, 0), (38, y), (2, 0), (38, z)]))


def rtcm_filler(msg_type, size, tick):
    body = bytes(((msg_type >> 4) & 0xFF, (msg_type & 0x0F) << 4)) + bytes((i * 37 + tick) & 0xFF for i in range(size - 2))
    return rtcm_frame(body)


def nav_svin(duration, acc_m, valid, active, ecef):
    def split(m):
        tenth = int(round(m * 1e4))
        cm = int(tenth / 100)
        return cm, tenth - cm * 100
    (x, xh), (y, yh), (z, zh) = (split(v) for v in ecef)
    return ubx(0x01, 0x3B, struct.pack("<B3xIIiiibbbxIIBB2x", 0, 0, duration, x, y, z, xh, yh, zh,
                                       int(acc_m * 1e4), duration * 8, valid, active))


class DemoBase:
    """Looks like pyserial to RtkService. Reacts to the setup HERON writes, like an F9P would."""

    def __init__(self, speed):
        self.speed = speed
        self.out = bytearray()
        self.cv = threading.Condition()
        self.closed = False
        self.mode = "as_is"
        self.survey_start = None
        self.min_s, self.acc_limit = 120, 2.0
        threading.Thread(target=self._run, daemon=True, name="demo-base").start()

    def write(self, data):
        # Parse CFG-VALSET from HERON: ACK each one and note the mode it sets.
        i = 0
        while (i := data.find(b"\xb5\x62\x06\x8a", i)) >= 0:
            length = data[i + 4] | data[i + 5] << 8
            payload = data[i + 6:i + 6 + length]
            for k in range(4, len(payload) - 4):
                key = struct.unpack_from("<I", payload, k)[0]
                if key == 0x20030001:                                   # TMODE_MODE
                    self.mode = {0: "off", 1: "survey", 2: "fixed"}.get(payload[k + 4], "off")
                elif key == 0x40030010:                                 # SVIN_MIN_DUR, seconds
                    self.min_s = struct.unpack_from("<I", payload, k + 4)[0]
                elif key == 0x40030011:                                 # SVIN_ACC_LIMIT, 0.1 mm
                    self.acc_limit = struct.unpack_from("<I", payload, k + 4)[0] / 1e4
            if self.mode == "survey":
                self.survey_start = time.monotonic()
            self._emit(ubx(0x05, 0x01, b"\x06\x8a"))
            i += 6 + length + 2
        return len(data)

    def _emit(self, data):
        with self.cv:
            self.out += data
            self.cv.notify_all()

    def read(self, n):
        with self.cv:
            if not self.out:
                self.cv.wait(0.2)
            chunk, self.out[:] = bytes(self.out[:n]), self.out[n:]
            return chunk

    def close(self):
        self.closed = True

    def _run(self):
        tick = 0
        while not self.closed:
            time.sleep(1)
            tick += 1
            if self.mode == "survey" and self.survey_start is not None:
                t = int((time.monotonic() - self.survey_start) * self.speed)
                acc = max(0.6, 6.0 / (1 + t / 25))                       # accuracy improves with time
                done = t >= self.min_s and acc <= self.acc_limit
                self._emit(nav_svin(t, acc, int(done), int(not done), BASE_ECEF))
                if not done:
                    continue                                           # a surveying F9P sends no RTCM
            frames = [rtcm_1005(BASE_ECEF)] if tick % 5 == 1 else []
            frames += [rtcm_filler(m, n + (tick % 9), tick) for m, n in SIZES.items()]
            self._emit(b"".join(frames))


# ---------------------------------------------------------------- simulated drone

class DemoDrone:
    """Stands in for MavlinkLink. Decodes real MAVLink bytes and reassembles like ArduPilot."""

    def __init__(self, connect_after, show):
        from pymavlink.dialects.v20 import ardupilotmega as mav
        self.mav = mav
        self.packer = mav.MAVLink(None, 255, 190)
        self.parser = mav.MAVLink(None)
        self.start = time.monotonic()
        self.connect_after = connect_after
        self.show = show
        self.s = {"transport": "herelink_hotspot"}            # pretend Herelink link
        self.frag = {"seq": None, "parts": {}, "count": 0}
        self.good = []                                        # times complete RTCM arrived
        self.lat, self.lon, _ = ecef_to_lla(*BASE_ECEF)
        self.lat += 0.0009                                    # about 100 m north of the base

    def connected(self):
        return time.monotonic() - self.start >= self.connect_after

    def send_rtcm(self, flags, length, data):
        if not self.connected():
            raise ConnectionError("demo drone not connected yet")
        wire = self.packer.gps_rtcm_data_encode(flags, length, data).pack(self.packer)
        self.packer.seq = (self.packer.seq + 1) % 256          # packet counter, as pymavlink's send() does
        msg = self.parser.parse_buffer(wire)[0]               # what the Cube would decode
        if self.show:
            kind = "whole" if not msg.flags & 1 else f"fragment {(msg.flags >> 1) & 3}"
            print(f"  MAVLink GPS_RTCM_DATA  flags=0x{msg.flags:02x} ({kind}, seq {msg.flags >> 3:2d})  "
                  f"len={msg.len:3d}  {len(wire):3d} B  {wire[:14].hex(' ')} …")
        payload = bytes(msg.data[:msg.len])
        if not msg.flags & 1:
            self._complete(payload)
            return
        f = self.frag
        seq, part = msg.flags >> 3, (msg.flags >> 1) & 3
        if seq != f["seq"]:
            f.update(seq=seq, parts={}, count=0)
        f["parts"][part] = payload
        if msg.len < 180:
            f["count"] = part + 1
        if f["count"] and all(i in f["parts"] for i in range(f["count"])):
            self._complete(b"".join(f["parts"][i] for i in range(f["count"])))
            f.update(seq=None, parts={}, count=0)

    def _complete(self, rtcm):
        ok = rtcm[:1] == b"\xd3" and crc24q(rtcm[:-3]) == int.from_bytes(rtcm[-3:], "big")
        if ok:
            self.good.append(time.monotonic())
            if self.show:
                typ = (rtcm[3] << 4) | (rtcm[4] >> 4)
                print(f"  -> drone rebuilt RTCM {typ} ({NAMES.get(typ, '?')}), {len(rtcm)} B, checksum OK")

    def snapshot(self):
        now = time.monotonic()
        self.good = [t for t in self.good if now - t < 120]
        recent = [t for t in self.good if now - t < 3]
        streak = 0.0
        if recent and self.good:
            # how long corrections have kept flowing without a 3 s gap
            first = self.good[-1]
            for t in reversed(self.good):
                if first - t > 3:
                    break
                first = t
            streak = now - first
        fix, acc = (3, 0.62)
        if recent and streak > 20:
            fix, acc = 6, 0.014
        elif recent and streak > 5:
            fix, acc = 5, max(0.05, 0.6 - streak * 0.03)
        return {"connected": self.connected(), "fresh": {"gps": True}, "gps_fix": fix, "satellites": 21,
                "gps_h_acc": acc, "gps_v_acc": acc * 1.6, "transport": "herelink_hotspot",
                "position": {"lat": self.lat, "lon": self.lon, "alt_msl": 1620.0}}


class DemoFlight:
    def __init__(self, drone):
        self.link = drone
        self.reserved_ports = set()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drone-after", type=float, default=0, help="seconds before the demo drone connects (default 0)")
    ap.add_argument("--survey-speed", type=float, default=4, help="survey-in runs this many times faster (default 4)")
    ap.add_argument("--show-packets", action="store_true", help="print every MAVLink packet sent to the drone")
    a = ap.parse_args()

    drone = DemoDrone(a.drone_after, a.show_packets)
    planner.rtk.flight = DemoFlight(drone)
    planner.rtk.opener = lambda port, baud: DemoBase(a.survey_speed)

    real_ports = flight_mod.serial_port_records

    def ports_with_demo():
        try:
            ports = real_ports()
        except Exception:
            ports = []
        return [{"device": DEMO_PORT, "description": "Simulated base (demo)", "autopilot": False}] + ports
    flight_mod.serial_port_records = ports_with_demo           # only the RTK port list reads this per call

    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)     # keep the terminal for the RTK output
    print("RTK demo: simulated base + simulated drone. Nothing touches real hardware.")
    print("Open http://127.0.0.1:5055, go to Fly, open the RTK bar, pick 'Simulated base (demo)', Start base.")
    if a.drone_after:
        print(f"The demo drone connects {a.drone_after:.0f} s after this starts.")
    planner.start_almanac_watch()
    planner.app.run(host="127.0.0.1", port=5055, debug=False)


if __name__ == "__main__":
    main()
