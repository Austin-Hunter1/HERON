#!/usr/bin/env python3
"""Check an ArduCopter dataflash log (.bin) after a test flight: plots + pass/warn table.

    .venv/bin/python scripts/check_flight_log.py path/to/00000042.BIN [--out output/logcheck]

Writes <name>_check.png (one page of plots) and <name>_check.txt (the table) next to --out, and
prints the table. Made for the "hover with payload, then fly AUTO" workflow: hover numbers come
from the steady parts of the flight (in the air, under 1 m/s, not climbing).

Thresholds are common ArduPilot rules of thumb, not limits for a particular airframe. Compare
against a log from the same drone without payload when you have one.

Needs: pymavlink (already in requirements), numpy, matplotlib
    .venv/bin/python -m pip install numpy matplotlib
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from pymavlink import mavutil

WANT = {"RCOU", "CTUN", "VIBE", "BAT", "ATT", "MAG", "GPS", "GPA", "MODE", "ERR", "EV", "MSG",
        "PARM", "ESC", "POS", "XKF4", "NKF4", "RATE"}
MOTORS = {1: 4, 2: 6, 3: 8, 4: 8, 5: 6, 7: 3, 10: 1, 12: 12, 14: 4}       # FRAME_CLASS -> motors
COPTER_MODES = {0: "Stabilize", 1: "Acro", 2: "AltHold", 3: "Auto", 4: "Guided", 5: "Loiter", 6: "RTL",
                7: "Circle", 9: "Land", 16: "PosHold", 17: "Brake", 21: "SmartRTL"}


def load(path):
    log = mavutil.mavlink_connection(str(path), dialect="ardupilotmega")
    data = defaultdict(lambda: defaultdict(list))
    params, msgs = {}, []
    while True:
        m = log.recv_match(type=list(WANT))
        if m is None:
            break
        t = m.get_type()
        d = m.to_dict()
        if t == "PARM":
            params[d["Name"]] = d["Value"]
            continue
        if t == "MSG":
            msgs.append((d.get("TimeUS", 0) / 1e6, d.get("Message", "")))
            continue
        inst = d.get("I", d.get("Instance", d.get("C", 0)))
        key = t if t not in ("BAT", "MAG", "GPS", "GPA", "ESC", "XKF4", "NKF4") else f"{t}{inst}"
        for k, v in d.items():
            if isinstance(v, (int, float)):
                data[key][k].append(v)
    out = {k: {f: np.asarray(v, float) for f, v in fields.items()} for k, fields in data.items()}
    for k in out:
        if "TimeUS" in out[k]:
            out[k]["t"] = out[k]["TimeUS"] / 1e6
    return out, params, msgs


def interp(src, field, t):
    if src is None or field not in src or len(src["t"]) < 2:
        return np.full_like(t, np.nan)
    return np.interp(t, src["t"], src[field])


class Report:
    def __init__(self):
        self.rows = []

    def add(self, area, value, status, note=""):
        self.rows.append((area, value, status, note))

    def grade(self, area, x, warn, fail, fmt, note="", higher_bad=True):
        if x is None or not np.isfinite(x):
            self.add(area, "no data", "-", note)
            return
        bad = (lambda a, b: a > b) if higher_bad else (lambda a, b: a < b)
        st = "FAIL" if bad(x, fail) else "WARN" if bad(x, warn) else "OK"
        self.add(area, fmt.format(x), st, note)

    def text(self):
        w = max(len(r[0]) for r in self.rows) + 2
        v = max(len(r[1]) for r in self.rows) + 2
        lines = [f"{'Check':<{w}}{'Value':<{v}}Result  Note", "-" * (w + v + 40)]
        for a, val, st, note in self.rows:
            lines.append(f"{a:<{w}}{val:<{v}}{st:<8}{note}")
        n = {s: sum(1 for r in self.rows if r[2] == s) for s in ("OK", "WARN", "FAIL")}
        verdict = ("NOT READY for AUTO: fix the FAIL items first" if n["FAIL"]
                   else "Look at the WARN items before flying AUTO" if n["WARN"] else "All checks OK")
        lines += ["", f"{n['OK']} OK, {n['WARN']} WARN, {n['FAIL']} FAIL  ->  {verdict}"]
        return "\n".join(lines)


def analyse(d, params, msgs):
    rep = Report()
    nmot = MOTORS.get(int(params.get("FRAME_CLASS", 2)), 6)
    pwm_max = params.get("MOT_PWM_MAX") or 2000
    pwm_max = pwm_max if pwm_max > 0 else 2000
    rc, ct = d.get("RCOU"), d.get("CTUN")
    if rc is None or ct is None:
        raise SystemExit("This log has no RCOU/CTUN messages (was the drone armed? is LOG_BITMASK default?)")
    t = rc["t"]
    motors = np.vstack([rc.get(f"C{i}", np.full_like(t, np.nan)) for i in range(1, nmot + 1)])
    alt = interp(ct, "Alt", t)
    tho = interp(ct, "ThO", t)
    crt = interp(ct, "CRt", t) if "CRt" in ct else np.zeros_like(t)
    pos = d.get("POS")
    gps = d.get("GPS0")
    spd = interp(gps, "Spd", t) if gps is not None else np.zeros_like(t)
    airborne = (np.nanmin(motors, axis=0) > (params.get("MOT_PWM_MIN") or 1000) + 50) & (alt > 1.5)
    steady = airborne & (np.abs(spd) < 1.0) & (np.abs(crt / (100 if np.nanmax(np.abs(crt)) > 50 else 1)) < 0.5)
    t_air = float(np.sum(np.diff(t, prepend=t[0])[airborne]))
    rep.add("Time in the air", f"{t_air / 60:.1f} min", "-", f"steady/hover samples: {int(steady.sum())}")
    hov = steady if steady.sum() > 20 else airborne

    # --- motors
    if hov.any():
        spread = np.nanmedian(np.nanmax(motors[:, hov], 0) - np.nanmin(motors[:, hov], 0))
        means = np.nanmean(motors[:, hov], 1)
        hi = int(np.nanargmax(means)) + 1
        rep.grade("Motor spread (hover)", spread, 150, 250, "{:.0f} µs",
                  f"highest: motor {hi} at {means[hi - 1]:.0f} µs (mean of all {np.nanmean(means):.0f})")
        rep.add("Hover PWM (mean)", f"{np.nanmean(means):.0f} µs", "-", "compare with a no-payload log")
    if airborne.any():
        sat = np.mean(np.nanmax(motors[:, airborne], 0) >= pwm_max - 60) * 100
        rep.grade("Motors near max", sat, 1.0, 5.0, "{:.1f} % of time", f"any motor ≥ {pwm_max - 60:.0f} µs")
    rep.grade("Hover throttle ThO", float(np.nanmedian(tho[hov])) if hov.any() else None, 0.65, 0.75, "{:.2f}",
              f"MOT_THST_HOVER at end of log: {params.get('MOT_THST_HOVER', float('nan')):.3f}")

    # --- vibration
    vb = d.get("VIBE")
    if vb is not None:
        va = interp(vb, "VibeX", t), interp(vb, "VibeY", t), interp(vb, "VibeZ", t)
        p95 = max(float(np.nanpercentile(v[airborne], 95)) for v in va) if airborne.any() else None
        rep.grade("Vibration (95th pct)", p95, 30, 60, "{:.1f} m/s²", "worst of X/Y/Z")
        clip = sum(float(np.nanmax(vb[c]) - np.nanmin(vb[c])) for c in ("Clip0", "Clip1", "Clip2", "Clip") if c in vb)
        rep.grade("Accel clipping", clip, 0.5, 100, "{:.0f} events")

    # --- battery
    bt = d.get("BAT0")
    if bt is not None and "Volt" in bt:
        vmax = float(np.nanmax(bt["Volt"]))
        cells = max(1, round(vmax / 4.2 + 0.3)) if vmax > 5 else 1
        vb_air = interp(bt, "Volt", t)[airborne]
        vmin = float(np.nanmin(vb_air)) if vb_air.size else float(np.nanmin(bt["Volt"]))
        rep.grade("Lowest voltage / cell", vmin / cells, 3.5, 3.3, "{:.2f} V", f"{cells}S, start {vmax:.1f} V",
                  higher_bad=False)
        cur = interp(bt, "Curr", t)
        if hov.any() and np.isfinite(cur[hov]).any():
            i_h = float(np.nanmedian(cur[hov]))
            cap = params.get("BATT_CAPACITY") or 0
            used = float(np.nanmax(bt.get("CurrTot", [0])))
            note = ""
            if cap > 0:
                left = cap * 0.8 - used
                note = f"used {used:.0f} of {cap:.0f} mAh; at this current ≈{left / 1000 / i_h * 60:.0f} min more to the 20 % reserve"
                if vmax / cells < 4.1:
                    note += " (assumes a FULL pack; this one started part-charged, so less)"
            rep.add("Hover current", f"{i_h:.1f} A", "-", note)
        rep.grade("Start voltage / cell", vmax / cells, 4.1, 3.9, "{:.2f} V", "full 6S LiPo ≈ 4.15–4.2 V/cell",
                  higher_bad=False)

    # --- attitude tracking
    at = d.get("ATT")
    if at is not None and airborne.any():
        for ax in ("Roll", "Pitch"):
            e = interp(at, f"Des{ax}", t) - interp(at, ax, t)
            rep.grade(f"{ax} tracking (RMS)", float(np.sqrt(np.nanmean(e[airborne] ** 2))), 3, 6, "{:.1f}°",
                      "desired vs actual")
        ey = (interp(at, "DesYaw", t) - interp(at, "Yaw", t) + 180) % 360 - 180
        rep.grade("Yaw tracking (RMS)", float(np.sqrt(np.nanmean(ey[airborne] ** 2))), 8, 15, "{:.1f}°")

    # --- compass vs current (interference)
    mg = d.get("MAG0")
    if mg is not None and bt is not None and "Curr" in bt and airborne.any():
        mnorm = np.sqrt(interp(mg, "MagX", t) ** 2 + interp(mg, "MagY", t) ** 2 + interp(mg, "MagZ", t) ** 2)
        cur = interp(bt, "Curr", t)
        ok = airborne & np.isfinite(mnorm) & np.isfinite(cur)
        if ok.sum() > 20 and np.ptp(cur[ok]) > 2:
            slope = np.polyfit(cur[ok], mnorm[ok], 1)[0]
            pct = abs(slope * np.ptp(cur[ok])) / np.nanmedian(mnorm[ok]) * 100
            rep.grade("Compass vs motor current", pct, 30, 60, "{:.0f} %",
                      "field change across the current range (compassmot: <30 % good)")

    # --- GPS / RTK
    if gps is not None and airborne.any():
        st = interp(gps, "Status", t)[airborne]
        rep.add("GPS fix in the air", f"RTK fixed {np.mean(st >= 6) * 100:.0f} %, float {np.mean(st == 5) * 100:.0f} %",
                "OK" if np.mean(st >= 5) > 0.9 else "WARN", "6 = RTK fixed, 5 = float, 3 = no RTK")
        rep.grade("Fewest satellites", float(np.nanmin(interp(gps, "NSats", t)[airborne])), 12, 8, "{:.0f}",
                  higher_bad=False)
    ga = d.get("GPA0")
    if ga is not None and airborne.any():
        rep.grade("GPS horizontal acc (95 %)", float(np.nanpercentile(interp(ga, "HAcc", t)[airborne], 95)), 0.1, 1.0,
                  "{:.2f} m")

    # --- EKF innovations
    xk = d.get("XKF40") or d.get("NKF40")
    if xk is not None:
        worst = max(float(np.nanmax(xk[f])) for f in ("SV", "SP", "SH", "SM") if f in xk)
        rep.grade("EKF test ratio (max)", worst, 0.8, 1.0, "{:.2f}", "over 1 = a sensor was being rejected")

    # --- errors and failsafes
    er = d.get("ERR")
    nerr = 0 if er is None else int(np.sum(er.get("ECode", np.zeros(0)) != 0))
    bad = [m for _, m in msgs if any(w in m.lower() for w in ("fail", "error", "crash", "lost", "bad", "thrust loss"))]
    rep.add("Errors / failsafes", f"{nerr} ERR, {len(bad)} msgs", "OK" if not (nerr or bad) else "WARN",
            "; ".join(dict.fromkeys(bad))[:120])
    return rep, dict(t=t, motors=motors, alt=alt, tho=tho, airborne=airborne, steady=steady, nmot=nmot, pwm_max=pwm_max)


def plot(d, ctx, out_png, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t0 = ctx["t"][0]
    T = lambda x: (x - t0) / 60
    fig, ax = plt.subplots(4, 2, figsize=(15, 13), sharex=True)
    fig.suptitle(title, fontsize=13, fontweight="bold")
    modes = d.get("MODE")

    def shade(a):
        if modes is None:
            return
        ts = list(modes["t"]) + [ctx["t"][-1]]
        num = modes.get("ModeNum", modes.get("Mode"))
        span = max(ts[-1] - ts[0], 1)
        last_x = -1e9
        for i in range(len(ts) - 1):
            n = int(num[i])
            a.axvspan(T(ts[i]), T(ts[i + 1]), color=f"C{n % 10}", alpha=0.07, lw=0)
            if a is ax[0, 0] and ts[i] - last_x > 0.04 * span:            # skip labels that would overlap
                a.text(T(ts[i]), 1.01, COPTER_MODES.get(n, str(n)), transform=a.get_xaxis_transform(), fontsize=8)
                last_x = ts[i]

    a = ax[0, 0]
    a.plot(T(ctx["t"]), ctx["alt"], "k", lw=1)
    a.set_ylabel("Altitude above home (m)")
    a = ax[0, 1]
    for i in range(ctx["nmot"]):
        a.plot(T(ctx["t"]), ctx["motors"][i], lw=0.8, label=f"M{i + 1}")
    a.axhline(ctx["pwm_max"] - 60, color="r", ls="--", lw=0.8)
    a.set_ylabel("Motor output RCOU (µs)")
    a.legend(ncol=ctx["nmot"], fontsize=7, loc="lower center")
    a = ax[1, 0]
    a.plot(T(ctx["t"]), ctx["tho"], "C0", lw=1)
    a.axhline(0.65, color="orange", ls="--", lw=0.8)
    a.set_ylabel("Throttle out ThO")
    vb = d.get("VIBE")
    a = ax[1, 1]
    if vb is not None:
        for f in ("VibeX", "VibeY", "VibeZ"):
            a.plot(T(vb["t"]), vb[f], lw=0.8, label=f)
        a.axhline(30, color="orange", ls="--", lw=0.8)
        a.axhline(60, color="r", ls="--", lw=0.8)
        a.legend(fontsize=7)
    a.set_ylabel("Vibration (m/s²)")
    bt = d.get("BAT0")
    a = ax[2, 0]
    if bt is not None:
        a.plot(T(bt["t"]), bt["Volt"], "C2", lw=1)
        a.set_ylabel("Battery (V)", color="C2")
        if "Curr" in bt:
            b = a.twinx()
            b.plot(T(bt["t"]), bt["Curr"], "C3", lw=0.8)
            b.set_ylabel("Current (A)", color="C3")
    at = d.get("ATT")
    a = ax[2, 1]
    if at is not None:
        a.plot(T(at["t"]), at["DesRoll"], "C0", lw=0.8, ls="--", label="desired roll")
        a.plot(T(at["t"]), at["Roll"], "C0", lw=0.8, label="roll")
        a.plot(T(at["t"]), at["DesPitch"], "C1", lw=0.8, ls="--", label="desired pitch")
        a.plot(T(at["t"]), at["Pitch"], "C1", lw=0.8, label="pitch")
        a.legend(fontsize=7, ncol=2)
    a.set_ylabel("Attitude (°)")
    mg = d.get("MAG0")
    a = ax[3, 0]
    if mg is not None:
        a.plot(T(mg["t"]), np.sqrt(mg["MagX"] ** 2 + mg["MagY"] ** 2 + mg["MagZ"] ** 2), "C4", lw=0.8)
    a.set_ylabel("Compass field |MAG| (mGauss)")
    a.set_xlabel("Minutes since logging started")
    gp, ga = d.get("GPS0"), d.get("GPA0")
    a = ax[3, 1]
    if gp is not None:
        a.step(T(gp["t"]), gp["Status"], "C5", lw=1, where="post")
        a.set_ylabel("GPS status (6 = RTK fixed)", color="C5")
        a.set_ylim(0, 7)
    if ga is not None:
        b = a.twinx()
        b.plot(T(ga["t"]), ga["HAcc"], "C6", lw=0.8)
        b.set_ylabel("HAcc (m)", color="C6")
        b.set_yscale("log")
    a.set_xlabel("Minutes since logging started")
    for a in ax.flat:
        shade(a)
        a.grid(alpha=0.25)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(out_png, dpi=110)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", type=Path)
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "output" / "logcheck")
    a = ap.parse_args()
    if not a.log.exists():
        sys.exit(f"No such file: {a.log}")
    print(f"Reading {a.log.name} ...", flush=True)
    d, params, msgs = load(a.log)
    rep, ctx = analyse(d, params, msgs)
    a.out.mkdir(parents=True, exist_ok=True)
    stem = a.log.stem
    txt = rep.text()
    (a.out / f"{stem}_check.txt").write_text(txt + "\n")
    print("\n" + txt)
    try:
        plot(d, ctx, a.out / f"{stem}_check.png", f"{a.log.name}  ·  {ctx['nmot']} motors")
        print(f"\nPlots: {a.out / (stem + '_check.png')}")
    except ImportError:
        print("\n(matplotlib not installed: .venv/bin/python -m pip install matplotlib)")


if __name__ == "__main__":
    main()
