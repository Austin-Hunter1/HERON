"""Figures that tell the story of the 2026-10-01 B210 dual-stream test.

The test recorded two antennas (Flight, Patch) on one B210 at the same time, in
four collects (L1CA and L5, morning and afternoon). This script joins three
sources that normally sit apart:

* the logging: the USRP sidecar (`*.dat.usrp`), the collect YAML, and the
  gain-calibration sweeps;
* the tracking: the HDF5 files that notebook 01 wrote;
* the position solutions: the notebook-02 pipeline, run here for the runs that
  decode a time of week, in three variants (all epochs, settled epochs, and
  settled epochs from satellites that pass a cross-satellite consistency check).

Every setting comes from `configs/oct01_story_figures.yml`. Run:

    python scripts/oct01_story_figures.py [--config PATH]

The figures go to `<collects>/<experiment>/Figures/<output_subdir>/`, each as a
PNG (200 dpi, 16:9) and an SVG for slides.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from datetime import datetime, timedelta
from pathlib import Path

import h5py
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import patheffects
import numpy as np
import yaml
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, FuncFormatter, NullFormatter, NullLocator

# The repository root must be on the path so `utils` resolves to this copy.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import utils  # noqa: E402
from utils import (  # noqa: E402
    broadcast_ephemeris,
    catalog,
    navigation,
    observables,
    tracking_io,
)
from utils import signal_interfaces  # noqa: E402
from utils.nav import cnav, lnav  # noqa: E402
from utils.nav import symbols as nav_symbols  # noqa: E402

SPEED_OF_LIGHT_M_S = 299_792_458.0
BYTES_PER_COMPLEX_SAMPLE_SC8 = 2
ADC_FULL_SCALE_COUNTS = 127.0
USRP_RECORD_DTYPE = np.dtype([("index", "<u4"), ("t_device_s", "<f8"),
                              ("t_host_s", "<f8"), ("error_code", "u1")])
USRP_OVERFLOW_CODE = 8  # UHD `ERROR_CODE_OVERFLOW`
L5_CODE_PERIOD_MS = 20.0  # Q5 x NH20: the code period acquisition cannot see beyond
REQUIRED_KEYS = (
    "experiment", "output_subdir", "track_start_offset_ms", "track_duration_ms",
    "reference_geo", "epoch_interval_ms", "settle_s", "cluster_gap_s", "quiet_start_s",
    "quiet_end_s", "consistency_threshold_km", "ephemeris_span_hours", "map_half_extent_m",
    "collects", "gain_calibration", "tracking_runs", "scorecard",
    "acquisition_sweep_l1ca_patch", "physical_antenna", "before_after_run",
)

# Colour-blind-safe palette (Okabe-Ito). Flight and Patch are the two antennas; the
# bands only appear in figure 1; EVENT marks USRP overflows everywhere.
FLIGHT, PATCH = "#D55E00", "#0072B2"
L1CA_COLOR, L5_COLOR = "#009E73", "#CC79A7"
CAL_COLOR, EVENT, MUTED = "#E69F00", "#222222", "#8A8A8A"
ANTENNA_COLOR = {"Flight": FLIGHT, "Patch": PATCH}
PRN_COLORS = {"G03": "#56B4E9", "G04": "#0072B2", "G07": "#999999", "G09": "#E69F00",
              "G16": "#F0E442", "G26": "#009E73", "G27": "#CC79A7"}
BAND_COLOR = {"L1CA": L1CA_COLOR, "L5": L5_COLOR}
SLIDE = (16, 9)
FOOTER = "2026-10-01 B210 dual-stream test  |  gnss_processing"

STYLE = {
    "font.size": 14, "axes.titlesize": 15, "axes.labelsize": 14, "xtick.labelsize": 13,
    "ytick.labelsize": 13, "legend.fontsize": 12, "axes.spines.top": False,
    "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.25, "lines.linewidth": 2.0,
    "axes.titleweight": "bold", "figure.dpi": 100,
}


# --------------------------------------------------------------------------- config
def load_config(path: Path) -> dict:
    """Read the YAML config and stop with a clear message if a key is missing."""
    with open(path, encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    missing = [key for key in REQUIRED_KEYS if key not in config]
    if missing:
        raise SystemExit(f"{path}: missing keys: {', '.join(missing)}")
    for run in config["tracking_runs"]:
        name = run["signal"]
        if not hasattr(signal_interfaces, name) or getattr(signal_interfaces, name) is None:
            raise SystemExit(f"{path}: signal {name!r} is not available in utils.signal_interfaces")
    return config


def _fit_text(fig, x, y, text, size, color, weight, max_right=0.965):
    """Place left-aligned text and shrink it until it fits inside the figure."""
    artist = fig.text(x, y, text, fontsize=size, color=color, fontweight=weight, va="top", ha="left")
    renderer = fig.canvas.get_renderer()
    while size > 11:
        width = artist.get_window_extent(renderer).width / fig.bbox.width
        if x + width <= max_right:
            break
        size -= 0.5
        artist.set_fontsize(size)


def finish(fig, outdir: Path, name: str, headline: str, subtitle: str = "", note: str = "",
           top: float | None = None) -> None:
    """Add the headline, subtitle, optional note and footer, then save a PNG and an SVG.

    `top` sets where the plot area ends. Use a lower value when the plot has labels outside its axes.
    """
    bottom = 0.09 if note else 0.03
    if top is None:
        top = 0.885 if subtitle else 0.925
    fig.tight_layout(rect=(0.0, bottom, 1.0, top))
    _fit_text(fig, 0.012, 0.975, headline, 22, "black", "bold")
    if subtitle:
        _fit_text(fig, 0.012, 0.925, subtitle, 15, "0.3", "normal")
    if note:
        fig.text(0.012, 0.038, note, fontsize=11, color="0.4", ha="left", va="bottom")
    fig.text(0.988, 0.008, FOOTER, fontsize=10, color="0.5", ha="right", va="bottom")
    fig.savefig(outdir / f"{name}.png", dpi=200)
    fig.savefig(outdir / f"{name}.svg")
    plt.close(fig)


# --------------------------------------------------------------------------- logging data
def read_collect_yaml(collects_dir: Path, experiment: str, folder: str) -> dict:
    """The one-off YAML that the recorder wrote beside a collect."""
    with open(collects_dir / experiment / f"{folder}.yml", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def read_usrp_sidecar(path: Path) -> np.ndarray:
    """Read the 21-byte-per-record USRP sidecar (one record per recorder transfer)."""
    return np.fromfile(path, dtype=USRP_RECORD_DTYPE)


def stream_sidecar(collects_dir: Path, experiment: str, folder: str, stream_dir: str) -> Path:
    """Path of the `.dat.usrp` file of one stream."""
    matches = sorted((collects_dir / experiment / folder / stream_dir).glob("*.dat.usrp"))
    if not matches:
        raise SystemExit(f"no .dat.usrp file under {folder}/{stream_dir}")
    return matches[0]


def stream_dir_of(stream: dict) -> str:
    """Folder name of one stream, from its `data_file` path (`<collect>/<stream>/<file>`)."""
    return stream["data_file"].split("/")[1]


def physical_antenna(config: dict, stream: dict) -> str:
    """The antenna that was really on this stream's RF connector (D-031).

    The recorder wrote its own antenna label into each stream folder and YAML, and
    that label is crossed on 2026-10-01. The config maps each stream folder to the
    real antenna, so the figures never read the recorder label as the antenna.
    """
    return config["physical_antenna"][stream_dir_of(stream)]


def recorder_label_for(config: dict, info: dict, antenna: str) -> str:
    """The recorder's own label (the key of the overflow tables) of the stream with this antenna."""
    return next(s["antenna"] for s in info["channels"] if physical_antenna(config, s) == antenna)


