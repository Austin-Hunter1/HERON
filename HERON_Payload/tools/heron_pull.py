# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6"]
# ///
"""Download the most recent recordings from the NUC into a collects folder on this computer.

Run on the laptop (Windows, macOS or Linux):

    uv run tools/heron_pull.py                         # newest finished session -> D:\\01_Data\\collects
    uv run tools/heron_pull.py --list                  # show the sessions on the NUC
    uv run tools/heron_pull.py --count 3               # the three newest
    uv run tools/heron_pull.py --session HERON_20261102_170501_123456 --experiment 20261102_Reflection_Test

It uses the Payload Setup web API (the same page as http://<nuc>:8080), so it needs no SSH password. The NUC
refuses downloads while it records or is armed; stop first.

Layout, the same as the collect wizard's experiments (for example 20261019_Flight_Test_Logging):

    <dest>/<experiment>/                      experiment = <UTC date>_HERON_Payload unless --experiment
        <collect>.dat                         one file per channel: the 30 s segments joined in order
        <collect>.yml                         wizard-style sidecar: radio profile, gain, antenna, drops, ...
        metadata.yml                          the processing team's schema (D-022); new collections are added
        nuc/<session>/...                     session.json, completion.json, and each radio's settings.json
                                              and samples.jsonl, as they were on the NUC

<collect> is <UTC start YYYYMMDD_HHMMSS>_<band>_<antenna label>, so the collection id in metadata.yml starts
with the UTC time, which 02-navigation.ipynb needs (D-030). The samples are interleaved I,Q int8 (sc8) or int16
(sc16), little-endian. Dropped samples are not padded: see "drops" in the .yml and nuc/.../samples.jsonl.
A session that is already in the experiment folder is skipped.
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_NUC = "http://172.27.16.140:8080"
DEFAULT_DEST = r"D:\01_Data\collects"
# Band names for metadata.yml (the processing tools key bands by name). Other centres get "F<MHz>".
BANDS = {1575.42e6: "L1", 1227.60e6: "L2", 1176.45e6: "L5", 1561.098e6: "B1I", 1207.14e6: "E5b"}
CHUNK = 4 << 20


class Nuc:
    """The NUC's Payload Setup API. Tests replace it with a fake that has the same two methods."""

    def __init__(self, base, timeout=30):
        self.base, self.timeout = base.rstrip("/"), timeout

    def get_json(self, path, **query):
        url = self.base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise RuntimeError(_api_error(e)) from None

    def stream(self, path, **query):
        """Yield the body of one file in chunks."""
        url = self.base + path + "?" + urllib.parse.urlencode(query)
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as r:
                while True:
                    block = r.read(CHUNK)
                    if not block:
                        return
                    yield block
        except urllib.error.HTTPError as e:
            raise RuntimeError(_api_error(e)) from None


def _api_error(e):
    try:
        return json.loads(e.read()).get("error") or str(e)
    except ValueError:
        return str(e)


def _num(value, default=None):
    # settings.json from the C++ recorder stores numbers as strings ("45").
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_") or "x"


def band_name(freq_hz):
    for f, name in BANDS.items():
        if freq_hz and abs(freq_hz - f) < 1e3:
            return name
    return "F%.3f" % (freq_hz / 1e6) if freq_hz else "unknown"


