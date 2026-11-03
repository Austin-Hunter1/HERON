"""GoPro GPMF telemetry: pull the gyro and accelerometer out of an MP4.

The camera stores telemetry as a data track ("GoPro MET", codec tag gpmd). Each packet is about
one second of KLV records: 4-char key, 1-byte type, 1-byte struct size, 2-byte repeat count,
payload padded to 4 bytes. Type 0 means the payload is more KLV (DEVC > STRM > ...).

Output matches what the HERON engine reads (data/<gopro_dir>/GYRO.csv, ACCL.csv):
    packet, nominal_video_time_s, component_0, component_1, component_2
in the HERO7 sample order. Cameras write an ORIN string naming the camera axis held by each
column (lowercase = negated); HERO7 writes "YxZ". Other cameras are re-ordered to match, so the
engine's fixed camera-to-IMU rotation still applies.
"""
import json, struct, subprocess
from pathlib import Path
import numpy as np

_TYPES = {"b": "b", "B": "B", "c": "c", "d": "d", "f": "f", "F": "4s", "j": "q", "J": "Q",
          "l": "i", "L": "I", "s": "h", "S": "H", "q": "i", "Q": "q", "U": "16s"}
HERO7_ORDER = "YxZ"


def _klv(buf, off=0, end=None):
    end = len(buf) if end is None else end
    while off + 8 <= end:
        key = buf[off:off + 4].decode("latin-1"); typ = chr(buf[off + 4]); size = buf[off + 5]
        rep = struct.unpack(">H", buf[off + 6:off + 8])[0]; n = size * rep
        start = off + 8; off = start + ((n + 3) & ~3)
        if key == "\0\0\0\0": break
        yield key, typ, size, rep, start, start + n


def _values(buf, typ, size, rep, a, b):
    if typ == "c": return buf[a:b].decode("latin-1").rstrip("\0")
    f = _TYPES.get(typ)
    if f is None: return None
    w = struct.calcsize(">" + f); per = max(1, size // w)
    v = np.array(struct.unpack(">" + f * (per * rep), buf[a:a + w * per * rep]))
    if typ in "qQ": v = v / (65536.0 if typ == "q" else 4294967296.0)
    return v.reshape(rep, per) if per > 1 else v


def _streams(payload):
    """Yield dicts {key: (typ,size,rep,a,b)} for each STRM in a packet, plus device info."""
    for key, typ, size, rep, a, b in _klv(payload):
        if key != "DEVC" or typ != "\0": continue
        dev = {}
        for k2, t2, s2, r2, a2, b2 in _klv(payload, a, b):
            if k2 == "STRM" and t2 == "\0":
                yield dev, {k3: (t3, s3, r3, a3, b3) for k3, t3, s3, r3, a3, b3 in _klv(payload, a2, b2)}
            elif t2 == "c":
                dev[k2] = payload[a2:b2].decode("latin-1").rstrip("\0")


def probe_packets(mp4, stream_index):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", str(stream_index), "-show_entries",
                          "packet=pts_time,duration_time,size", "-of", "json", str(mp4)],
                         capture_output=True, text=True, check=True).stdout
    return [(float(p["pts_time"]), float(p.get("duration_time") or 1.001), int(p["size"]))
            for p in json.loads(out)["packets"]]


def read_raw(mp4, stream_index):
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp4), "-map", f"0:{stream_index}", "-c", "copy",
                           "-f", "data", "-"], capture_output=True, check=True).stdout


def extract(mp4, stream_index, out_dir, keys=("GYRO", "ACCL")):
    """Write <key>.csv for each sensor; returns a summary dict (rates, camera model, lens mode...)."""
    return extract_parts([(mp4, stream_index, 0.0)], out_dir, keys)


def extract_parts(parts, out_dir, keys=("GYRO", "ACCL")):
    """Several chapters of one recording: [(mp4, stream_index, start_offset_s), ...]. Each chapter's
    telemetry clock starts at 0, so samples are shifted by the chapter's start in the joined video."""
    rows = {k: [] for k in keys}; info = {"orin": None, "chapters": len(parts)}; pkt0 = 0
    for mp4, stream_index, t_off in parts:
        n = _extract_one(mp4, stream_index, keys, rows, info, t_off, pkt0); pkt0 += n
    return _write(rows, info, out_dir)


def _extract_one(mp4, stream_index, keys, rows, info, t_off, pkt0):
    pk = probe_packets(mp4, stream_index); raw = read_raw(mp4, stream_index)
    off = 0
    for i, (pts, dur, size) in enumerate(pk):
        payload = raw[off:off + size]; off += size
        for dev, s in _streams(payload):
            if "MINF" in dev: info["model"] = dev["MINF"]
            if "DVNM" in dev: info.setdefault("device", dev["DVNM"])
            for k, v in s.items():
                if k in ("VFOV", "EISE", "EISA", "MINF") and v[0] == "c":
                    info[k.lower()] = payload[v[3]:v[4]].decode("latin-1").rstrip("\0")
            key = next((k for k in keys if k in s), None)
            if key is None: continue
            data = _values(payload, *s[key]).astype(float)
            if data.ndim == 1: data = data.reshape(-1, 3)
            scal = _values(payload, *s["SCAL"]).astype(float).ravel() if "SCAL" in s else np.ones(1)
            data = data / (scal if scal.size == data.shape[1] else scal[0])
            if "ORIN" in s:
                orin = payload[s["ORIN"][3]:s["ORIN"][4]].decode("latin-1").rstrip("\0")
                info["orin"] = orin; data = to_hero7_order(data, orin)
            n = len(data); t = t_off + pts + np.arange(n) * dur / n
            rows[key].append(np.c_[np.full(n, pkt0 + i), t, data])
    return len(pk)


def _write(rows, info, out_dir):
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True); summary = dict(info)
    for k, r in rows.items():
        if not r: continue
        a = np.vstack(r)
        np.savetxt(out_dir / f"{k}.csv", a, delimiter=",", fmt=["%d", "%.6f", "%.9g", "%.9g", "%.9g"],
                   header="packet,nominal_video_time_s,component_0,component_1,component_2", comments="")
        summary[k] = dict(samples=len(a), rate_hz=round((len(a) - 1) / max(1e-9, a[-1, 1] - a[0, 1]), 1))
    return summary


def to_hero7_order(data, orin):
    """Re-order columns from this camera's ORIN to HERO7's ("YxZ")."""
    if len(orin) != data.shape[1] or orin == HERO7_ORDER or sorted(orin.upper()) != ["X", "Y", "Z"]: return data
    cols = []
    for h in HERO7_ORDER:
        j = orin.upper().index(h.upper())
        sign = (-1 if orin[j].islower() else 1) * (-1 if h.islower() else 1)
        cols.append(data[:, j] * sign)
    return np.c_[tuple(cols)]