def record_period_s(info: dict, n_records: int) -> float:
    """Seconds of data per sidecar record, from the file size and the sample rate."""
    stream = info["channels"][0]
    samples_per_record = stream["data_file_bytes"] / n_records / BYTES_PER_COMPLEX_SAMPLE_SC8
    return samples_per_record / info["radio_profile"]["rate_hz"]


def adc_std_counts(data_path: Path, n_bytes: int = 8_000_000) -> float:
    """Standard deviation of the recorded 8-bit values: the signal power, without DC."""
    values = np.fromfile(data_path, dtype=np.int8, count=n_bytes).astype(float)
    return float(values.std())


def overflow_events(rec: np.ndarray, period_s: float) -> tuple[np.ndarray, np.ndarray]:
    """Record indices and file times of the overflow-flagged records."""
    index = np.nonzero(rec["error_code"] == USRP_OVERFLOW_CODE)[0]
    return index, index * period_s


def wrap_ms(value_ms: float, period_ms: float) -> float:
    """Wrap a time to +/- half a code period."""
    return float((value_ms + period_ms / 2) % period_ms - period_ms / 2)


def sidecar_anomaly_ms(rec: np.ndarray, indices: list[int], period_s: float) -> float:
    """Device-time anomaly of one overflow event, wrapped to +/- 10 ms.

    The device time that passes between the record before an overflow and the record
    after it, minus the two transfer periods it should span. The sum over all
    overflows of an event is wrapped because the tracking can only see a gap
    modulo the 20 ms code period.
    """
    total = 0.0
    for i in indices:
        span = rec["t_device_s"][i + 1] - rec["t_device_s"][i - 1]
        total += span * 1e3 - 2 * period_s * 1e3
    return wrap_ms(total, L5_CODE_PERIOD_MS)


# --------------------------------------------------------------------------- tracking data
def read_tracking(path: Path) -> dict[str, dict[str, np.ndarray]]:
    """Per-PRN arrays from a notebook-01 HDF5 file."""
    out: dict[str, dict[str, np.ndarray]] = {}
    with h5py.File(path, "r") as handle:
        for sid in sorted(handle.keys()):
            group = handle[sid]
            cn0 = group["cn0_dbhz"][:]
            if cn0.ndim > 1:
                with np.errstate(all="ignore"):
                    cn0 = np.nanmax(np.where(np.isnan(cn0), -np.inf, cn0), axis=1)
                cn0[~np.isfinite(cn0)] = np.nan
            out[sid] = {
                "uptime_s": group["uptime_epoch_ms"][:] * 1e-3,
                "code_phase_ms": group["code_phase_ms"][:],
                "pll_mode": group["pll_mode"][:].astype(bool),
                "cn0_dbhz": cn0,
                "cn0_uptime_s": group["cn0_uptime_ms"][:] * 1e-3,
            }
    return out


def segment_starts_s(uptime_s: np.ndarray, gap_s: float = 0.05) -> np.ndarray:
    """Start time of each tracking segment: the first epoch, and any epoch after a gap."""
    if not len(uptime_s):
        return np.array([])
    jumps = np.nonzero(np.diff(uptime_s) > gap_s)[0] + 1
    return uptime_s[np.concatenate(([0], jumps))]


def tracking_path_for(config: dict, outputs_path: Path, ch: "catalog.Channel", signal) -> Path:
    return tracking_io.tracking_path(
        outputs_path, ch.experiment_name, ch.collect_id, signal.signal_type_id,
        config["track_start_offset_ms"], config["track_duration_ms"],
        experiment_collect_ids=ch.sibling_collect_ids,
    )


# --------------------------------------------------------------------------- overflow ledger
def merge_events(times_s: np.ndarray, gap_s: float) -> list[list[int]]:
    """Group overflow indices that are closer than `gap_s` into one event."""
    clusters: list[list[int]] = []
    for i, t in enumerate(times_s):
        if clusters and t - times_s[clusters[-1][-1]] < gap_s:
            clusters[-1].append(i)
        else:
            clusters.append([i])
    return clusters


def pseudorange_ledger(time_s: np.ndarray, pr_km: np.ndarray, times_s: np.ndarray,
                       clusters: list[list[int]], quiet_start_s: float,
                       quiet_end_s: float) -> dict:
    """Step in each satellite's pseudorange level across each overflow event.

    The level is the median pseudorange in the quiet window between events. The step
    that is the same on every satellite is time the receiver lost (the clock absorbs
    it). What differs between satellites is a tracking error in one channel.
    """
    edges_lo = [time_s[0] + 1.0] + [times_s[c[-1]] + quiet_start_s for c in clusters]
    edges_hi = [times_s[c[0]] - quiet_end_s for c in clusters] + [time_s[-1]]
    levels = np.full((len(clusters) + 1, pr_km.shape[1]), np.nan)
    for k, (lo, hi) in enumerate(zip(edges_lo, edges_hi)):
        sel = (time_s > lo) & (time_s < hi)
        if sel.sum() > 5:
            levels[k] = np.nanmedian(pr_km[sel], axis=0)
    steps = np.diff(levels, axis=0)
    common = np.full(len(clusters), np.nan)
    for k in range(len(clusters)):
        if np.isfinite(steps[k]).sum() >= 3:
            common[k] = np.nanmedian(steps[k])
    rel = steps - common[:, None]
    cumulative = np.cumsum(np.nan_to_num(rel), axis=0)
    return {"common_km": common, "rel_km": rel, "cumulative_km": cumulative,
            "event_time_s": np.array([times_s[c[0]] for c in clusters])}


def inconsistent_from_s(ledger: dict, threshold_km: float) -> np.ndarray:
    """Per satellite, the time from which its running offset exceeds the threshold."""
    n_sats = ledger["cumulative_km"].shape[1] if len(ledger["cumulative_km"]) else 0
    out = np.full(n_sats, np.inf)
    for j in range(n_sats):
        bad = np.nonzero(np.abs(ledger["cumulative_km"][:, j]) > threshold_km)[0]
        if len(bad):
            out[j] = ledger["event_time_s"][bad[0]]
    return out