def session_start(name):
    """HERON_YYYYMMDD_HHMMSS[_ffffff] is the UTC start (controller.py)."""
    try:
        return datetime.strptime(name[6:21], "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def plan_session(session, experiment):
    """What one session becomes on disk: one collect per channel. Pure, so it is easy to test."""
    start = session_start(session["name"])
    stamp = start.strftime("%Y%m%d_%H%M%S") if start else session["name"]
    collects, used = [], set()
    for radio in session["radios"]:
        band = band_name(radio.get("freq_hz"))
        for ch in radio["channels"]:
            label = slug(ch["label"])
            if label.startswith(band.lower() + "_"):        # "L5 direct" -> "direct", not "l5_l5_direct"
                label = label[len(band) + 1:]
            name = f"{stamp}_{band.lower()}_{label}"
            if name in used:                               # two radios with the same label and band
                name += "_" + slug(radio["id"])
            used.add(name)
            fmt = radio.get("format") or "sc8"
            rate = radio.get("rate_hz")
            seconds = ch.get("seconds") or (ch["bytes"] / (2 if fmt == "sc8" else 4) / rate if rate else 0)
            collects.append(dict(
                collect=name, radio=radio["id"], channel=ch["n"], label=ch["label"], band=band, format=fmt,
                rate_hz=rate, freq_hz=radio.get("freq_hz"), gain_db=ch.get("gain_db"), port=ch.get("port"),
                serial=radio.get("serial"), model=radio.get("model"), segments=[s["name"] for s in ch["segments"]],
                bytes=ch["bytes"], seconds=seconds, start=start,
                collection_id=f"{name}_{round(seconds)}s",
                channel_config=f"{slug(radio['id'])}_{band.lower()}_{_rate_tag(rate)}"))
    return dict(session=session["name"], experiment=experiment, collects=collects)


def _rate_tag(rate):
    return ("%gmsps" % (rate / 1e6)).replace(".", "p") if rate else "rate_unknown"


def download_session(nuc, session, dest, experiment, report=print):
    """Download one session into <dest>/<experiment>. Returns the list of collect names written."""
    plan = plan_session(session, experiment)
    exp_dir = Path(dest) / experiment
    raw_dir = exp_dir / "nuc" / session["name"]
    if (raw_dir / "pulled.json").exists():
        report(f"{session['name']}: already in {exp_dir}, skipped")
        return []
    exp_dir.mkdir(parents=True, exist_ok=True)
    # The NUC's own JSON files first, for traceability and for the drop list.
    for name in ("session.json", "completion.json"):
        _save(nuc, raw_dir / name, session=session["name"], name=name)
    settings = {}
    for radio in session["radios"]:
        for f in radio.get("files", []):
            _save(nuc, raw_dir / radio["id"] / f["name"], session=session["name"], radio=radio["id"], name=f["name"])
        try:
            settings[radio["id"]] = json.loads((raw_dir / radio["id"] / "settings.json").read_text())
        except (OSError, ValueError):
            settings[radio["id"]] = {}
    drops = {r["id"]: _drops(raw_dir / r["id"] / "samples.jsonl", r.get("rate_hz")) for r in session["radios"]}
    for c in plan["collects"]:
        dat = exp_dir / (c["collect"] + ".dat")
        part = dat.with_suffix(".dat.part")
        report(f"{session['name']}: {c['label']} -> {dat.name} ({c['bytes'] / 1e9:.2f} GB, "
               f"{len(c['segments'])} segment(s))")
        written = 0
        with open(part, "wb") as out:
            for seg in c["segments"]:          # the segments in order, joined into one file like the wizard's
                for block in nuc.stream("/api/recordings/file", session=session["name"], radio=c["radio"], name=seg):
                    out.write(block)
                    written += len(block)
        if written != c["bytes"]:
            raise RuntimeError(f"{dat.name}: got {written} bytes, the NUC listed {c['bytes']}")
        part.replace(dat)
        _write_yaml(exp_dir / (c["collect"] + ".yml"), _sidecar(c, session, settings[c["radio"]],
                                                              drops[c["radio"]], experiment))
    _update_metadata(exp_dir / "metadata.yml", plan, settings, drops)
    (raw_dir / "pulled.json").write_text(json.dumps(dict(
        pulled=datetime.now(timezone.utc).isoformat(), collects=[c["collect"] for c in plan["collects"]]), indent=2))
    return [c["collect"] for c in plan["collects"]]


def _save(nuc, path, **query):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "wb") as out:          # samples.jsonl can be hundreds of MB: stream it
            for block in nuc.stream("/api/recordings/file", **query):
                out.write(block)
    except RuntimeError:
        path.unlink(missing_ok=True)           # completion.json is missing while a session is unfinished


def _drops(jsonl, rate):
    """Count drops in a samples.jsonl the same way the NUC's "Find drops" does (recordings.find_drops)."""
    if not rate or not jsonl.exists():
        return None
    total = missing = 0
    expected = None
    with open(jsonl, "rb") as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            t = float(row["device_time"])
            if expected is not None and abs(t - expected) > 1.5 / rate:
                total += 1
                missing += max(0, round((t - expected) * rate))
            elif row.get("uhd_error"):
                total += 1
            expected = t + int(row["count"]) / rate
    return dict(total=total, missing_samples=missing, missing_ms=round(missing / rate * 1e3, 3))


def _sidecar(c, session, settings, drops, experiment):
    """A per-collect YAML with the collect wizard's keys, plus the HERON source and the drop count."""
    end = c["start"] + timedelta(seconds=c["seconds"]) if c["start"] else None
    bw = _num(settings.get("bandwidth_mhz"))
    return {
        "experiment": experiment,
        "collect_name": c["collect"],
        "start_time": c["start"].isoformat(timespec="seconds") if c["start"] else None,
        "end_time": end.isoformat(timespec="seconds") if end else None,
        "duration_s": round(c["seconds"], 3),
        "status": "success" if session.get("complete") else "incomplete",
        "radio_profile": {"name": c["band"], "freq_hz": c["freq_hz"], "rate_hz": c["rate_hz"],
                          "bw_hz": bw * 1e6 if bw else None},
        "gain_db": c["gain_db"],
        "antenna": c["label"],
        "antenna_port": c["port"],
        "device_args": ",".join(x for x in ("serial=" + c["serial"] if c["serial"] else "", settings.get("device_args")) if x),
        "clock_ref": settings.get("clock"),
        "sample_type": "byte" if c["format"] == "sc8" else "short",
        "wirefmt": settings.get("wire_format") or c["format"],
        "peak": _num(settings.get("sc8_peak")),
        "int_n": str(settings.get("int_n", "true")).lower() in ("true", "1"),
        "write_mode": settings.get("write_mode"),
        "spb": _num(settings.get("spb")),
        "data_file": c["collect"] + ".dat",
        "data_file_bytes": c["bytes"],
        "drops": drops,
        "source": {"payload": "HERON_Payload", "session": session["name"], "radio": c["radio"],
                   "channel": c["channel"], "segments": c["segments"], "setup": session.get("setup")},
    }


