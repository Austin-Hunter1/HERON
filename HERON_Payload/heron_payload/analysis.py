"""Signal-level check for a short test recording (the SURGE gain-sweep method, automated).

The old team picked the gain by recording a few seconds at several gains and looking at a
histogram of the 8-bit samples (`hist8`). GNSS signals sit below the noise floor, so what
matters is that the noise uses a sensible part of the 8-bit range without clipping.
"""
import json
import math
import struct
from pathlib import Path

TARGET_RMS_SC8 = 24.0          # ~ -14 dBFS: room for interference spikes, enough bits for the noise
MAX_CLIP = 0.001               # more than 0.1 % of values at full scale = too much gain
MIN_RMS_SC8 = 6.0              # below this the noise uses fewer than ~3 bits
BINS = 32


def analyse_file(path, file_format, max_values=2_000_000):
    path = Path(path)
    eight = file_format == "sc8"
    size = 1 if eight else 2
    with open(path, "rb") as f:
        raw = f.read(max_values * size)
    n = len(raw) // size
    if n < 2:
        raise ValueError(path.name + " is empty")
    values = struct.unpack("<%d%s" % (n, "b" if eight else "h"), raw[:n * size])
    if not eight:
        values = [v / 256 for v in values]      # report on the same 8-bit scale
    lo, hi = -128, 127
    counts = [0] * BINS
    width = 256 / BINS
    sq = s_i = s_q = clipped = 0.0
    for k, v in enumerate(values):
        sq += v * v
        if k % 2:
            s_q += v
        else:
            s_i += v
        if v <= lo or v >= hi:
            clipped += 1
        counts[min(BINS - 1, int((v - lo) / width))] += 1
    rms = math.sqrt(sq / n)
    clip = clipped / n
    change = round(20 * math.log10(TARGET_RMS_SC8 / max(rms, 0.3)))
    if clip > MAX_CLIP:
        verdict, change = "clipping", min(-3, change - 3)
    elif rms < MIN_RMS_SC8:
        verdict = "too quiet"
    elif change > 3:
        verdict = "low"
    elif change < -3:
        verdict = "high"
    else:
        verdict, change = "good", 0
    return dict(file=path.name, values=n, rms=round(rms, 2), clip_pct=round(100 * clip, 4),
                dc_i=round(2 * s_i / n, 2), dc_q=round(2 * s_q / n, 2), histogram=counts,
                verdict=verdict, gain_change_db=change)


def analyse_session(session_dir):
    """Analyse the first file of every channel in a recording session folder."""
    root = Path(session_dir)
    result = []
    for radio_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        settings = {}
        if (radio_dir / "settings.json").exists():
            settings = json.loads((radio_dir / "settings.json").read_text())
        actual = settings.get("actual_channels") or []
        files = sorted(radio_dir.glob("channel_*_000000.sc*"))
        for n, f in enumerate(files):
            ch = actual[n] if n < len(actual) else {}
            fmt = "sc8" if f.suffix == ".sc8" else "sc16"
            row = analyse_file(f, fmt)
            # The C++ recorder writes settings.json with Boost, which stores every number as a string ("45").
            try:
                gain = float(ch.get("gain_db"))
            except (TypeError, ValueError):
                gain = None
            row.update(radio=radio_dir.name, label=ch.get("label", f"channel {n}"), gain_db=gain,
                       suggested_gain_db=round(gain + row["gain_change_db"], 1) if gain is not None else None)
            result.append(row)
    if not result:
        raise ValueError("No sample files found in " + root.name)
    return result


def list_sessions(storage_path, limit=20):
    root = Path(storage_path)
    if not root.exists():
        return []
    rows = []
    for d in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True)[:limit]:
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        complete = None
        if (d / "completion.json").exists():
            try:
                complete = json.loads((d / "completion.json").read_text()).get("complete")
            except ValueError:
                complete = False
        rows.append(dict(name=d.name, bytes=size, complete=complete))
    return rows