# --------------------------------------------------------------------------- navigation
def settled_mask(outputs, obs_uptime_ms: np.ndarray, settle_s: float) -> np.ndarray:
    """True where a channel is in PLL mode and at least `settle_s` past its latest (re)start."""
    uptime_ms = outputs.uptime_epoch_ms[outputs.valid]
    pll = np.asarray(outputs.pll_mode[outputs.valid], dtype=bool)
    starts_ms = segment_starts_s(uptime_ms * 1e-3) * 1e3
    idx = np.clip(np.searchsorted(uptime_ms, obs_uptime_ms, side="right") - 1, 0, len(uptime_ms) - 1)
    seg = np.clip(np.searchsorted(starts_ms, obs_uptime_ms, side="right") - 1, 0, len(starts_ms) - 1)
    next_idx = np.clip(idx + 1, 0, len(uptime_ms) - 1)
    in_gap = (uptime_ms[next_idx] - uptime_ms[idx]) > 50.0
    covered = (obs_uptime_ms >= uptime_ms[0]) & (obs_uptime_ms <= uptime_ms[-1])
    return covered & ~in_gap & pll[idx] & ((obs_uptime_ms - starts_ms[seg]) >= settle_s * 1e3)


def run_navigation(config: dict, collects_dir: Path, outputs_path: Path, run_cfg: dict,
                   ch: "catalog.Channel", overflow_times_s: np.ndarray) -> dict | None:
    """The notebook-02 pipeline for one run, in three variants."""
    signal = getattr(signal_interfaces, run_cfg["signal"])
    tid = signal.signal_type_id
    message_format = {"GPS_L1CA": "LNAV", "GPS_L2C": "CNAV", "GPS_L5": "CNAV"}[tid]
    run = tracking_io.require_tracking_run(
        tracking_path_for(config, outputs_path, ch, signal),
        collect_id=ch.collect_id, signal_type_id=tid,
        minimum_duration_ms={"GPS_L1CA": 40_000, "GPS_L2C": 40_000, "GPS_L5": 15_000}[tid],
        minimum_signals=1,
    )

    anchors, decodes = {}, {}
    for sid in run.signal_ids:
        stream = nav_symbols.extract(run[sid].outputs, tid)
        if not len(stream):
            continue
        if message_format == "CNAV":
            result = cnav.decode(stream.soft, message_duration_s=cnav.MESSAGE_DURATION_S[tid],
                                 expected_prn=int(sid[1:]))
            if not result.synced:
                continue
            anchors[sid] = observables.anchor_from_cnav(stream, result.messages[0], sat_id=sid)
        else:
            result = lnav.decode(stream.soft)
            if not result.synced:
                continue
            anchors[sid] = observables.anchor_from_lnav(stream, result.subframes[0], sat_id=sid)
        decodes[sid] = result
    if not anchors:
        return None

    collect_date = datetime.strptime(ch.collect_id.split("_")[0], "%Y%m%d")
    week, _ = broadcast_ephemeris.gps_week_and_tow(collect_date)
    reference_tow = float(np.median([a.tow_s for a in anchors.values()]))
    epoch_datetime = broadcast_ephemeris.datetime_from_gps(week, reference_tow)
    span = timedelta(hours=max(2.0, float(config["ephemeris_span_hours"])))
    records = broadcast_ephemeris.load_brdc_span(epoch_datetime - span, epoch_datetime + span)
    broadcast = broadcast_ephemeris.ephemerides_for(
        sorted(anchors), reference_tow, week, epoch_datetime, records=records)
    anchors = {sid: a for sid, a in anchors.items() if sid in broadcast}

    obs = observables.form_observables(
        {sid: run[sid].outputs for sid in anchors}, anchors,
        epoch_interval_ms=config["epoch_interval_ms"], week=week)

    iono_alpha = iono_beta = None
    for result in decodes.values():
        if message_format == "CNAV":
            clocks = [m for m in result.messages if 30 <= m.message_type <= 37]
            if clocks:
                parsed = cnav.parse_type_30(clocks[0])
                iono_alpha, iono_beta = parsed.alpha, parsed.beta
                break
        else:
            for sub in result.subframes:
                parsed = lnav.parse_iono_utc(sub)
                if parsed is not None:
                    iono_alpha, iono_beta = parsed.alpha, parsed.beta
                    break
            if iono_alpha is not None:
                break
    if iono_alpha is None:
        header = broadcast_ephemeris.load_brdc_iono(collect_date)
        if header is not None:
            iono_alpha, iono_beta = header

    settings = navigation.CorrectionSettings(
        satellite_clock=True, group_delay=True, sagnac=True, troposphere=True, ionosphere=True)
    reference_ecef = navigation.geodetic_to_ecef(*config["reference_geo"])

    def solve(o):
        return navigation.solve_series(
            o, broadcast, signal_type_id=tid, settings=settings,
            initial_position_ecef_m=reference_ecef, iono_alpha=iono_alpha, iono_beta=iono_beta)

    time_s = obs.epoch_uptime_ms * 1e-3
    pr_km = obs.pseudorange_m * 1e-3
    clusters = merge_events(overflow_times_s, config["cluster_gap_s"])
    ledger = (pseudorange_ledger(time_s, pr_km, overflow_times_s, clusters,
                                 config["quiet_start_s"], config["quiet_end_s"])
              if clusters else None)
    bad_from_s = (inconsistent_from_s(ledger, config["consistency_threshold_km"])
                  if ledger is not None else np.full(len(obs.sat_ids), np.inf))

    settled = obs.pseudorange_m.copy()
    consistent = obs.pseudorange_m.copy()
    for j, sid in enumerate(obs.sat_ids):
        ok = settled_mask(run[sid].outputs, obs.epoch_uptime_ms, config["settle_s"])
        settled[~ok, j] = np.nan
        consistent[~(ok & (time_s < bad_from_s[j])), j] = np.nan
    variants = {
        "all": obs,
        "settled": dataclasses.replace(obs, pseudorange_m=settled),
        "consistent": dataclasses.replace(obs, pseudorange_m=consistent),
    }

    out = {"obs": obs, "sat_ids": list(obs.sat_ids), "ledger": ledger,
           "bad_from_s": dict(zip(obs.sat_ids, bad_from_s)), "clusters": clusters}
    for name, o in variants.items():
        try:
            sol = solve(o)
        except Exception as exc:  # a solver failure must not stop the other figures
            print(f"  {ch.collect_id}: {name} solve failed: {type(exc).__name__}: {exc}")
            out[name] = None
            continue
        enu = sol.enu_about(reference_ecef)
        out[name] = {"enu": enu, "valid": np.asarray(sol.valid, dtype=bool), "time_s": time_s}
    return out