def _update_metadata(path, plan, settings, drops):
    """Add this session's bands, channel configs and collections to the experiment's metadata.yml (D-022)."""
    import yaml
    meta = {}
    if path.exists():
        meta = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    meta.setdefault("description", "HERON payload recordings downloaded from the NUC with tools/heron_pull.py.")
    bands = meta.setdefault("band_configurations", {})
    configs = meta.setdefault("channel_configurations", {})
    collections = meta.setdefault("collections", {})
    for c in plan["collects"]:
        bands.setdefault(c["band"], {"center_freq": c["freq_hz"], "inter_freq": 0.0})
        configs.setdefault(c["channel_config"], {
            "samp_rate": c["rate_hz"], "bands": [c["band"]],
            "sample_format": {"bit_depth": 8 if c["format"] == "sc8" else 16, "is_complex": True,
                              "is_integer": True, "is_signed": True, "is_i_lsb": True}})
        d = drops.get(c["radio"])
        port = f"{c['port']}, " if c["port"] else ""
        serial = f" serial {c['serial']}" if c["serial"] else ""
        note = (f"{c['band']} at {c['rate_hz'] / 1e6:g} Msps, antenna {c['label']} ({port}{c['gain_db']} dB "
                f"gain). {(c['model'] or 'radio').upper()}{serial}, radio {c['radio']} channel {c['channel']}. "
                f"HERON session {plan['session']}; {len(c['segments'])} segment(s) joined, "
                f"{c['seconds']:.2f} s ({c['bytes']} bytes).")
        if d and d["total"]:
            note += (f" {d['total']} drop(s), {d['missing_samples']} samples ({d['missing_ms']} ms) missing and not "
                     "padded: expect discontinuities (see nuc/.../samples.jsonl).")
        collections[c["collection_id"]] = {"channel_config": c["channel_config"],
                                           "filename": c["collect"] + ".dat", "notes": note}
    _write_yaml(path, meta)


def _write_yaml(path, data):
    import yaml
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8")


def pick(sessions, count, name, include_incomplete):
    if name:
        found = [s for s in sessions if s["name"] == name]
        if not found:
            raise SystemExit(f"No session {name} on the NUC. Use --list.")
        return found
    usable = [s for s in sessions if include_incomplete or s.get("complete")]
    return usable[:count]                      # the NUC lists the newest first


def main(argv=None):
    p = argparse.ArgumentParser(description="Download recent HERON recordings from the NUC.")
    p.add_argument("--nuc", default=DEFAULT_NUC, help=f"Payload Setup address (default {DEFAULT_NUC})")
    p.add_argument("--dest", default=DEFAULT_DEST, help=f"collects folder (default {DEFAULT_DEST})")
    p.add_argument("--experiment", help="experiment folder name (default <UTC date>_HERON_Payload)")
    p.add_argument("--count", type=int, default=1, help="how many of the newest finished sessions (default 1)")
    p.add_argument("--session", help="one session by name, e.g. HERON_20261102_170501_123456")
    p.add_argument("--include-incomplete", action="store_true", help="also sessions without a clean finish")
    p.add_argument("--include-simulated", action="store_true", help="also sessions from simulated radios")
    p.add_argument("--list", action="store_true", help="list the sessions on the NUC and stop")
    a = p.parse_args(argv)
    nuc = Nuc(a.nuc)
    sessions = nuc.get_json("/api/recordings")["sessions"]
    if not a.include_simulated:
        sessions = [s for s in sessions if not s.get("simulated")]
    if a.list:
        for s in sessions:
            state = "in progress" if s.get("complete") is None else "complete" if s["complete"] else "incomplete"
            print(f"{s['name']}  {s.get('setup') or '?':30s} {s['seconds']:8.1f} s  {s['bytes'] / 1e9:7.2f} GB  "
                  f"{state}{'  drops ' + str(s['overflows'] + s['gaps']) if s.get('overflows') or s.get('gaps') else ''}")
        return 0
    chosen = pick(sessions, a.count, a.session, a.include_incomplete)
    if not chosen:
        print("No finished session on the NUC. Use --list, or --include-incomplete.")
        return 1
    for s in chosen:
        start = session_start(s["name"])
        experiment = a.experiment or f"{(start or datetime.now(timezone.utc)).strftime('%Y%m%d')}_HERON_Payload"
        names = download_session(nuc, s, a.dest, experiment)
        if names:
            print(f"{s['name']}: {len(names)} collect(s) in {Path(a.dest) / experiment}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
