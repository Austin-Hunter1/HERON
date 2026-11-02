"""Automated bench tests run from Payload Setup: the gain sweep and the buffer sweep (HERON D-078).

Both record a series of short trials with the loaded setup, change one thing between trials, score each
trial, and then load the original setup again. Nothing is saved to the setup by itself: the page shows the
results and the operator chooses "Use".

Gain sweep: the collect wizard's gain calibration (collect_wizard.py step_gain_calibration), done for every
enabled channel at once. Each channel has its own receive chain, so one trial can give each channel a
different gain.
  1. Coarse: every channel at each gain in the list.
  2. Predicted: per channel, the median of (sigma_dB - gain_dB) over the clean coarse trials predicts the
     gain that puts `target_sigma_multiple` standard deviations inside full scale. One trial confirms it.
  3. Fine: `fine_step_db` steps between each channel's two best gains so far.
  The best score wins. Score = -|sigma/full scale - 1/target| - 50 x clip fraction, with sigma taken from the
  16th-84th percentile spread (robust to a burst of interference), as in the wizard.

Buffer sweep: every combination of write method, samples per buffer, UHD device args (USB buffers) and file
buffer. A trial is clean when the recorder reports no overflow or gap and samples.jsonl shows no drop.
"""
import copy
import itertools
import json
import math
import shutil
import statistics
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import recordings
from .config import atomic_json

# Wizard defaults (collect_wizard.py DEFAULT_CONFIG).
GAIN_DEFAULTS = dict(gains=[10, 20, 30, 40, 50, 60, 70], seconds=8.0, fine_step_db=2.0,
                     target_sigma_multiple=4.5, clip_threshold_fraction=0.98, keep=False)
BUFFER_DEFAULTS = dict(write_modes=["direct", "threaded"], spb=[2000, 10000, 50000],
                       device_args=["", "num_recv_frames=256", "num_recv_frames=1024"],
                       file_buffer_bytes=[0, 4194304], seconds=30.0, keep=False)
MAX_GAIN_DB = 76.0
MAX_TRIALS = 200
READ_VALUES = 4_000_000            # I and Q values read per channel file, in 8 slices across the file


# ---------------------------------------------------------------- scoring (pure functions)