# --------------------------------------------------------------------------- figures
def fig_scorecard(config, collects_dir, outputs_path, overflow_info, outdir) -> None:
    """Figure 0: one row per recorded stream, from the recording to the result."""
    exp = config["experiment"]
    header = ["Collect (local)", "Antenna", "Gain set\n[dB]", "Signal level\n[ADC counts]",
              "USRP\noverflows", "Satellites\nacquired", "Tracking"]
    rows, colors = [], []
    labels = {c["folder"]: c["label"] for c in config["collects"]}
    for entry in config["scorecard"]:
        info, period, over = overflow_info[entry["folder"]]
        stream = next(s for s in info["channels"] if physical_antenna(config, s) == entry["antenna"])
        level = adc_std_counts(collects_dir / exp / stream["data_file"])
        n_over = len(over[stream["antenna"]])
        rows.append([labels[entry["folder"]], entry["antenna"], f"{stream['gain_db']:.0f}",
                     f"{level:.2f}", str(n_over), entry["acquired"], entry["tracking"]])
        good = entry["tracking"].startswith("full")
        colors.append("#DCEFE6" if good else ("#FBE5CC" if entry["tracking"] != "none" else "#F3D9D9"))
    fig, ax = plt.subplots(figsize=SLIDE)
    ax.axis("off")
    table = ax.table(cellText=rows, colLabels=header, loc="center", cellLoc="center",
                     colWidths=[0.12, 0.08, 0.10, 0.11, 0.09, 0.27, 0.23])
    table.auto_set_font_size(False)
    table.set_fontsize(15)
    table.scale(1, 3.6)
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor("white")
        if r == 0:
            cell.set_facecolor("#33415C")
            cell.set_text_props(color="white", fontweight="bold")
        else:
            cell.set_facecolor(colors[r - 1])
            if c == 1:
                cell.set_text_props(color=ANTENNA_COLOR[rows[r - 1][1]], fontweight="bold")
            if c == 2:
                cell.set_text_props(fontweight="bold")
    finish(fig, outdir, "00_scorecard",
           "Only the afternoon collects gave usable tracks",
           "Eight streams, one B210: what was recorded and what the receiver could do with it",
           note="Green: tracked the whole collect.  Orange: acquired but lost.  Red: nothing acquired.  "
                "Signal level = standard deviation of the recorded 8-bit samples; "
                "L1CA streams are 5 Msps, L5 streams 25 Msps.")


def fig_logging_overview(config, collects_dir, outdir) -> dict:
    """Figure 1: when each collect ran, and where the USRP logged overflows."""
    exp = config["experiment"]
    fig, axes = plt.subplots(3, 1, figsize=SLIDE, gridspec_kw={"height_ratios": [1.25, 1.15, 1.1]})
    ax_t, ax_o, ax_z = axes
    ax_t.grid(False)

    overflow_info = {}
    n = len(config["collects"])
    for row, entry in enumerate(config["collects"]):
        info = read_collect_yaml(collects_dir, exp, entry["folder"])
        band = "L5" if "L5" in entry["folder"] else "L1CA"
        x = row if row < 2 else row + 0.7  # the 3.5 h gap is drawn as a break
        by_antenna = {}
        for stream in info["channels"]:
            rec = read_usrp_sidecar(stream_sidecar(collects_dir, exp, entry["folder"], stream_dir_of(stream)))
            period = record_period_s(info, len(rec))
            by_antenna[stream["antenna"]] = overflow_events(rec, period)[1]
        overflow_info[entry["folder"]] = (info, period, by_antenna)
        shared = by_antenna[info["channels"][0]["antenna"]]
        ax_t.add_patch(plt.Rectangle((x - 0.4, 0), 0.8, 1, color=BAND_COLOR[band]))
        ax_t.text(x, 0.78, entry["label"], ha="center", va="center", color="w", fontweight="bold", fontsize=16)
        gain = {physical_antenna(config, s): s["gain_db"] for s in info["channels"]}
        ax_t.text(x, 0.36, f"{info['radio_profile']['rate_hz']/1e6:g} Msps\n"
                           f"gain {gain['Flight']:.0f} | {gain['Patch']:.0f} dB",
                  ha="center", va="center", color="w", fontsize=12)
        ax_t.text(x, -0.1, f"{len(shared)} overflows" if len(shared) else "no overflows",
                  ha="center", va="top", fontsize=13)
        ax_o.scatter(shared, np.full(len(shared), row), marker="|", s=1100, color=EVENT, lw=2.5)
        ax_o.axhline(row, color="0.88", lw=1.0, zorder=0)
    ax_t.add_patch(plt.Rectangle((4.75 - 0.4, 0.15), 0.8, 0.7, color=CAL_COLOR))
    ax_t.text(4.75, 0.5, "gain\ncalibration\n15:00", ha="center", va="center", fontsize=12)
    ax_t.text(1.85, 0.5, "3.5 h later", ha="center", va="center", fontsize=13, color="0.4")
    ax_t.text(-0.55, 1.12, "gain = Flight | Patch", fontsize=11, color="0.4", va="bottom")
    ax_t.set(xlim=(-0.6, 5.4), ylim=(-0.5, 1.12), yticks=[], xticks=[])
    ax_t.set_title("Four collects, the Flight and Patch antennas recorded at once", loc="left", pad=14)
    ax_o.set(yticks=range(n), xlim=(0, 130), xlabel="Time in the collect [s]")
    ax_o.set_yticklabels([c["label"] for c in config["collects"]])
    ax_o.invert_yaxis()
    ax_o.set_title("USRP overflow records: identical on both antennas, because they share one stream", loc="left")

    folder = config["collects"][3]["folder"]
    info, period, over = overflow_info[folder]
    stream = info["channels"][0]
    rec = read_usrp_sidecar(stream_sidecar(collects_dir, exp, folder, stream_dir_of(stream)))
    first = int(overflow_events(rec, period)[0][0])
    idx = np.arange(first - 6, first + 8)
    dt_ms = np.diff(rec["t_device_s"])[idx[0]:idx[-1] + 1] * 1e3
    ax_z.bar(idx, dt_ms, color=[EVENT if i in (first - 1, first) else "#C9C9C9" for i in idx])
    ax_z.axhline(period * 1e3, color=EVENT, ls="--", lw=1.2)
    ax_z.text(idx[-1] + 0.3, period * 1e3 + 1.0, f"expected: {period*1e3:.0f} ms per transfer", fontsize=11, ha="right")
    ax_z.set(xlabel="Record index", ylabel="Interval [ms]", ylim=(0, 26))
    ax_z.set_title(f"Zoom on the first overflow of the {config['collects'][3]['label']} collect: "
                   "two records span 20 ms of device time instead of 40 ms", loc="left")
    finish(fig, outdir, "01_logging_overview",
           "The 25 Msps collects overflowed eight times each; the 5 Msps collects never did",
           "What the recorder logged on Oct 01")
    return overflow_info


