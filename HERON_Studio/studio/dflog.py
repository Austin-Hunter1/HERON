"""ArduPilot DataFlash log (.bin) -> the CSV files the HERON engine reads.

Writes POS, ATT, GPS, IMU, XKF1, RFND, PARM (plus MODE, CMD, EV, MSG when present) as
    record_index, TimeUS, <fields...>
and picks the mission-time zero (TimeUS of the first switch to AUTO; arming or first fix
otherwise). The engine only uses that zero as an offset, so any consistent choice works.
"""
import csv
from pathlib import Path

WANT = ("POS", "ATT", "GPS", "IMU", "XKF1", "RFND", "PARM", "MODE", "CMD", "EV", "MSG", "BAT", "BARO")
REQUIRED = ("POS", "ATT", "GPS", "IMU", "XKF1")
AUTO_MODES = {3}          # ArduCopter AUTO


def convert(bin_path, out_dir, progress=None):
    from pymavlink import DFReader
    log = DFReader.DFReader_binary(str(bin_path), zero_time_base=False)
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    files, writers, counts = {}, {}, {}
    t0_auto = t0_arm = t0_pos = None; mission = []; idx = 0; last_pct = -1
    size = Path(bin_path).stat().st_size
    try:
        while True:
            m = log.recv_match(type=list(WANT))
            if m is None: break
            idx += 1; name = m.get_type()
            if name == "GPS2": name = "GPS"
            if name not in writers:
                cols = [c for c in m.get_fieldnames() if c != "TimeUS"]
                f = open(out_dir / f"{name}.csv", "w", newline=""); files[name] = f
                w = csv.writer(f); w.writerow(["record_index", "TimeUS", *cols]); writers[name] = (w, cols); counts[name] = 0
            w, cols = writers[name]; tus = getattr(m, "TimeUS", None)
            w.writerow([idx, tus, *[_v(getattr(m, c, "")) for c in cols]]); counts[name] += 1
            if name == "MODE" and t0_auto is None and getattr(m, "ModeNum", getattr(m, "Mode", None)) in AUTO_MODES: t0_auto = tus
            elif name == "EV" and t0_arm is None and getattr(m, "Id", None) == 10: t0_arm = tus
            elif name == "POS" and t0_pos is None: t0_pos = tus
            elif name == "CMD": mission.append(dict(seq=m.CNum, cmd=m.CId, lat=m.Lat, lon=m.Lng, alt=m.Alt))
            if progress and idx % 20000 == 0:
                pct = int(100 * log.offset / max(1, size))
                if pct != last_pct: progress(pct); last_pct = pct
    finally:
        for f in files.values(): f.close()
    missing = [k for k in REQUIRED if k not in counts]
    t0 = t0_auto or t0_arm or t0_pos
    return dict(counts=counts, missing=missing, t0_us=t0,
                t0_source="AUTO" if t0_auto else "arming" if t0_arm else "first position",
                mission_items=len(mission))


def _v(x):
    if isinstance(x, bytes): x = x.decode("latin-1", "replace")
    if isinstance(x, str): return x.rstrip("\0")
    return x


# ------------------------------------------------------------------ test fixture
def write_bin(csv_dir, out_path, types=("POS", "ATT", "GPS", "IMU", "XKF1", "RFND", "PARM"), max_rows=None, extra_mode_us=None):
    """Build a DataFlash .bin from engine CSVs (used by the tests to round-trip convert())."""
    import struct
    import pandas as pd
    head = b"\xa3\x95"; out = bytearray(); fmts = {}
    def fmt_msg(tid, name, fmt, cols):
        body = struct.pack("<BB4s16s64s", tid, 3 + struct.calcsize("<" + _pyfmt(fmt)), name.encode(), fmt.encode(), ",".join(cols).encode())
        return head + bytes([128]) + body
    out += head + bytes([128]) + struct.pack("<BB4s16s64s", 128, 89, b"FMT", b"BBnNZ", b"Type,Length,Name,Format,Columns")
    events = []
    for k, name in enumerate(types):
        df = pd.read_csv(Path(csv_dir) / f"{name}.csv")
        if max_rows: df = df.iloc[:max_rows]
        cols = [c for c in df.columns if c not in ("record_index", "TimeUS")]
        fmt = "Q" + "".join("N" if not pd.api.types.is_numeric_dtype(df[c]) else "L" if c in ("Lat", "Lng") else "d" for c in cols)
        tid = 140 + k; fmts[name] = (tid, fmt); out += fmt_msg(tid, name, fmt, ["TimeUS", *cols])
        for row in df.itertuples(index=False):
            r = row._asdict(); vals = [int(r["TimeUS"])]
            for c, f in zip(cols, fmt[1:]):
                v = r[c]
                vals.append(str(v).encode()[:16] if f == "N" else int(round(float(v) * 1e7)) if f == "L" else float(v))
            events.append((vals[0], tid, struct.pack("<" + _pyfmt(fmt), *vals)))
    if extra_mode_us:
        out += fmt_msg(139, "MODE", "QMBB", ["TimeUS", "Mode", "ModeNum", "Rsn"])
        events.append((extra_mode_us, 139, struct.pack("<QBBB", extra_mode_us, 3, 3, 1)))
    for _, tid, body in sorted(events, key=lambda e: e[0]): out += head + bytes([tid]) + body
    Path(out_path).write_bytes(bytes(out))


def _pyfmt(fmt):
    return "".join({"Q": "Q", "d": "d", "L": "i", "N": "16s", "B": "B", "M": "B", "n": "4s", "Z": "64s"}[c] for c in fmt)