def iq_quality(path, file_format, target_sigma_multiple, clip_threshold_fraction, max_values=READ_VALUES):
    """Score how well one channel file fills the 8- or 16-bit range (the wizard's analyze_iq_quality).

    Reads up to `max_values` values in 8 slices spread over the file, so a long file costs little time.
    """
    path = Path(path)
    eight = file_format == "sc8"
    size, full = (1, 127.0) if eight else (2, 32767.0)
    total = path.stat().st_size // size
    if total < 2:
        return dict(clip_fraction=1.0, sigma_fraction=0.0, score=float("-inf"), values=0)
    take = min(total, max_values)
    slices = 8 if total > take else 1
    per = (take // slices) // 2 * 2                    # keep I/Q pairs together
    counts = Counter()
    with open(path, "rb") as f:
        for k in range(slices):
            start = (total - per) * k // max(slices - 1, 1) // 2 * 2 if slices > 1 else 0
            f.seek(start * size)
            raw = f.read(per * size)
            if eight:
                counts.update(raw)                     # byte values 0..255; C speed
            else:
                counts.update(int.from_bytes(raw[i:i + 2], "little", signed=True) for i in range(0, len(raw), 2))
    if eight:
        counts = Counter({(b - 256 if b > 127 else b): n for b, n in counts.items()})
    n = sum(counts.values())
    if n < 2:
        return dict(clip_fraction=1.0, sigma_fraction=0.0, score=float("-inf"), values=0)
    limit = clip_threshold_fraction * full
    clipped = sum(c for v, c in counts.items() if abs(v) >= limit)
    p16, p84 = _percentile(counts, n, 0.16), _percentile(counts, n, 0.84)
    sigma_fraction = (p84 - p16) / 2.0 / full
    clip_fraction = clipped / n
    score = -abs(sigma_fraction - 1.0 / target_sigma_multiple) - 50.0 * clip_fraction
    return dict(clip_fraction=clip_fraction, sigma_fraction=sigma_fraction, score=score, values=n)


def _percentile(counts, n, q):
    """Linear-interpolated percentile (numpy's default method) from a value -> count table."""
    pos = q * (n - 1)
    i, frac = int(math.floor(pos)), pos - math.floor(pos)
    values = sorted(counts)

    def value_at(index):
        # The value at this position in the sorted list of all n values.
        seen = 0
        for v in values:
            seen += counts[v]
            if seen > index:
                return v
        return values[-1]

    lo = value_at(i)
    return float(lo) if frac == 0 else lo + frac * (value_at(min(i + 1, n - 1)) - lo)


def predict_gain(trials, target_sigma_multiple, max_gain_db=MAX_GAIN_DB):
    """The wizard's robust prediction: median of (sigma_dB - gain_dB) over clean trials. None if < 2."""
    offsets = [20 * math.log10(t["sigma_fraction"]) - t["gain"] for t in trials
               if not t.get("error") and t["clip_fraction"] == 0 and t["sigma_fraction"] > 0]
    if len(offsets) < 2:
        return None
    target_db = 20 * math.log10(1.0 / target_sigma_multiple)
    return max(0.0, min(max_gain_db, target_db - statistics.median(offsets)))


def fine_gains_between(lo, hi, step):
    """Gains strictly between lo and hi at `step` dB (the ends are already tested)."""
    out, g = [], lo + step
    while g < hi - 1e-9:
        out.append(round(g, 6))
        g += step
    return out


def buffer_trials(params):
    """Every combination of the buffer settings, in a stable order."""
    combos = itertools.product(params["write_modes"], params["spb"], params["device_args"], params["file_buffer_bytes"])
    return [dict(write_mode=m, spb=s, device_args=a, file_buffer_bytes=b) for m, s, a, b in combos]


def check_gain_params(raw):
    p = dict(GAIN_DEFAULTS, **{k: v for k, v in (raw or {}).items() if k in GAIN_DEFAULTS})
    gains = sorted({_num(g, 0, MAX_GAIN_DB, "Gain") for g in p["gains"]}) if isinstance(p["gains"], list) else []
    if len(gains) < 2:
        raise ValueError("Give at least two coarse gains (0-76 dB).")
    return dict(gains=gains, seconds=_num(p["seconds"], 1, 600, "Seconds per gain"),
                fine_step_db=_num(p["fine_step_db"], 0.5, 20, "Fine step"),
                target_sigma_multiple=_num(p["target_sigma_multiple"], 2, 10, "Target sigma multiple"),
                clip_threshold_fraction=_num(p["clip_threshold_fraction"], 0.5, 1, "Clip threshold"),
                keep=bool(p["keep"]))


def check_buffer_params(raw):
    import re
    p = dict(BUFFER_DEFAULTS, **{k: v for k, v in (raw or {}).items() if k in BUFFER_DEFAULTS})
    modes = [m for m in p["write_modes"] if m in ("direct", "threaded")] if isinstance(p["write_modes"], list) else []
    if not modes:
        raise ValueError("Choose at least one write method.")
    spb = [int(_num(v, 64, 10_000_000, "Samples per buffer")) for v in p["spb"]] if isinstance(p["spb"], list) else []
    fbb = [int(_num(v, 0, 1 << 30, "File buffer")) for v in p["file_buffer_bytes"]] \
        if isinstance(p["file_buffer_bytes"], list) else []
    args = [str(a).strip() for a in p["device_args"]] if isinstance(p["device_args"], list) else []
    pattern = r"[A-Za-z0-9_]+=[A-Za-z0-9_.+-]+(,[A-Za-z0-9_]+=[A-Za-z0-9_.+-]+)*"
    for a in args:
        if a and not re.fullmatch(pattern, a):
            raise ValueError("USB buffer entries look like num_recv_frames=256,recv_frame_size=16000.")
    if not spb or not fbb or not args:
        raise ValueError("Give at least one value in every list (an empty USB buffer entry means 'as set up').")
    out = dict(write_modes=modes, spb=spb, device_args=args, file_buffer_bytes=fbb,
               seconds=_num(p["seconds"], 1, 3600, "Seconds per trial"), keep=bool(p["keep"]))
    if len(buffer_trials(out)) > MAX_TRIALS:
        raise ValueError(f"That is {len(buffer_trials(out))} trials; keep it to {MAX_TRIALS} or fewer.")
    return out


def _num(value, lo, hi, label):
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(label + " must be a number.")
    if isinstance(value, bool) or not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError(f"{label} must be between {lo:g} and {hi:g}.")
    return v


# ---------------------------------------------------------------- the runner

class SweepRunner:
    """Runs one sweep at a time on a background thread. `snapshot()` is what the page polls."""

    def __init__(self, controller, results_dir, sleep=time.sleep, clock=time.monotonic):
        self.controller, self.results_dir = controller, Path(results_dir)
        self.sleep, self.clock = sleep, clock
        self.lock = threading.Lock()
        self.thread = None
        self.cancel = threading.Event()
        self.state = dict(running=False, kind=None, step=None, trial=0, trials=0, error=None, result=None,
                          started=None, saved=None)

    @property
    def running(self):
        with self.lock:
            return self.state["running"]

    def snapshot(self):
        with self.lock:
            s = copy.deepcopy(self.state)
        s["elapsed_s"] = round(self.clock() - s["started"], 1) if s["running"] and s["started"] else 0
        return s

    def start(self, kind, params):
        params = check_gain_params(params) if kind == "gain" else check_buffer_params(params)
        c = self.controller
        with self.lock:
            if self.state["running"]:
                raise ValueError("A sweep is already running.")
            if c.state != "READY" or not c.config:
                raise ValueError("Load a setup first." if c.state == "IDLE" else
                                 "Sweeps need the payload READY (now " + c.state + ").")
            if c.cube.get("armed"):
                raise ValueError("The aircraft is armed. Sweeps are for the bench.")
            self.cancel.clear()
            self.state = dict(running=True, kind=kind, step="Starting", trial=0, error=None, started=self.clock(),
                              trials=len(params["gains"]) + 1 if kind == "gain" else len(buffer_trials(params)),
                              result=None, saved=None, params=params)
        original = copy.deepcopy(c.config)
        work = self._gain if kind == "gain" else self._buffer
        self.thread = threading.Thread(target=self._run, args=(work, original, params), daemon=True,
                                       name=kind + "-sweep")
        self.thread.start()

    def stop(self):
        self.cancel.set()
        c = self.controller
        if c.state == "RECORDING" and c.origin == "sweep":
            c.execute("stop")

    def _run(self, work, original, params):
        result = error = None
        try:
            result = work(original, params)
        except Exception as exc:
            error = str(exc)
        if self.controller.state == "RECORDING" and self.controller.origin == "sweep":
            try:
                self.controller.execute("stop")
            except Exception:
                pass
        try:
            self._set(step="Loading the original setup again")
            self.controller.execute("prepare", dict(config=original))
        except Exception as exc:
            error = (error + "; " if error else "") + "could not load the original setup again: " + str(exc)
        saved = None
        if result is not None:
            try:
                self.results_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                saved = self.results_dir / f"{self.state['kind']}_sweep_{stamp}.json"
                atomic_json(saved, dict(kind=self.state["kind"], params=params, setup=original["name"], result=result,
                                        cancelled=self.cancel.is_set(), error=error))
                saved = str(saved)
            except OSError:
                saved = None
        if self.cancel.is_set() and not error:
            error = "Stopped before the end; the results so far are shown."
        self._set(running=False, step=None, result=result, error=error, saved=saved)

    def _set(self, **kw):
        with self.lock:
            self.state.update(kw)

    def _record(self, config, seconds, label):
        """Load `config`, record `seconds`, stop. Return the session folder (or None) and an error text."""
        c = self.controller
        if self.cancel.is_set():
            raise _Cancelled()
        self._set(step=label + ": loading radios")
        c.execute("prepare", dict(config=config))
        self._set(step=label + ": recording")
        c.execute("start", dict(origin="sweep"))
        session = c.session
        session_dir = c.backend.session_path()
        until = self.clock() + seconds
        while self.clock() < until and not self.cancel.is_set():
            if not (c.state == "RECORDING" and c.session == session):
                break                                  # ended elsewhere: disk reserve, recorder fault, ground Stop
            self.sleep(0.2)
        problem = None
        if c.state == "RECORDING" and c.session == session:
            c.execute("stop")
        elif not self.cancel.is_set():
            # Something else ended the trial. A fault is this trial's result; anything else (ground Stop,
            # disk reserve) ends the sweep, so a sweep never fights the operator.
            if c.state == "FAULT":
                problem = "recorder fault: " + str(c.fault)
            else:
                problem = "the recording was stopped from outside the sweep"
                self.cancel.set()
        if self.cancel.is_set():
            self._discard(session_dir, keep=False)
            raise _Cancelled()
        return (Path(session_dir) if session_dir else None), problem

    def _discard(self, session_dir, keep):
        """Delete one trial's recording. Only a HERON_* session folder that this runner just recorded."""
        if keep or not session_dir:
            return
        d = Path(session_dir)
        if recordings.SESSION_RE.match(d.name) and d.is_dir():
            shutil.rmtree(d, ignore_errors=True)

    # ------------------------------------------------ gain sweep
    def _gain(self, original, p):
        channels = [(r["id"], ch["index"], ch["label"]) for r in original["radios"] if r["enabled"]
                    for ch in r["channels"] if ch["enabled"]]
        trials = {key: [] for key in channels}
        result = dict(channels=[dict(radio=r, index=i, label=label) for r, i, label in channels], trials=[], best=[],
                      predicted={})
        done = 0

        def run(gain_of, phase):
            nonlocal done
            done += 1
            config = copy.deepcopy(original)
            for r in config["radios"]:
                for ch in r["channels"]:
                    if (r["id"], ch["index"], ch["label"]) in gain_of:
                        ch["gain_db"] = round(gain_of[(r["id"], ch["index"], ch["label"])], 1)
            text = ", ".join(sorted({f"{g:g} dB" for g in gain_of.values()}))
            self._set(trial=done)
            try:
                session_dir, problem = self._record(config, p["seconds"], f"{phase.title()} trial {done} ({text})")
            except _Cancelled:
                raise
            except Exception as exc:
                session_dir, problem = None, str(exc)
            for key, gain in gain_of.items():
                row = dict(radio=key[0], index=key[1], label=key[2], gain=round(gain, 1), phase=phase)
                try:
                    if problem:
                        raise RuntimeError(problem)
                    path, fmt = _channel_file(session_dir, key, original)
                    row.update(iq_quality(path, fmt, p["target_sigma_multiple"], p["clip_threshold_fraction"]))
                except Exception as exc:
                    row.update(clip_fraction=1.0, sigma_fraction=0.0, score=float("-inf"), error=str(exc))
                row["sigma_multiple"] = round(1 / row["sigma_fraction"], 2) if row["sigma_fraction"] > 0 else None
                trials[key].append(row)
                result["trials"].append(_json_safe(row))
            self._discard(session_dir, p["keep"])
            self._set(result=copy.deepcopy(result))

        try:
            for g in p["gains"]:
                run({key: g for key in channels}, "coarse")
            predicted = {key: predict_gain(trials[key], p["target_sigma_multiple"]) for key in channels}
            result["predicted"] = {f"{k[0]}|{k[1]}": (round(v, 1) if v is not None else None) for k, v in predicted.items()}
            wanted = {k: round(v, 1) for k, v in predicted.items() if v is not None}
            if wanted:
                run(wanted, "predicted")
            fine = {}
            for key in channels:
                ranked = sorted(trials[key], key=lambda t: t["score"], reverse=True)[:2]
                if len(ranked) == 2 and ranked[0]["score"] != float("-inf"):
                    lo, hi = sorted(t["gain"] for t in ranked)
                    fine[key] = fine_gains_between(lo, hi, p["fine_step_db"])
            steps = max((len(v) for v in fine.values()), default=0)
            self._set(trials=done + steps)
            for k in range(steps):
                run({key: g[k] for key, g in fine.items() if k < len(g)}, "fine")
        except _Cancelled:
            pass
        for key in channels:
            ok = [t for t in trials[key] if t["score"] != float("-inf")]
            best = max(ok, key=lambda t: t["score"]) if ok else None
            result["best"].append(dict(radio=key[0], index=key[1], label=key[2],
                                       gain=best["gain"] if best else None,
                                       sigma_multiple=best["sigma_multiple"] if best else None,
                                       clip_pct=round(100 * best["clip_fraction"], 4) if best else None,
                                       phase=best["phase"] if best else None))
        return result

    # ------------------------------------------------ buffer sweep
    def _buffer(self, original, p):
        rows = []
        result = dict(trials=rows, recommended=None)
        for n, t in enumerate(buffer_trials(p), start=1):
            self._set(trial=n)
            config = copy.deepcopy(original)
            config.update(write_mode=t["write_mode"], spb=t["spb"], file_buffer_bytes=t["file_buffer_bytes"],
                          device_args=t["device_args"] or original.get("device_args", ""))
            label = f"Trial {n} ({t['write_mode']}, spb {t['spb']}, {t['device_args'] or 'USB as set up'}, " \
                    f"file buffer {t['file_buffer_bytes']})"
            row = dict(t, n=n, overflows=None, gaps=None, drops=None, missing_samples=None, mbps=None,
                       clean=False, error=None)
            try:
                session_dir, problem = self._record(config, p["seconds"], label)
            except _Cancelled:
                break
            except Exception as exc:
                session_dir, problem = None, str(exc)
            row["error"] = problem
            if session_dir and session_dir.is_dir():
                row.update(_buffer_metrics(session_dir, p["seconds"]))
            row["clean"] = not row["error"] and row["overflows"] == 0 and row["gaps"] == 0 and row["drops"] in (0, None)
            rows.append(row)
            self._discard(session_dir, p["keep"])
            self._set(result=copy.deepcopy(result))
        clean = [r for r in rows if r["clean"]]
        # Prefer the collect wizard's direct method, then the smallest buffers that were clean.
        clean.sort(key=lambda r: (r["write_mode"] != "direct", r["spb"], r["file_buffer_bytes"]))
        result["recommended"] = clean[0]["n"] if clean else None
        return result


class _Cancelled(Exception):
    pass


def _channel_file(session_dir, key, config):
    """The first sample file of one channel. Files are numbered by enabled channel order in a radio."""
    radio_id, index, _ = key
    radio = next(r for r in config["radios"] if r["id"] == radio_id)
    n = [ch["index"] for ch in radio["channels"] if ch["enabled"]].index(index)
    folder = Path(session_dir) / radio_id
    for fmt in ("sc8", "sc16"):
        path = folder / f"channel_{n}_000000.{fmt}"
        if path.is_file():
            return path, fmt
    raise ValueError("No sample file for " + radio_id + " channel " + str(n))


def _buffer_metrics(session_dir, seconds):
    """Overflows and gaps from completion.json, drops from each radio's samples.jsonl, and the write rate."""
    out = dict(overflows=0, gaps=0, drops=None, missing_samples=None, mbps=None)
    try:
        completion = json.loads((Path(session_dir) / "completion.json").read_text())
    except (OSError, ValueError):
        completion = {}
    for r in ((completion.get("status") or {}).get("radios")) or []:
        out["overflows"] += int(r.get("overflows") or 0)
        out["gaps"] += int(r.get("gaps") or 0)
    size = 0
    for radio in (p for p in Path(session_dir).iterdir() if p.is_dir()):
        size += sum(f.stat().st_size for f in radio.glob("channel_*"))
        try:
            d = recordings.find_drops(session_dir, radio.name)
        except ValueError:
            continue                                   # no samples.jsonl (simulated radios)
        out["drops"] = (out["drops"] or 0) + d["total"]
        out["missing_samples"] = (out["missing_samples"] or 0) + d["missing_samples"]
    out["mbps"] = round(size / seconds / 1e6, 2) if seconds else None
    return out


def _json_safe(row):
    return {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in row.items()}