def fig_adc_scaling(config, collects_dir, outdir) -> None:
    """Figure 2: recorded ADC level against the gain setting, per stream and session."""
    exp = config["experiment"]
    fig, ax = plt.subplots(figsize=SLIDE)
    cal_levels = {}
    for antenna, folder in config["gain_calibration"].items():
        base = collects_dir / exp / "gain_calibration" / folder
        with open(base / "calibration_summary.yml", encoding="utf-8") as handle:
            summary = yaml.safe_load(handle)
        points = {}
        for r in summary["coarse_results"] + summary["predicted_results"]:
            level = adc_std_counts(base / f"gaincal_L5_g{int(r['gain'])}_{r['phase']}.dat")
            if level > 0:
                points[float(r["gain"])] = level
        cal_levels[antenna] = points
        gains = sorted(points)
        ax.plot(gains, [points[g] for g in gains], "o-", color=ANTENNA_COLOR[antenna], alpha=0.55, ms=7,
                label=f"{antenna}, gain calibration at 15:00 (L5)")
    ref_label = config["collects"][3]["label"]
    for entry in config["collects"]:
        info = read_collect_yaml(collects_dir, exp, entry["folder"])
        for stream in info["channels"]:
            antenna = physical_antenna(config, stream)
            raw_level = adc_std_counts(collects_dir / exp / stream["data_file"])
            level = max(raw_level, 1e-2)
            ax.plot(stream["gain_db"], level, "*", ms=19, mfc=ANTENNA_COLOR[antenna], mec="k", ls="none",
                    label=f"{antenna}, collects" if entry["label"] == ref_label else None, zorder=4)
            tag = entry["label"] + ("  (no signal)" if raw_level < 1e-2 else "")
            ax.annotate(tag, (stream["gain_db"], level), textcoords="offset points", xytext=(14, -4), fontsize=11)
            ref = cal_levels[antenna].get(float(stream["gain_db"]))
            if entry["label"] == ref_label and ref:
                delta = 20 * np.log10(ref / level)
                if abs(delta) >= 1:
                    ax.annotate("", xy=(stream["gain_db"], level), xytext=(stream["gain_db"], ref),
                                arrowprops=dict(arrowstyle="<->", color=ANTENNA_COLOR[antenna], lw=2))
                    ax.text(stream["gain_db"] - 0.9, np.sqrt(level * ref), f"{delta:.0f} dB below\nthe calibration",
                            ha="right", va="center", fontsize=13, color=ANTENNA_COLOR[antenna], fontweight="bold")
                else:
                    ax.text(stream["gain_db"] - 1.2, level * 0.12, "on the calibration curve",
                            ha="right", va="center", fontsize=13, color=ANTENNA_COLOR[antenna], fontweight="bold")
    ax.axhspan(1e-3, 1.0, color="0.5", alpha=0.10)
    ax.text(10.4, 0.55, "below 1 count: the signal barely moves the 8-bit converter", fontsize=12, color="0.3", va="top")
    ax.axhline(ADC_FULL_SCALE_COUNTS, color=EVENT, lw=1.2)
    ax.text(10.4, ADC_FULL_SCALE_COUNTS * 1.1, "8-bit full scale", fontsize=12, va="bottom")
    ax.set(yscale="log", xlabel="Receiver gain setting [dB]", ylabel="Recorded signal, standard deviation [ADC counts]",
           xlim=(9, 73), ylim=(1e-3, 400))
    ax.legend(loc="lower right")
    finish(fig, outdir, "02_recorded_level_vs_gain",
           "At the same gain setting, the recorded level varied by more than 20 dB between sessions",
           "The afternoon L5 Flight stream sits on the calibration curve; the Patch stream is 14 dB under it")


def fig_overflow_vs_lock(config, outputs_path, channels, overflow_info, outdir) -> dict:
    """Figure 3: C/N0 per PRN with the USRP overflow times and the tracking restarts on top."""
    runs = [r for r in config["tracking_runs"] if "L5" in r["signal"]]
    fig, axes = plt.subplots(len(runs), 1, figsize=SLIDE, sharex=True)
    axes = np.atleast_1d(axes)
    lags = []
    for ax, run_cfg in zip(axes, runs):
        ch = channels[run_cfg["collect_id"]]
        signal = getattr(signal_interfaces, run_cfg["signal"])
        tracks = read_tracking(tracking_path_for(config, outputs_path, ch, signal))
        info, period, over = overflow_info[run_cfg["folder"]]
        stream_label = next(s["antenna"] for s in info["channels"]
                            if f"/{run_cfg['stream_dir']}/" in s["data_file"])
        t_over = over[stream_label]
        for sid, tr in tracks.items():
            ax.plot(tr["cn0_uptime_s"], tr["cn0_dbhz"], lw=1.8, color=PRN_COLORS.get(sid, MUTED), label=sid)
        for t in t_over:
            ax.axvline(t, color=EVENT, ls="--", lw=1.2, alpha=0.8)
        starts = np.sort(np.concatenate([segment_starts_s(tr["uptime_s"])[1:] for tr in tracks.values()]))
        ax.scatter(starts, np.full(len(starts), 11.5), marker="^", color=EVENT, s=40, zorder=5)
        for t in t_over:
            if len(starts):
                lags.append(float(starts[np.argmin(np.abs(starts - t))] - t))
        ax.set(ylabel="C/N0 [dB-Hz]", ylim=(10, 55))
        ax.set_title(f"{run_cfg['antenna']} antenna: {len(tracks)} satellites tracked", loc="left")
        handles, labels = ax.get_legend_handles_labels()
        handles += [Line2D([], [], color=EVENT, ls="--", lw=1.2), Line2D([], [], color=EVENT, marker="^", ls="", ms=8)]
        labels += ["USRP overflow", "channel restarted"]
        ax.legend(handles, labels, ncol=7, fontsize=11, loc="lower right", bbox_to_anchor=(1.0, 1.0), frameon=False)
    axes[-1].set_xlabel("Time in the collect [s]")
    finish(fig, outdir, "03_overflow_vs_lock",
           "Every USRP overflow broke lock on all satellites, and each was re-acquired within about a second",
           f"L5 collect at 14:54: C/N0 of each tracked satellite; restarts follow overflows by "
           f"{min(lags):.1f} to {max(lags):.1f} s")
    return {"lag_s": lags}


def fig_dual_stream(config, outputs_path, channels, outdir) -> None:
    """Figure 4: Flight vs Patch tracking, and the 14:49 L1CA Flight acquisition rescue."""
    fig, axes = plt.subplots(1, 3, figsize=SLIDE, gridspec_kw={"width_ratios": [1.15, 1.15, 1.0]})
    for ax, band in zip(axes[:2], ("GpsL5", "GpsL1CA")):
        runs = [r for r in config["tracking_runs"] if r["signal"] == band]
        data = {}
        for r in runs:
            ch = channels[r["collect_id"]]
            tracks = read_tracking(tracking_path_for(config, outputs_path, ch, getattr(signal_interfaces, band)))
            for sid, tr in tracks.items():
                good = np.isfinite(tr["cn0_dbhz"]) & (tr["cn0_uptime_s"] > 0)
                span = float(tr["uptime_s"][-1] - tr["uptime_s"][0]) if len(tr["uptime_s"]) > 1 else 0.0
                data[(r["antenna"], sid)] = (float(np.nanmedian(tr["cn0_dbhz"][good])) if good.any() else np.nan, span)
        prns = sorted({sid for _, sid in data})
        x = np.arange(len(prns))
        for k, antenna in enumerate(("Flight", "Patch")):
            xs = x + (k - 0.5) * 0.38
            vals = [data.get((antenna, sid), (np.nan, 0.0))[0] for sid in prns]
            ax.bar(xs, np.nan_to_num(vals), 0.38, color=ANTENNA_COLOR[antenna], label=antenna)
            for xi, sid in zip(xs, prns):
                if (antenna, sid) not in data:
                    ax.text(xi, 1.5, "not acquired", ha="center", fontsize=10, rotation=90, color=MUTED)
                elif not np.isfinite(data[(antenna, sid)][0]):
                    ax.text(xi, 1.5, f"lost after {data[(antenna, sid)][1]:.1f} s", ha="center", fontsize=10,
                            rotation=90, color=ANTENNA_COLOR[antenna])
        ax.set_xticks(x)
        ax.set_xticklabels(prns, rotation=90)
        ax.set(ylabel="Median C/N0 [dB-Hz]", ylim=(0, 55))
        ax.set_title(f"{band.replace('Gps', 'GPS ')}, afternoon", loc="left")
        ax.grid(False, axis="x")
        ax.legend(loc="upper right", frameon=False)
    ax = axes[2]
    sweep = config["acquisition_sweep_l1ca_patch"]
    for start, color in zip(sorted({s["start_s"] for s in sweep}), ("#0072B2", "#E69F00", "#009E73")):
        rows = [s for s in sweep if s["start_s"] == start]
        ax.plot([r["dwell_ms"] for r in rows], [r["peak_db"] - r["threshold_db"] for r in rows],
                "o-", color=color, ms=8, label=f"dwell starts at {start} s")
    ax.axhline(0, color=EVENT, lw=1.5)
    ax.axhspan(0, 3, color=L1CA_COLOR, alpha=0.08)
    ax.axhspan(-3, 0, color=PATCH, alpha=0.07)
    ax.text(470, 0.12, "detected", ha="right", color=L1CA_COLOR, fontweight="bold")
    ax.text(470, -0.4, "missed", ha="right", color=PATCH, fontweight="bold")
    ax.set_xscale("log")
    ax.xaxis.set_major_locator(FixedLocator([20, 50, 100, 250, 500]))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.set(xlabel="Acquisition dwell [ms]", ylabel="Loudest peak minus threshold [dB]", ylim=(-1.6, 2.4), xlim=(15, 700))
    ax.set_title("14:49 L1CA Patch acquisition", loc="left")
    ax.legend(fontsize=11, loc="upper left", frameon=False)
    finish(fig, outdir, "04_dual_stream_tracking",
           "The Flight stream carried more satellites; the Patch L1CA stream acquired only with a long dwell",
           "Median C/N0 per satellite on each antenna (left, middle) and the Patch L1CA dwell-length sweep (right)")


