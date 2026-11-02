"""Validated JSON settings. Operators edit these through the Payload workspace."""
import copy
import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".heron-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _radio(ident, name, model, mhz, channels, rate=22.0, bw=20.322):
    return dict(id=ident, name=name, model=model, serial="", enabled=True, frequency_mhz=mhz,
                sample_rate_msps=rate, bandwidth_mhz=bw,
                channels=[dict(index=i, enabled=True, label=label, port="RX2", gain_db=45.0)
                          for i, label in enumerate(channels)])


def _setup(name, radios, clock="internal"):
    return dict(schema=1, name=name, radios=radios, clock=clock, file_format="sc8", sc8_peak=0.25,
                storage=dict(path="/media/DataStore/heron", require_mount="/media/DataStore",
                             reserve_gb=20.0, segment_seconds=30),
                behavior=dict(fallback=False, grace_seconds=120, stop_on_exit_auto=True,
                              stop_on_disarm=True, cube_loss="continue"))


def presets():
    """Built-in starting points. The first one is the default for a single B210."""
    l5 = _setup("L5 direct + reflected", [
        _radio("b210", "B210", "b210", 1176.45, ["L5 direct (RHCP up)", "L5 reflected (LHCP down)"])])
    surge = _setup("SURGE single antenna", [
        _radio("b210", "B210", "b210", 1176.45, ["L5 LHCP (SURGE)", "unused"])])
    surge["radios"][0]["channels"][0]["enabled"] = False      # SURGE recorded RF B only (subdev A:B)
    surge["radios"][0]["channels"][1].update(enabled=True, label="L5 LHCP (SURGE)")
    surge["radios"][0]["channels"][0]["label"] = "RF A (off)"
    full = _setup("Full payload (4 radios)", [
        _radio("direct_l1", "Direct L1", "b200", 1575.42, ["L1 direct RHCP"]),
        _radio("direct_l5", "Direct L5", "b200", 1176.45, ["L5 direct RHCP"]),
        _radio("reflected_l1", "Reflected L1", "b210", 1575.42, ["L1 reflected RHCP", "L1 reflected LHCP"]),
        _radio("reflected_l5", "Reflected L5", "b210", 1176.45, ["L5 reflected RHCP", "L5 reflected LHCP"]),
    ], clock="external")
    return [l5, surge, full]


def number(value, lo, hi, label):
    if isinstance(value, bool):
        raise ValueError(label + " must be a number.")
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValueError(label + " must be a number.")
    if not math.isfinite(n) or not lo <= n <= hi:
        raise ValueError(f"{label} must be between {lo:g} and {hi:g}.")
    return n


