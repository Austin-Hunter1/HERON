#!/usr/bin/env python3
"""Convert an ArduPilot dataflash log (.bin) to one CSV per message type.

    .venv/bin/python scripts/bin_to_csv.py "path/to/2026-11-09 10-20-08.bin" [--out folder]

Output folder (default: next to the log, named after it) gets:
    GPS.csv, ATT.csv, IMU.csv, RCOU.csv, BAT.csv, ...   one per message type, every field
    PARM.csv                                            all parameters (name, value)
    MSG.csv, MODE.csv, EV.csv, ERR.csv                  text messages, mode changes, events, errors
    README.txt                                          what's in each file: rows, rate, columns, units

Every CSV starts with:
    time_s      seconds since the autopilot booted (TimeUS / 1e6)
    utc         UTC time of the row, from the GPS clock (empty before the first GPS fix)
Multi-instance messages (two GPSs, two IMUs, two batteries...) keep their instance column (I or
Instance), so filter on it.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pymavlink import DFReader

GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)
LEAP_S = 18                               # GPS - UTC since 2017
SKIP = {"FMT", "FMTU", "UNIT", "MULT"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    if not a.log.exists():
        sys.exit(f"No such file: {a.log}")
    out = a.out or a.log.with_name(a.log.stem.replace(" ", "_") + "_csv")
    out.mkdir(parents=True, exist_ok=True)

    log = DFReader.DFReader_binary(str(a.log), zero_time_base=False)
    units = {}                            # (type, field) -> unit, from the log's own FMTU/UNIT
    writers, files, counts, first_t, last_t = {}, {}, defaultdict(int), {}, {}
    gps_offset = None                     # utc_seconds - time_s
    print(f"Reading {a.log.name} ...", flush=True)
    while True:
        m = log.recv_msg()
        if m is None:
            break
        t = m.get_type()
        if t in SKIP or t == "BAD_DATA":
            continue
        d = m.to_dict()
        d.pop("mavpackettype", None)
        tus = d.get("TimeUS")
        ts = tus / 1e6 if isinstance(tus, (int, float)) else None
        if t == "GPS" and gps_offset is None and d.get("GWk", 0) > 0 and d.get("Status", 0) >= 3 and ts is not None:
            utc = GPS_EPOCH + timedelta(weeks=d["GWk"], milliseconds=d["GMS"]) - timedelta(seconds=LEAP_S)
            gps_offset = utc.timestamp() - ts
        if t not in writers:
            f = open(out / f"{t}.csv", "w", newline="")
            cols = [c for c in d if c != "TimeUS"]
            w = csv.DictWriter(f, fieldnames=["time_s", "utc"] + cols, extrasaction="ignore")
            w.writeheader()
            writers[t], files[t] = w, f
            try:
                fmt = log.formats[m.fmt.type] if hasattr(m, "fmt") else None
                if fmt is not None and getattr(fmt, "units", None):
                    for c, u in zip(fmt.columns, fmt.units):
                        if u:
                            units[(t, c)] = u
            except Exception:
                pass
        row = {k: v for k, v in d.items() if k != "TimeUS"}
        row["time_s"] = f"{ts:.6f}" if ts is not None else ""
        row["utc"] = (datetime.fromtimestamp(ts + gps_offset, timezone.utc).isoformat(timespec="milliseconds")
                      if ts is not None and gps_offset is not None else "")
        for k, v in row.items():
            if isinstance(v, bytes):
                row[k] = v.decode(errors="replace").rstrip("\x00")
        writers[t].writerow(row)
        counts[t] += 1
        if ts is not None:
            first_t.setdefault(t, ts)
            last_t[t] = ts
    for f in files.values():
        f.close()

    # rows written before the first GPS fix have no utc yet: fill them now that the offset is known
    if gps_offset is not None:
        for t in counts:
            p = out / f"{t}.csv"
            rows = list(csv.reader(open(p, newline="")))
            if len(rows) > 1 and any(r[1] == "" and r[0] for r in rows[1:]):
                for r in rows[1:]:
                    if r[0] and not r[1]:
                        r[1] = datetime.fromtimestamp(float(r[0]) + gps_offset, timezone.utc).isoformat(timespec="milliseconds")
                csv.writer(open(p, "w", newline="")).writerows(rows)

    lines = [f"{a.log.name} -> one CSV per message type", "",
             "time_s = seconds since boot; utc = from the GPS clock (GPS - 18 s leap seconds).",
             "Units come from the log's own unit table where it has one.", "",
             f"{'File':<14}{'Rows':>9}{'Rate Hz':>9}  Columns"]
    for t in sorted(counts, key=lambda k: -counts[k]):
        span = last_t.get(t, 0) - first_t.get(t, 0)
        rate = counts[t] / span if span > 0 else 0
        cols = writers[t].fieldnames[2:]
        cu = ", ".join(f"{c}[{units[(t, c)]}]" if (t, c) in units else c for c in cols)
        lines.append(f"{t + '.csv':<14}{counts[t]:>9}{rate:>9.1f}  {cu}")
    (out / "README.txt").write_text("\n".join(lines) + "\n")
    total = sum(counts.values())
    print(f"Wrote {len(counts)} CSVs, {total:,} rows -> {out}")
    print("Most useful: GPS, POS (fused position), ATT (attitude), XKF1 (EKF), RCOU (motors), BAT, "
          "CTUN (altitude/throttle), IMU, MODE, CMD (mission items), PARM")


if __name__ == "__main__":
    main()
