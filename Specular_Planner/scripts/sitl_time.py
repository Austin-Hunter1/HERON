#!/usr/bin/env python3
"""Fly a QGC .waypoints mission in ArduCopter SITL and time every phase in simulated seconds.

    .venv/bin/python scripts/sitl_time.py output/HERON_20261109_1720.waypoints \
        --model hexax:$HOME/Downloads/X6MAX_SITL_Model/sitl/x6max.json \
        --defaults $HOME/ardupilot/Tools/autotest/default_params/copter.parm,$HOME/Downloads/X6MAX_SITL_Model/sitl/x6max_sitl.parm \
        [--set NAME=VALUE ...] [--speedup 20] [--wind 5,270]

Runs ~/ardupilot/build/sitl/bin/arducopter headless (no map/console), uploads the mission, arms,
starts AUTO, and prints climb / transit / survey / return+land seconds.

Time comes from the autopilot's own clock (SYSTEM_TIME.time_boot_ms), so --speedup doesn't change it.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

from pymavlink import mavutil, mavwp

AP = Path(os.environ.get("ARDUPILOT", Path.home() / "ardupilot")).expanduser()


def tempfile_dir(instance):
    import tempfile
    return Path(tempfile.gettempdir()) / f"heron_sitl_i{instance}"


def load_mission(path):
    rows = [l.split("\t") for l in Path(path).read_text().splitlines() if l and l[0].isdigit()]
    items = []
    for r in rows:
        items.append(dict(seq=int(r[0]), frame=int(r[2]), cmd=int(r[3]), p=[float(x) for x in r[4:8]],
                          x=float(r[8]), y=float(r[9]), z=float(r[10]), auto=int(r[11])))
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mission")
    ap.add_argument("--model", required=True)
    ap.add_argument("--defaults", required=True)
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--speedup", type=float, default=20)
    ap.add_argument("--home-alt", type=float, default=1582.0)
    ap.add_argument("--wind", help="speed_mps,dir_deg")
    ap.add_argument("--json", help="write results here")
    ap.add_argument("--instance", type=int, default=0)
    a = ap.parse_args()

    items = load_mission(a.mission)
    home = items[0]
    work = Path(tempfile_dir(a.instance))
    work.mkdir(parents=True, exist_ok=True)
    port = 5760 + 10 * a.instance
    # SITL opens files relative to its working folder (it strips a leading "/"), so copy the
    # frame JSON next to it and pass the bare name.
    model = a.model
    if ":" in model and Path(model.split(":", 1)[1]).expanduser().is_file():
        frame, src = model.split(":", 1)
        src = Path(src).expanduser()
        (work / src.name).write_bytes(src.read_bytes())
        model = f"{frame}:{src.name}"
    cmd = [str(AP / "build/sitl/bin/arducopter"), "-S", "--model", model, "--speedup", str(a.speedup),
           "--home", f"{home['x']},{home['y']},{a.home_alt},0", "--defaults", a.defaults, "-w",
           "-I", str(a.instance)]
    if not Path(cmd[0]).exists():
        sys.exit(f"No SITL binary at {cmd[0]}. Build it: cd {AP} && ./waf configure --board sitl && ./waf copter")
    print("running:", " ".join(cmd), file=sys.stderr)
    sim = subprocess.Popen(cmd, cwd=work, stdout=open(work / "sim.log", "w"), stderr=subprocess.STDOUT)
    try:
        return run(a, items, port, sim)
    finally:
        sim.terminate()
        try:
            sim.wait(5)
        except subprocess.TimeoutExpired:
            sim.kill()


def run(a, items, port, sim):
    logf = Path(tempfile_dir(a.instance)) / "sim.log"
    for _ in range(60):                               # wait until SITL is listening, or report why not
        time.sleep(0.5)
        if sim.poll() is not None:
            sys.exit(f"SITL exited (code {sim.returncode}). Last lines of {logf}:\n" + "\n".join(logf.read_text().splitlines()[-25:]))
        if "bind port" in logf.read_text() or "Waiting for connection" in logf.read_text():
            break
    else:
        sys.exit(f"SITL started but never opened TCP {port} in 30 s. Last lines of {logf}:\n"
                 + "\n".join(logf.read_text().splitlines()[-25:]))
    m = mavutil.mavlink_connection(f"tcp:127.0.0.1:{port}", source_system=255, retries=10)
    m.wait_heartbeat(timeout=30)
    m.mav.request_data_stream_send(m.target_system, m.target_component, mavutil.mavlink.MAV_DATA_STREAM_ALL, 10, 1)

    def setp(name, value):
        for _ in range(5):
            m.mav.param_set_send(m.target_system, m.target_component, name.encode(), float(value),
                                 mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
            r = m.recv_match(type="PARAM_VALUE", blocking=True, timeout=3)
            while r and r.param_id != name:
                r = m.recv_match(type="PARAM_VALUE", blocking=True, timeout=3)
            if r and abs(r.param_value - float(value)) < 1e-3 * max(1, abs(float(value))):
                return
        raise RuntimeError(f"param {name} not set")

    sets = dict(s.split("=", 1) for s in a.set)
    if a.wind:
        spd, d = a.wind.split(",")
        sets.update(SIM_WIND_SPD=spd, SIM_WIND_DIR=d, SIM_WIND_TURB="1")
    for k, v in sets.items():
        setp(k, v)

    # wait for GPS + EKF ready to arm
    t0 = time.time()
    while time.time() - t0 < 120:
        msg = m.recv_match(type=["GPS_RAW_INT", "SYS_STATUS", "EKF_STATUS_REPORT"], blocking=True, timeout=2)
        if msg and msg.get_type() == "EKF_STATUS_REPORT" and (msg.flags & 0x10) and (msg.flags & 0x08):
            break
    # upload the mission (seq 0 = home)
    wp = mavwp.MAVWPLoader()
    for it in items:
        wp.add(mavutil.mavlink.MAVLink_mission_item_message(
            m.target_system, m.target_component, it["seq"], it["frame"], it["cmd"], 0, it["auto"],
            *it["p"], it["x"], it["y"], it["z"]))
    m.waypoint_clear_all_send()
    m.waypoint_count_send(wp.count())
    sent = set()
    while len(sent) < wp.count():
        req = m.recv_match(type=["MISSION_REQUEST", "MISSION_REQUEST_INT"], blocking=True, timeout=10)
        if req is None:
            raise RuntimeError("mission upload stalled")
        m.mav.send(wp.wp(req.seq))
        sent.add(req.seq)
    ack = m.recv_match(type="MISSION_ACK", blocking=True, timeout=10)
    assert ack and ack.type == 0, ack

    last_wp = max(it["seq"] for it in items if it["cmd"] == 16 and it["seq"] > 0)
    first_wp = min(it["seq"] for it in items if it["cmd"] == 16 and it["seq"] > 0)
    # arm in GUIDED, then AUTO + MISSION_START (what HERON's Fly Mission does)
    for _ in range(30):
        m.set_mode("GUIDED")
        m.arducopter_arm()
        hb = m.recv_match(type="HEARTBEAT", blocking=True, timeout=2)
        if m.motors_armed():
            break
        time.sleep(1)
    if not m.motors_armed():
        raise RuntimeError("would not arm: " + str(m.recv_match(type="STATUSTEXT", blocking=False)))
    m.set_mode("AUTO")
    m.mav.command_long_send(m.target_system, m.target_component, mavutil.mavlink.MAV_CMD_MISSION_START, 0, 0, 0, 0, 0, 0, 0, 0)

    ev = {}
    tb = 0
    max_alt = 0
    track = []
    peak_spd = 0
    start_wall = time.time()
    while time.time() - start_wall < 1800:
        msg = m.recv_match(blocking=True, timeout=5)
        if msg is None:
            if sim.poll() is not None:
                raise RuntimeError("SITL exited")
            continue
        t = msg.get_type()
        if hasattr(msg, "time_boot_ms"):
            tb = msg.time_boot_ms / 1000.0
        if t == "GLOBAL_POSITION_INT":
            alt = msg.relative_alt / 1000
            max_alt = max(max_alt, alt)
            spd = math.hypot(msg.vx, msg.vy) / 100
            peak_spd = max(peak_spd, spd)
            if "armed" not in ev:
                ev["armed"] = tb
            if alt > 0.5 and "liftoff" not in ev:
                ev["liftoff"] = tb
            if alt > 117 and "at_alt" not in ev:
                ev["at_alt"] = tb
            if int(tb * 2) != int((tb - 0.1) * 2):
                track.append((round(tb, 1), msg.lat / 1e7, msg.lon / 1e7, round(alt, 1), round(spd, 2)))
        elif t == "MISSION_ITEM_REACHED":
            if msg.seq == first_wp and "first_wp" not in ev:
                ev["first_wp"] = tb
            if msg.seq == last_wp and "last_wp" not in ev:
                ev["last_wp"] = tb
        elif t == "HEARTBEAT" and msg.get_srcSystem() == m.target_system:
            mode = mavutil.mode_string_v10(msg)
            armed = msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
            if mode == "RTL" and "rtl" not in ev:
                ev["rtl"] = tb
            if "liftoff" in ev and not armed:
                ev["disarmed"] = tb
                break
        elif t == "STATUSTEXT" and any(w in msg.text.lower() for w in ("fail", "crash", "error")):
            print("  !", msg.text, file=sys.stderr)
    a0 = ev.get("armed", 0)
    rel = {k: round(v - a0, 1) for k, v in ev.items()}
    out = dict(events=rel, peak_speed_mps=round(peak_spd, 2), max_alt_m=round(max_alt, 1), set=sets)
    if "disarmed" in rel:
        out["total_s"] = rel["disarmed"]
        out["phases"] = {
            "takeoff_climb": rel.get("at_alt", 0),
            "transit_to_first_wp": round(rel["first_wp"] - rel.get("at_alt", 0), 1),
            "survey": round(rel["last_wp"] - rel["first_wp"], 1),
            "return_and_land": round(rel["disarmed"] - rel["last_wp"], 1),
        }
    if a.json:
        Path(a.json).write_text(json.dumps(dict(out, track=track)))
    print(json.dumps(out))


if __name__ == "__main__":
    sys.exit(main())