def validate(raw, hardware=False):
    c = copy.deepcopy(raw)
    if isinstance(c, dict):
        c.pop("builtin", None)
    if not isinstance(c, dict) or c.get("schema") != 1:
        raise ValueError("Unsupported payload configuration.")
    if not isinstance(c.get("name"), str) or not 1 <= len(c["name"].strip()) <= 60:
        raise ValueError("Give this setup a name (1–60 characters).")
    if c.get("clock") not in ("internal", "external") or c.get("file_format") not in ("sc8", "sc16"):
        raise ValueError("Choose a clock and sample format.")
    c["sc8_peak"] = number(c.get("sc8_peak", 0.25), 0.01, 1, "8-bit scaling peak")
    radios = c.get("radios")
    if not isinstance(radios, list) or not 1 <= len(radios) <= 16:
        raise ValueError("A setup needs 1–16 radios.")
    seen, serials, labels = set(), set(), set()
    for r in radios:
        if not isinstance(r, dict) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", str(r.get("id", ""))):
            raise ValueError("Invalid radio identifier.")
        if r["id"] in seen:
            raise ValueError("Radio identifiers must be unique.")
        seen.add(r["id"])
        if r.get("model") not in ("b200", "b210") or type(r.get("enabled")) is not bool:
            raise ValueError("Choose B200 or B210 and enable the radios you need.")
        if not isinstance(r.get("name"), str) or not 1 <= len(r["name"]) <= 60:
            raise ValueError("Each radio needs a name.")
        serial = r.get("serial", "").strip()
        if serial and not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", serial):
            raise ValueError("Serial numbers may contain letters, numbers, underscores and dashes.")
        r["serial"] = serial
        if r["enabled"]:
            if hardware and not serial:
                raise ValueError(r["name"] + ": assign a detected radio serial first.")
            if serial and serial in serials:
                raise ValueError("Two enabled radios cannot share a serial number.")
            if serial:
                serials.add(serial)
        r["frequency_mhz"] = number(r.get("frequency_mhz"), 70, 6000, r["name"] + " frequency (MHz)")
        r["sample_rate_msps"] = number(r.get("sample_rate_msps"), .2, 61.44, "Sample rate (MS/s)")
        r["bandwidth_mhz"] = number(r.get("bandwidth_mhz"), .2, 56, "Bandwidth (MHz)")
        channels = r.get("channels")
        if not isinstance(channels, list) or len(channels) != (2 if r["model"] == "b210" else 1):
            raise ValueError("A B200 has one RX channel; a B210 has two.")
        for i, ch in enumerate(channels):
            if ch.get("index") != i or type(ch.get("enabled")) is not bool:
                raise ValueError("Invalid channel selection.")
            if ch.get("port") not in ("RX2", "TX/RX"):
                raise ValueError("Choose RX2 or TX/RX for each antenna input.")
            if not isinstance(ch.get("label"), str) or not 1 <= len(ch["label"].strip()) <= 60:
                raise ValueError("Each channel needs an antenna label.")
            ch["gain_db"] = number(ch.get("gain_db"), 0, 76, "Gain (dB)")
            if r["enabled"] and ch["enabled"]:
                if ch["label"] in labels:
                    raise ValueError("Enabled channels need distinct antenna labels.")
                labels.add(ch["label"])
        active = sum(ch["enabled"] for ch in channels)
        if r["enabled"] and not active:
            raise ValueError(r["name"] + ": enable at least one channel, or disable the radio.")
        if r["enabled"] and active == 2 and r["sample_rate_msps"] > 30.72:
            raise ValueError("Two-channel B210 setups are limited to 30.72 MS/s per channel.")
        if r["enabled"] and r["bandwidth_mhz"] > r["sample_rate_msps"]:
            raise ValueError(r["name"] + ": analog bandwidth should not exceed the sample rate.")
    if not any(r["enabled"] for r in radios):
        raise ValueError("Enable at least one radio.")
    s = c.get("storage", {})
    if not isinstance(s.get("path"), str) or not Path(s["path"]).is_absolute():
        raise ValueError("Storage folder must be an absolute path on the NUC.")
    # Recording under /media/<disk>/... requires that disk to be mounted (so a missing SSD never fills the
    # system drive). Any other folder (e.g. /home/heron/recordings for bench tests) has no mount requirement.
    parts = Path(s["path"]).parts
    s["require_mount"] = str(Path(*parts[:3])) if len(parts) >= 3 and parts[1] in ("media", "mnt") else ""
    s["reserve_gb"] = number(s.get("reserve_gb"), 1, 10000, "Disk reserve (GB)")
    s["segment_seconds"] = int(number(s.get("segment_seconds"), 1, 3600, "File duration (seconds)"))
    b = c.get("behavior", {})
    for key in ("fallback", "stop_on_exit_auto", "stop_on_disarm"):
        if type(b.get(key)) is not bool:
            raise ValueError("Invalid automatic recording settings.")
    b["grace_seconds"] = number(b.get("grace_seconds"), 10, 3600, "Fallback delay")
    if b.get("cube_loss") not in ("continue", "stop"):
        raise ValueError("Choose what happens if the Cube connection is lost.")
    c.update(storage=s, behavior=b)
    return c


def estimate(c):
    enabled = [r for r in c["radios"] if r["enabled"]]
    samples = sum(r["sample_rate_msps"] * sum(ch["enabled"] for ch in r["channels"]) for r in enabled)
    mbps = samples * (2 if c["file_format"] == "sc8" else 4)
    return dict(radios=len(enabled), channels=sum(sum(ch["enabled"] for ch in r["channels"]) for r in enabled),
                disk_mbps=round(mbps, 3), usb_mbps=round(samples * 4, 3), gb_per_min=round(mbps * .06, 3))


class ProfileStore:
    def __init__(self, path):
        self.path = Path(path)
        self.entries = {p["name"]: p for p in presets()}
        if self.path.exists():
            for p in json.loads(self.path.read_text()).get("profiles", []):
                p = validate(p)
                self.entries[p["name"]] = p

    def save(self, config):
        config = validate(config)
        if config["name"] not in self.entries and len(self.entries) >= 50:
            raise ValueError("At most 50 saved setups are supported.")
        entries = dict(self.entries, **{config["name"]: config})
        atomic_json(self.path, {"profiles": list(entries.values())})
        self.entries = entries
        return copy.deepcopy(config)

    def delete(self, name):
        if name in {p["name"] for p in presets()}:
            raise ValueError("Built-in setups cannot be deleted; save a copy under a new name instead.")
        if name not in self.entries:
            raise ValueError("No saved setup named " + str(name))
        entries = {k: v for k, v in self.entries.items() if k != name}
        atomic_json(self.path, {"profiles": [p for p in entries.values() if p["name"] not in {q["name"] for q in presets()}]})
        self.entries = entries

    def list(self):
        builtin = {p["name"] for p in presets()}
        return [dict(copy.deepcopy(p), builtin=p["name"] in builtin) for p in self.entries.values()]