def fig_pseudorange_before_after(config, outputs_path, channels, nav_results, overflow_info, outdir) -> None:
    """Figure 5: the code-phase fix, as raw pseudorange of one L5 run."""
    collect_id = config["before_after_run"]
    run_cfg = next(r for r in config["tracking_runs"] if r["collect_id"] == collect_id)
    res = nav_results[collect_id]
    obs = res["obs"]
    t = obs.epoch_uptime_ms * 1e-3
    tracks = read_tracking(tracking_path_for(config, outputs_path, channels[collect_id],
                                             signal_interfaces.GpsL5))
    fig, axes = plt.subplots(1, 2, figsize=SLIDE)
    info = overflow_info[run_cfg["folder"]][0]
    over = overflow_info[run_cfg["folder"]][2][recorder_label_for(config, info, run_cfg["antenna"])]
    worst = {"before": 0.0, "after": 0.0}
    for j, sid in enumerate(obs.sat_ids):
        after = obs.pseudorange_m[:, j]
        starts = segment_starts_s(tracks[sid]["uptime_s"])
        first = np.searchsorted(tracks[sid]["uptime_s"], starts)
        seed_periods = np.floor(tracks[sid]["code_phase_ms"][first] / L5_CODE_PERIOD_MS)
        seg = np.clip(np.searchsorted(starts, t, side="right") - 1, 0, len(starts) - 1)
        before = after + SPEED_OF_LIGHT_M_S * L5_CODE_PERIOD_MS * 1e-3 * seed_periods[seg]
        for ax, series, key in ((axes[0], before, "before"), (axes[1], after, "after")):
            change_km = (series - series[np.isfinite(series)][0]) * 1e-3
            ax.plot(t, change_km, lw=2.2, color=PRN_COLORS[sid], label=sid)
            worst[key] = max(worst[key], float(np.nanmax(np.abs(change_km))))
    ratio = worst["before"] / worst["after"]
    thousands = FuncFormatter(lambda v, _: f"{v:,.0f}")
    for ax in axes:
        for tt in over:
            ax.axvline(tt, color=EVENT, ls="--", lw=1.0, alpha=0.7)
        ax.yaxis.set_major_formatter(thousands)
        ax.set_xlabel("Time in the collect [s]")
        ax.set_ylabel("Raw pseudorange change since the start [km]")
    axes[0].set_title("Before: each restart dropped whole 20 ms code periods", loc="left")
    axes[1].set_title("After: only the few-ms steps the overflows caused", loc="left")
    handles, labels = axes[1].get_legend_handles_labels()
    handles.append(Line2D([], [], color=EVENT, ls="--", lw=1.0))
    labels.append("USRP overflow")
    axes[0].legend(handles, labels, ncol=5, loc="upper left", frameon=False, fontsize=11)
    axes[0].text(0.03, 0.5, f"largest excursion:\n{worst['before']:,.0f} km", transform=axes[0].transAxes, fontsize=14,
                 color="0.3")
    axes[1].text(0.03, 0.78, f"largest excursion:\n{worst['after']:,.0f} km", transform=axes[1].transAxes, fontsize=14,
                 color="0.3")
    finish(fig, outdir, "05_pseudorange_before_after_fix",
           f"Seeding each restart with the unwrapped code phase shrank the pseudorange jumps about "
           f"{round(ratio, -3):,.0f}-fold",
           f"Raw pseudorange of the four L5 {run_cfg['antenna']} satellites; the left panel is rebuilt from the same tracking "
           "by undoing the fix")


