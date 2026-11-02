"""Browse recordings on the NUC for the Payload Setup page.

* list_sessions: every HERON_* session on the data drive and on the NUC SSD, with length and size.
* sample_channel: a few hundred short slices spread across one channel, sent to the browser, which
  draws the spectrogram and histogram itself (so the NUC needs no plotting libraries and only ~1 MB
  is read from disk per picture).
* file_path: a safe path to one file for download.
"""
import base64
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import storage

SESSION_RE = re.compile(r"^HERON_\d{8}_\d{6}(_\d+)?$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,120}$")
SAMPLE_RE = re.compile(r"^channel_(\d+)_(\d{6})\.(sc8|sc16)$")
DOWNLOADABLE = (".sc8", ".sc16", ".json", ".jsonl")
BYTES_PER_SAMPLE = {"sc8": 2, "sc16": 4}       # one complex sample = I + Q


def _num(value, default=None):
    try:
        v = float(value)
        return v if v == v else default
    except (TypeError, ValueError):
        return default


def _json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def session_time(name):
    try:
        return datetime.strptime(name[6:21], "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def describe_radio(folder):
    folder = Path(folder)
    settings = _json(folder / "settings.json")
    actual = settings.get("actual_channels") or []
    if not isinstance(actual, list):
        actual = []
    files, other = {}, []
    fmt = None
    for f in sorted(folder.iterdir()):
        if not f.is_file():
            continue
        m = SAMPLE_RE.match(f.name)
        if m:
            fmt = m.group(3)
            files.setdefault(int(m.group(1)), []).append(dict(name=f.name, segment=int(m.group(2)), bytes=f.stat().st_size))
        elif f.suffix in DOWNLOADABLE:
            other.append(dict(name=f.name, bytes=f.stat().st_size))
    first = actual[0] if actual and isinstance(actual[0], dict) else {}
    rate = _num(settings.get("sample_rate_msps"))
    rate = rate * 1e6 if rate else _num(first.get("sample_rate_hz"))
    freq = _num(settings.get("frequency_mhz"))
    freq = freq * 1e6 if freq else _num(first.get("frequency_hz"))
    bps = BYTES_PER_SAMPLE.get(fmt or "sc8")
    channels = []
    for n in sorted(files):
        segs = sorted(files[n], key=lambda s: s["segment"])
        info = actual[n] if n < len(actual) and isinstance(actual[n], dict) else {}
        for s in segs:
            s["seconds"] = round(s["bytes"] / bps / rate, 3) if rate else None
        total = sum(s["bytes"] for s in segs)
        channels.append(dict(n=n, label=info.get("label") or f"channel {n}", gain_db=_num(info.get("gain_db")),
                             port=info.get("port"), segments=segs, bytes=total,
                             seconds=round(total / bps / rate, 3) if rate else None))
    return dict(id=folder.name, name=settings.get("name") or folder.name,
                model=settings.get("device_model") or settings.get("model"), serial=settings.get("serial") or "",
                format=fmt, rate_hz=rate, freq_hz=freq, channels=channels, files=other)


def _faults(completion):
    radios = ((completion.get("status") or {}).get("radios")) or []
    overflows = sum(int(_num(r.get("overflows"), 0)) for r in radios)
    gaps = sum(int(_num(r.get("gaps"), 0)) for r in radios)
    return overflows, gaps


def summarize(session_dir, root_drive):
    d = Path(session_dir)
    completion = _json(d / "completion.json") if (d / "completion.json").exists() else None
    meta = _json(d / "session.json")
    radios = [describe_radio(r) for r in sorted(p for p in d.iterdir() if p.is_dir())]
    size = sum(c["bytes"] for r in radios for c in r["channels"]) + \
        sum(f["bytes"] for r in radios for f in r["files"])
    lengths = [c["seconds"] for r in radios for c in r["channels"] if c["seconds"] is not None]
    overflows, gaps = _faults(completion or {})
    return dict(name=d.name, started=session_time(d.name), folder=str(d), drive=root_drive,
                setup=(meta.get("config") or {}).get("name"), simulated=bool(meta.get("simulated")),
                mission=bool(meta.get("mission")), bytes=size, seconds=max(lengths) if lengths else 0,
                complete=None if completion is None else bool(completion.get("complete")),
                errors=(completion or {}).get("errors") or [], overflows=overflows, gaps=gaps, radios=radios)


def list_sessions(roots, limit=200):
    seen, found = set(), []
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        drive = storage.drive_label(root)
        for d in root.iterdir():
            if d.is_dir() and SESSION_RE.match(d.name) and d.name not in seen:
                seen.add(d.name)
                found.append((d, drive))
    found.sort(key=lambda x: x[0].name, reverse=True)
    rows = []
    for d, drive in found[:limit]:
        try:
            rows.append(summarize(d, drive))
        except OSError:
            continue
    return rows


def find_session(roots, name):
    if not isinstance(name, str) or not SESSION_RE.match(name):
        raise ValueError("Unknown recording.")
    for root in roots:
        d = Path(root) / name
        if d.is_dir():
            return d
    raise ValueError("Recording " + name + " was not found.")


def _radio_dir(session_dir, radio):
    if not isinstance(radio, str) or not NAME_RE.match(radio) or not (Path(session_dir) / radio).is_dir():
        raise ValueError("Unknown radio in this recording.")
    return Path(session_dir) / radio


def sample_channel(session_dir, radio, channel, start_s=0.0, span_s=None, rows=240, nfft=512, avg=4):
    """Evenly spaced slices of nfft*avg samples from one channel, for drawing in the browser."""
    info = describe_radio(_radio_dir(session_dir, radio))
    ch = next((c for c in info["channels"] if c["n"] == channel), None)
    if ch is None or not ch["segments"]:
        raise ValueError("That channel has no sample files.")
    if not info["rate_hz"]:
        raise ValueError("settings.json is missing the sample rate.")
    rate, bps = info["rate_hz"], BYTES_PER_SAMPLE[info["format"]]
    rows = int(min(max(rows, 1), 600))
    nfft = 512 if nfft not in (128, 256, 512, 1024, 2048) else nfft
    avg = int(min(max(avg, 1), 16))
    block = nfft * avg
    counts = [s["bytes"] // bps for s in ch["segments"]]
    total = sum(counts)
    start = int(min(max(_num(start_s, 0) * rate, 0), max(total - block, 0)))
    end = total if not span_s else int(min(total, start + max(_num(span_s, 0), 0) * rate))
    if end - start < block:
        start, end = max(0, end - block), max(end, min(total, block))
    rows = max(1, min(rows, max(1, (end - start) // block)))
    step = (end - start - block) / (rows - 1) if rows > 1 else 0
    folder = Path(session_dir) / radio
    chunks, times = [], []
    for i in range(rows):
        pos = int(start + i * step)
        k, before = 0, 0
        while k < len(counts) - 1 and pos >= before + counts[k]:
            before += counts[k]
            k += 1
        offset = min(pos - before, max(counts[k] - block, 0))
        with open(folder / ch["segments"][k]["name"], "rb") as f:
            f.seek(offset * bps)
            data = f.read(block * bps)
        chunks.append(data.ljust(block * bps, b"\0"))
        times.append(round((before + offset) / rate, 4))
    return dict(format=info["format"], rate_hz=rate, freq_hz=info["freq_hz"], label=ch["label"],
                gain_db=ch["gain_db"], radio=radio, channel=channel, rows=rows, nfft=nfft, avg=avg,
                row_samples=block, times=times, total_seconds=round(total / rate, 3),
                start_s=round(start / rate, 3), span_s=round((end - start) / rate, 3),
                data=base64.b64encode(b"".join(chunks)).decode())


# One samples.jsonl row, as the recorder writes it (recorder/main.cpp, SegmentFiles::write). A regex is about
# three times faster than json.loads, and a 30 min recording has millions of rows.
ROW_RE = re.compile(rb'"sample_offset":(\d+),"count":(\d+),"device_time":([-0-9.eE+]+),'
                    rb'"segment":(\d+),"segment_offset":(\d+),"uhd_error":(-?\d+)')
MAX_DROPS_LISTED = 500
_drop_cache = {}                     # (path, size, mtime_ns) -> result; a finished recording never changes


def find_drops(session_dir, radio):
    """Where samples are missing in one radio's recording, from its samples.jsonl.

    Each row is one block the recorder wrote: its first sample's offset in the recording, its sample count and
    the device time of its first sample. Without loss, each block starts exactly where the one before ended. A
    later start is a drop: the device or USB lost (start - expected) x rate samples, and the recorder wrote the
    next block straight after the last one, with no padding. So a drop sits just before `segment_offset` in the
    file of `segment`, and `at_s` (recorded samples / rate) is its place on the spectrogram time axis.
    A row with a UHD error code but no time jump is listed too, with 0 missing samples.
    """
    folder = _radio_dir(session_dir, radio)
    path = folder / "samples.jsonl"
    info = describe_radio(folder)
    rate = info["rate_hz"]
    if not rate:
        raise ValueError("settings.json is missing the sample rate.")
    if not path.is_file():
        raise ValueError("This radio has no samples.jsonl, so drops cannot be located.")
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key in _drop_cache:
        return _drop_cache[key]
    tolerance = 1.5 / rate           # the recorder's own gap test
    drops, total, missing, rows, bad_rows = [], 0, 0, 0, 0
    first_time = expected = None
    end_offset = 0
    with open(path, "rb") as f:
        for line in f:
            m = ROW_RE.search(line)
            if not m:
                bad_rows += 1
                continue
            offset, count, t, segment, seg_offset, error = (int(m[1]), int(m[2]), float(m[3]), int(m[4]),
                                                            int(m[5]), int(m[6]))
            rows += 1
            if first_time is None:
                first_time = t
            lost = 0 if expected is None else int(round((t - expected) * rate))
            jump = expected is not None and abs(t - expected) > tolerance
            if jump or error:
                total += 1
                missing += max(lost, 0)
                if len(drops) < MAX_DROPS_LISTED:
                    drops.append(dict(at_s=round(offset / rate, 6), device_s=round(t - first_time, 6),
                                      sample=offset, segment=segment, segment_offset=seg_offset,
                                      missing=lost if jump else 0, missing_ms=round(lost / rate * 1e3, 3) if jump else 0,
                                      uhd_error=error, kind="time went back" if jump and lost < 0 else
                                      "samples lost" if jump else "UHD error"))
            expected = t + count / rate
            end_offset = offset + count
    result = dict(radio=radio, rate_hz=rate, format=info["format"], rows=rows, unreadable_rows=bad_rows,
                  drops=drops, total=total, listed=len(drops), missing_samples=missing,
                  missing_s=round(missing / rate, 6), recorded_s=round(end_offset / rate, 3),
                  channels=[dict(n=c["n"], label=c["label"]) for c in info["channels"]])
    if len(_drop_cache) > 64:
        _drop_cache.clear()
    _drop_cache[key] = result
    return result


def file_path(session_dir, radio, name):
    session_dir = Path(session_dir)
    folder = _radio_dir(session_dir, radio) if radio else session_dir
    if not isinstance(name, str) or not NAME_RE.match(name) or not name.endswith(DOWNLOADABLE):
        raise ValueError("That file cannot be downloaded.")
    path = folder / name
    if not path.is_file() or path.resolve().parent != folder.resolve():
        raise ValueError("File not found.")
    return path
