#!/usr/bin/env python3
"""Check a base-station raw log before relying on it for CSRS-PPP / OPUS post-processing.

    .venv/bin/python scripts/check_base_log.py output/rtk/20261109_163012Z_base.ubx

Reports how long the raw measurements (UBX RXM-RAWX) cover, any gaps, how many satellites and
signals were tracked, the logging rate, and whether there is GPS L1 + L2 data. Also prints the base
position from the matching .json. Exits non-zero if the file can't be used.
"""
from __future__ import annotations

import json
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.rtk import StreamParser  # noqa: E402

GNSS = {0: "GPS", 1: "SBAS", 2: "Galileo", 3: "BeiDou", 5: "QZSS", 6: "GLONASS"}
GPS_L2 = {3, 4}                           # sigId: L2 CL, L2 CM


def summarise(path: Path):
    parser = StreamParser()
    epochs = []                           # (gps seconds, satellites, gps_l1, gps_l2)
    signals = Counter()
    sfrbx = 0
    with open(path, "rb") as f:
        while chunk := f.read(1 << 16):
            for kind, ident, payload in parser.feed(chunk):
                if kind != "ubx":
                    continue
                if ident == (0x02, 0x13):
                    sfrbx += 1
                if ident != (0x02, 0x15) or len(payload) < 16:
                    continue
                tow, week = struct.unpack_from("<dH", payload, 0)
                n = payload[11]
                sats, l1, l2 = set(), set(), set()
                for i in range(n):
                    o = 16 + 32 * i
                    if o + 32 > len(payload):
                        break
                    gnss, sv, sig = payload[o + 20], payload[o + 21], payload[o + 22]
                    sats.add((gnss, sv))
                    signals[GNSS.get(gnss, f"gnss{gnss}")] += 1
                    if gnss == 0:
                        (l2 if sig in GPS_L2 else l1).add(sv)
                epochs.append((week * 604800 + tow, len(sats), len(l1), len(l2)))
    return epochs, signals, sfrbx


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    path = Path(sys.argv[1])
    epochs, signals, sfrbx = summarise(path)
    size_mb = path.stat().st_size / 1e6
    print(f"File       {path.name}  ({size_mb:.1f} MB)")
    ok = True
    if not epochs:
        print("Raw data   NONE. No RXM-RAWX in this file: the base wasn't outputting raw measurements.")
        sys.exit(1)
    t = [e[0] for e in epochs]
    span = t[-1] - t[0]
    gaps = [(a, b - a) for a, b in zip(t, t[1:]) if b - a > 2.5]
    print(f"Raw data   {len(epochs)} epochs over {int(span // 3600)} h {int(span % 3600 // 60)} min"
          f"  ({size_mb / max(span / 3600, 1e-6):.0f} MB/h)")
    print(f"Gaps       {len(gaps)} longer than 2.5 s" + (f", longest {max(g for _, g in gaps):.0f} s" if gaps else ""))
    steps = sorted(b - a for a, b in zip(t, t[1:]))
    step = steps[len(steps) // 2] if steps else 0
    print(f"Rate       one epoch every {step:.2f} s")
    sats = sorted(e[1] for e in epochs)
    print(f"Satellites median {sats[len(sats) // 2]} per epoch;  measurements by system: "
          + ", ".join(f"{k} {v}" for k, v in signals.most_common()))
    l2_share = sum(1 for e in epochs if e[3] >= 4) / len(epochs)
    print(f"GPS L2     on {l2_share:.0%} of epochs (4+ satellites)")
    print(f"Nav data   {sfrbx} RXM-SFRBX messages" + ("" if sfrbx else "  (none: RINEX nav file will be empty)"))

    if span < 3600:
        print("\n✗ Under 1 h: too short for a dependable CSRS-PPP / OPUS result. Leave the base logging longer.")
        ok = False
    elif span < 2 * 3600:
        print("\n• 1 – 2 h: CSRS-PPP will give a position, but expect cm to dm. 2 h+ is better.")
    elif span < 6 * 3600:
        print("\n✓ 2 h or more: CSRS-PPP (and OPUS static) should give a few cm.")
    else:
        print("\n✓ 6 h or more: CSRS-PPP typically at the cm level or better.")
    if l2_share < 0.8:
        print("✗ Not enough GPS L2 (an L1/L5 ZED-F9P-15B?). CSRS-PPP can still use it; OPUS cannot.")
    if step and abs(step - round(step)) > 0.01:
        print("• Rate isn't a whole number of seconds; convert without convbin -ti (it would drop epochs).")
    if gaps and max(g for _, g in gaps) > 60:
        print("• Long gaps: check the USB cable / laptop sleep. Short sessions either side are still usable.")

    meta = path.with_suffix(".json")
    if meta.exists():
        rec = json.loads(meta.read_text())
        bp = rec.get("base_position")
        print(f"\nSession    {rec.get('started_utc')} → {rec.get('stopped_utc') or 'not stopped cleanly'}, mode {rec.get('mode')}")
        if bp:
            print(f"Base used  {bp['lat']:.9f}, {bp['lon']:.9f}, h {bp['ellipsoid_height_m']:.3f} m (ellipsoid)  "
                  f"from {bp['source']}" + (f", ±{bp['acc_m']:.2f} m" if bp.get("acc_m") else ""))
            print("           Shift for the flights = post-processed antenna position − this.")
        else:
            print("Base used  not recorded (the base never sent its position).")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