def fig_ledger(config, collects_dir, nav_results, overflow_info, outdir) -> list:
    """Figure 6: what each overflow cost, from the sidecar and from the tracking."""
    runs = [r for r in config["tracking_runs"]
            if r["signal"] == "GpsL5" and nav_results.get(r["collect_id"], {}).get("ledger") is not None]
    fig, axes = plt.subplots(1, 3, figsize=SLIDE, gridspec_kw={"width_ratios": [1.0, 1.0, 1.0]})
    rows = []
    ax = axes[0]
    markers = {"Flight": "o", "Patch": "s"}
    slip = None
    for r in runs:
        res = nav_results[r["collect_id"]]
        led, clusters = res["ledger"], res["clusters"]
        info, period, over = overflow_info[r["folder"]]
        rec = read_usrp_sidecar(stream_sidecar(collects_dir, config["experiment"], r["folder"], r["stream_dir"]))
        idx = overflow_events(rec, period)[0]
        side = np.array([sidecar_anomaly_ms(rec, [int(idx[i]) for i in c], period) for c in clusters])
        track = -led["common_km"] * 1e3 / SPEED_OF_LIGHT_M_S * 1e3  # km -> ms; sign: matches the sidecar
        ax.scatter(side, track, marker=markers[r["antenna"]], s=170 if r["antenna"] == "Flight" else 90,
                   color=ANTENNA_COLOR[r["antenna"]], edgecolor="k", label=f"{r['antenna']} stream", zorder=3)
        for e in range(len(clusters)):
            rows.append((r["antenna"], led["event_time_s"][e], side[e], track[e]))
        final = led["cumulative_km"][-1] if len(led["cumulative_km"]) else np.zeros(0)
        if len(final) and np.nanmax(np.abs(final)) > config["consistency_threshold_km"]:
            j = int(np.nanargmax(np.abs(final)))
            slip = (res["sat_ids"][j], r["antenna"], abs(final[j]) / (SPEED_OF_LIGHT_M_S * L5_CODE_PERIOD_MS * 1e-6))
    lim = 10.5
    ax.plot([-lim, lim], [-lim, lim], color=EVENT, ls="--", lw=1.2, label="equal")
    ax.set(xlim=(-lim, lim), ylim=(-lim, lim), aspect="equal",
           xlabel="Time-stamp anomaly in the USRP sidecar [ms]", ylabel="Step seen by the tracking [ms]")
    ax.set_title("Each overflow, two ways", loc="left")
    ax.legend(loc="upper left", frameon=False)
    for ax, r in zip(axes[1:], runs):
        res = nav_results[r["collect_id"]]
        led = res["ledger"]
        for j, sid in enumerate(res["sat_ids"]):
            ax.step(np.concatenate(([0], led["event_time_s"])),
                    np.concatenate(([0], led["cumulative_km"][:, j])), where="post", label=sid,
                    color=PRN_COLORS.get(sid, MUTED), lw=2.4)
        thr = config["consistency_threshold_km"]
        ax.axhspan(-thr, thr, color=L1CA_COLOR, alpha=0.15)
        if np.nanmax(np.abs(led["cumulative_km"])) < thr:
            ax.set_ylim(-1.35 * thr, 1.35 * thr)
        ax.text(2, thr * 0.5, "consistent", color=L1CA_COLOR, fontsize=12, fontweight="bold", va="center")
        ax.set(xlabel="Time in the collect [s]", ylabel="Offset from the other satellites [km]")
        ax.set_title(f"{r['antenna']}: each satellite vs. the others", loc="left")
        ax.legend(ncol=3, fontsize=11, loc="lower left", frameon=False)
    subtitle = ("The tracking also exposes one bad re-lock: "
                f"{slip[1]} {slip[0]} slipped by {slip[2]:.1f} code periods (20 ms each) across the 72 s and 77 s events"
                if slip else "No satellite slipped against the others")
    finish(fig, outdir, "06_overflow_ledger",
           "Each overflow cost a few milliseconds of data, and the sidecar and tracking agree to about 0.01 ms",
           subtitle)
    return rows


def fig_positions_time(config, nav_results, overflow_info, outdir) -> list:
    """Figure 7: position solutions over time, in three variants."""
    runs = [r for r in config["tracking_runs"] if r["collect_id"] in nav_results]
    fig, axes = plt.subplots(len(runs), 1, figsize=SLIDE, sharex=True)
    axes = np.atleast_1d(axes)
    rows = []
    labels = {"all": "all epochs", "settled": f"settled (PLL, >{config['settle_s']:g} s after a restart)",
              "consistent": "settled + consistent satellites"}
    plain = FuncFormatter(lambda v, _: f"{v:,.0f}")
    for ax, r in zip(axes, runs):
        res = nav_results[r["collect_id"]]
        antenna = r["antenna"]
        info, period, over = overflow_info[r["folder"]]
        t_over = over[recorder_label_for(config, info, antenna)]
        colors = {"all": "#CFCFCF", "settled": "#6E6E6E", "consistent": ANTENNA_COLOR[antenna]}
        sizes = {"all": 9, "settled": 6, "consistent": 4}
        spread = []
        for name in ("all", "settled", "consistent"):
            sol = res[name]
            if sol is None:
                continue
            good = sol["valid"] & np.isfinite(sol["enu"][:, 0])
            horiz = np.hypot(sol["enu"][:, 0], sol["enu"][:, 1])
            ax.plot(sol["time_s"][good], np.maximum(horiz[good], 0.1), ".", ms=sizes[name],
                    color=colors[name], label=labels[name])
            spread.append(horiz[good])
            med = np.nanmedian(sol["enu"][good], axis=0) if good.any() else np.full(3, np.nan)
            mad = (1.4826 * np.nanmedian(np.abs(sol["enu"][good] - med), axis=0)) if good.any() else np.full(3, np.nan)
            rows.append([r["collect_id"], antenna, name, int(good.sum()), len(good), *med, *mad])
        flat = np.concatenate(spread) if spread else np.array([1.0, 2.0])
        if flat.max() / max(flat.min(), 0.1) > 8:
            ax.set_yscale("log")
        for tt in t_over:
            ax.axvline(tt, color=EVENT, ls="--", lw=1.0, alpha=0.6)
        excluded = {s: t for s, t in res["bad_from_s"].items() if np.isfinite(t)}
        note = ("   (" + ", ".join(f"{s} left out from {t:.0f} s" for s, t in excluded.items()) + ")") if excluded else ""
        ax.set(ylabel="Horizontal offset [m]")
        ax.yaxis.set_major_formatter(plain)
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.set_title(f"{r['signal'].replace('Gps', 'GPS ')} {antenna}{note}", loc="left")
        ax.grid(True, which="both", alpha=0.2)
        if excluded:
            ax.text(0.62, 0.5, "one bad re-lock corrupts every later epoch\nuntil that satellite is left out",
                    transform=ax.transAxes, fontsize=12, color="0.25", va="center")
        if ax is axes[0]:
            ax.legend(loc="upper left", frameon=False, ncol=3, markerscale=2)
    axes[-1].set_xlabel("Time in the collect [s]")
    finish(fig, outdir, "07_position_over_time",
           "Position error is set by the restarts, not by the signal",
           "Horizontal offset from an unsurveyed reference, per 100 ms epoch; dashed lines are USRP overflows")
    return rows


def fig_positions_map(config, nav_results, outdir) -> None:
    """Figure 8: where the three solutions say the antenna was."""
    runs = [r for r in config["tracking_runs"] if r["collect_id"] in nav_results]
    half = float(config["map_half_extent_m"])
    fig = plt.figure(figsize=SLIDE)
    grid = fig.add_gridspec(1, 2, width_ratios=[1.0, 1.05])
    ax, ax_info = fig.add_subplot(grid[0]), fig.add_subplot(grid[1])
    extent = (-half, half, -half, half)
    try:
        from utils import basemap as basemap_module
        bm = basemap_module.fetch_satellite_basemap(config["reference_geo"][0], config["reference_geo"][1], half)
        ax.imshow(bm.image, extent=bm.extent_m, origin="upper", zorder=0)
        extent = bm.extent_m
    except Exception as exc:  # no imagery is not a reason to lose the figure
        print(f"  basemap unavailable ({type(exc).__name__}); plotting without it")
    colors = {"20261001_204944_l1ca_patch_120s": "#00E0A0", "20261001_205422_l5_flight_120s": "#FF8A3D",
              "20261001_205422_l5_patch_120s": "#4FC3FF"}
    names = {r["collect_id"]: f"{'L5' if 'L5' in r['signal'] else 'L1 C/A'}, {r['antenna']}"
             for r in config["tracking_runs"]}
    medians, table_rows, swatches = {}, [], []
    for r in runs:
        sol = nav_results[r["collect_id"]]["consistent"]
        if sol is None:
            continue
        good = sol["valid"] & np.isfinite(sol["enu"][:, 0])
        enu = sol["enu"][good]
        e, n = enu[:, 0], enu[:, 1]
        c = colors[r["collect_id"]]
        ax.scatter(e, n, s=14, color=c, alpha=0.55, zorder=3, edgecolor="none")
        med = np.median(enu, axis=0)
        sig = 1.4826 * np.median(np.abs(enu - med), axis=0)
        ax.plot(med[0], med[1], "o", ms=15, mfc=c, mec="k", mew=2, zorder=5)
        medians[r["collect_id"]] = med
        table_rows.append([names[r["collect_id"]], f"{med[0]:+.0f}", f"{med[1]:+.0f}", f"{med[2]:+.0f}",
                           f"{np.hypot(sig[0], sig[1]):.0f}", f"{int(good.sum()):,}"])
        swatches.append(c)
    ax.plot(0, 0, "*", ms=22, mfc="w", mec="k", mew=1.5, zorder=6)
    ax.annotate("reference\n(unsurveyed)", (0, 0), textcoords="offset points", xytext=(-10, 16), ha="right",
                fontsize=13, color="w", fontweight="bold",
                path_effects=[patheffects.withStroke(linewidth=3, foreground="black")])
    ax.set(xlim=extent[:2], ylim=extent[2:], aspect="equal", xlabel="East of the reference [m]",
           ylabel="North of the reference [m]")
    ax.grid(True, color="w", alpha=0.25)
    ax_info.axis("off")
    header = ["Solution", "East\n[m]", "North\n[m]", "Up\n[m]", "Scatter\n[m]", "Epochs"]
    table = ax_info.table(cellText=table_rows, colLabels=header, loc="upper center", cellLoc="center",
                          colWidths=[0.30, 0.14, 0.14, 0.14, 0.15, 0.13])
    table.auto_set_font_size(False)
    table.set_fontsize(14)
    table.scale(1, 3.0)
    for (rr, cc), cell in table.get_celld().items():
        cell.set_edgecolor("white")
        if rr == 0:
            cell.set_facecolor("#33415C")
            cell.set_text_props(color="white", fontweight="bold")
        else:
            cell.set_facecolor("#F4F4F4")
            if cc == 0:
                cell.set_facecolor(swatches[rr - 1])
                cell.set_text_props(fontweight="bold")
    meds = np.array(list(medians.values()))
    pair = max(np.hypot(*(a[:2] - b[:2])) for a in meds for b in meds)
    mean = meds.mean(axis=0)
    dist = float(np.hypot(mean[0], mean[1]))
    bearing = (np.degrees(np.arctan2(mean[0], mean[1])) + 360) % 360
    compass = ["north", "north-north-east", "north-east", "east-north-east", "east", "east-south-east",
               "south-east", "south-south-east", "south", "south-south-west", "south-west", "west-south-west",
               "west", "west-north-west", "north-west", "north-north-west"][int(((bearing + 11.25) % 360) // 22.5)]
    ax_info.text(0.02, 0.60,
                 "Median of the settled fixes from consistent satellites.\n"
                 "Scatter = robust horizontal spread (1.4826 x median absolute deviation).\n\n"
                 f"The three medians lie within {pair:.0f} m of each other horizontally.\n"
                 f"Their mean lies {dist:.0f} m {compass} of the reference (compass bearing {bearing:.0f} deg).\n"
                 "The vertical offsets differ by band: L1 and L5 do not agree in height.",
                 transform=ax_info.transAxes, fontsize=13, va="top", color="0.2", linespacing=1.6)
    finish(fig, outdir, "08_position_map",
           f"Three independent position solutions agree to within {pair:.0f} m",
           "Different bands, antennas and satellites, all from one B210: settled fixes on satellite imagery")


# --------------------------------------------------------------------------- main
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "configs" / "oct01_story_figures.yml")
    args = parser.parse_args()
    config = load_config(args.config)

    collects_dir = utils.environment_variables.get_collects_path()
    outputs_path = Path(utils.environment_variables.get_outputs_path())
    outdir = collects_dir / config["experiment"] / "Figures" / config["output_subdir"]
    outdir.mkdir(parents=True, exist_ok=True)
    for stale in list(outdir.glob("0[0-8]_*.png")) + list(outdir.glob("0[0-8]_*.svg")):
        stale.unlink()
    utils.plotting.setup_default_plotting()
    plt.rcParams.update(STYLE)

    all_channels = {c.collect_id: c for c in catalog.list_channels(collects_dir)
                    if c.experiment_name == config["experiment"]}
    channels = {r["collect_id"]: all_channels[r["collect_id"]] for r in config["tracking_runs"]}

    print("Figure 1: logging overview")
    overflow_info = fig_logging_overview(config, collects_dir, outdir)
    print("Figure 0: scorecard")
    fig_scorecard(config, collects_dir, outputs_path, overflow_info, outdir)
    print("Figure 2: recorded level versus gain")
    fig_adc_scaling(config, collects_dir, outdir)
    print("Figure 3: overflow versus lock")
    stats = fig_overflow_vs_lock(config, outputs_path, channels, overflow_info, outdir)
    print(f"  tracking restarts follow overflows by {min(stats['lag_s']):.2f} to {max(stats['lag_s']):.2f} s")
    print("Figure 4: dual-stream tracking")
    fig_dual_stream(config, outputs_path, channels, outdir)

    nav_results = {}
    for r in config["tracking_runs"]:
        if not r.get("navigate"):
            continue
        print(f"Navigation: {r['collect_id']}")
        info, period, over = overflow_info[r["folder"]]
        t_over = over[next(s["antenna"] for s in info["channels"] if f"/{r['stream_dir']}/" in s["data_file"])]
        res = run_navigation(config, collects_dir, outputs_path, r, channels[r["collect_id"]], t_over)
        if res is not None:
            nav_results[r["collect_id"]] = res
    print("Figure 5: pseudorange before and after the fix")
    fig_pseudorange_before_after(config, outputs_path, channels, nav_results, overflow_info, outdir)
    print("Figure 6: overflow ledger")
    ledger_rows = fig_ledger(config, collects_dir, nav_results, overflow_info, outdir)
    print("  stream, event time [s], sidecar anomaly [ms], tracking step [ms]")
    for row in ledger_rows:
        print(f"  {row[0]:7s} {row[1]:7.1f} {row[2]:9.3f} {row[3]:9.3f}")
    print("Figure 7: position over time")
    rows = fig_positions_time(config, nav_results, overflow_info, outdir)
    print("Figure 8: position map")
    fig_positions_map(config, nav_results, outdir)
    print("\ncollect, antenna, variant, valid/total, median E,N,U [m], robust sigma E,N,U [m]")
    for row in rows:
        print(f"  {row[0]:32s} {row[1]:7s} {row[2]:10s} {row[3]:5d}/{row[4]:<5d} "
              f"med {row[5]:10.1f} {row[6]:10.1f} {row[7]:10.1f}  sig {row[8]:9.1f} {row[9]:9.1f} {row[10]:9.1f}")
    print(f"\nFigures in {outdir}")


if __name__ == "__main__":
    main()
