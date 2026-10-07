"""Figure: acquisition of the 14:49 L1CA streams at different dwell times.

Runs the notebook-01 acquisition on the same time span of each stream (Flight
and Patch, recorded together on one B210) with a longer and longer
non-coherent dwell, and shows for each dwell:

* the peak of every PRN against the detection threshold, for each antenna;
* the correlation against code phase of two chosen satellites, with the two
  antennas on the same axes (they share one sample clock, so the code phase of
  a satellite is the same on both).

Settings come from `configs/oct01_story_figures.yml` (key `dwell_study`). Run:

    python scripts/l1ca_dwell_figure.py [--config PATH]
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Reuse the style, palette and `finish` of the story script.
_spec = importlib.util.spec_from_file_location("story", REPO_ROOT / "scripts" / "oct01_story_figures.py")
story = importlib.util.module_from_spec(_spec)
sys.modules["story"] = story
_spec.loader.exec_module(story)

from utils import bpsk_acquisition, catalog, sample_streaming  # noqa: E402
from utils.signal_interfaces import GpsL1CA, build_acquisition_code_params, build_signals  # noqa: E402

REQUIRED = ("streams", "start_s", "coherent_replica_ms", "coherent_sample_ms", "dwell_ms",
            "highlight_prns", "doppler_search_hz", "p_fa_total", "fine_search")


def load_study(config: dict) -> dict:
    """The `dwell_study` block of the config, checked."""
    study = config.get("dwell_study")
    if study is None:
        raise SystemExit("config has no `dwell_study` block")
    missing = [key for key in REQUIRED if key not in study]
    if missing:
        raise SystemExit(f"dwell_study: missing keys: {', '.join(missing)}")
    for stream in study["streams"]:
        if stream["antenna"] not in story.ANTENNA_COLOR:
            raise SystemExit(f"dwell_study: unknown antenna {stream['antenna']!r}")
    return study


def acquire(channel, study: dict, dwell_ms: float, code_params) -> dict:
    """Run acquisition on one dwell of the stream; return the per-PRN results."""
    fine = bpsk_acquisition.FineSearchParameters(**study["fine_search"])
    num_blocks = int(round(dwell_ms / study["coherent_sample_ms"]))
    cfg = bpsk_acquisition.AcquisitionConfiguration(
        coherent_duration_replica_ms=study["coherent_replica_ms"],
        coherent_duration_sample_ms=study["coherent_sample_ms"],
        num_blocks=num_blocks, sample_rate=channel.samp_rate,
        min_search_doppler_hz=-study["doppler_search_hz"], max_search_doppler_hz=study["doppler_search_hz"],
        fine_search=fine,
    )
    params = channel.sample_params
    n = int(channel.samp_rate * cfg.acq_total_duration_ms / 1e3)
    start_sample = int(round(channel.samp_rate * study["start_s"]))
    offset = sample_streaming.compute_sample_offset_bytes(start_sample, params.bit_depth, params.is_complex)
    raw_bytes = bytearray(sample_streaming.compute_sample_array_size_bytes(n, params.bit_depth, params.is_complex))
    with open(channel.filepath, "rb") as handle:
        handle.seek(offset)
        handle.readinto(raw_bytes)
    raw = np.zeros(n, dtype=np.complex64)
    baseband = np.zeros(n, dtype=np.complex64)
    sample_streaming.convert_to_complex64_samples(raw_bytes, raw, params)
    sample_streaming.mixdown_samples(raw, baseband, channel.samp_rate, initial_phase_cycles=0.0,
                                     freq_hz=channel.inter_freq_hz)
    baseband -= np.mean(baseband)
    return bpsk_acquisition.run_acquisition(
        sample_block=baseband, sample_block_uptime_epoch_ms=0.0, acq_config=cfg,
        code_parameters=code_params, prob_false_alarm_total=study["p_fa_total"],
        print_progress=False, noise_var_method="abscorrvar",
    )


def code_phase_slice(result, chip_rate: float) -> tuple[np.ndarray, np.ndarray]:
    """Correlation against code phase at the peak Doppler, in dB above the noise level."""
    corr = result.correlation_result
    matrix = np.asarray(corr.correlation_matrix, dtype=float)
    # Acquisition keeps only a window of Doppler rows around the peak; the peak is its centre row.
    full = matrix.shape[0] == result.config.num_doppler_hypotheses
    row = matrix[result.peak_doppler_bin if full else matrix.shape[0] // 2]
    snr_db = 10.0 * np.log10(np.maximum(row, 1e-30) / result.noise_var / (2 * result.config.num_blocks))
    return np.asarray(corr.code_phase_bins_seconds) * chip_rate, snr_db


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "configs" / "oct01_story_figures.yml")
    args = parser.parse_args()
    config = story.load_config(args.config)
    study = load_study(config)

    collects_dir = story.utils.environment_variables.get_collects_path()
    outdir = collects_dir / config["experiment"] / "Figures" / config["output_subdir"]
    outdir.mkdir(parents=True, exist_ok=True)
    story.utils.plotting.setup_default_plotting()
    plt.rcParams.update(story.STYLE)

    channels = {c.collect_id: c for c in catalog.list_channels(collects_dir)}
    signals = build_signals(GpsL1CA)
    code_params = build_acquisition_code_params(GpsL1CA, signals)
    chip_rate = next(iter(code_params.values())).rate_chips_per_sec

    dwells = list(study["dwell_ms"])
    highlights = list(study["highlight_prns"])
    streams = study["streams"]
    results = {}
    for stream in streams:
        for dwell in dwells:
            print(f"{stream['antenna']}, dwell {dwell} ms", flush=True)
            results[(stream["antenna"], dwell)] = acquire(channels[stream["collect_id"]], study, dwell, code_params)

    n_rows = len(streams) + len(highlights)
    fig, axes = plt.subplots(n_rows, len(dwells), figsize=story.SLIDE,
                             gridspec_kw={"height_ratios": [1.1] * len(streams) + [1.0] * len(highlights)})
    prns = sorted(next(iter(results.values())).keys())
    x = np.arange(len(prns))
    row_top = {s["antenna"]: 1.0 + max(max(r.peak_snr_db for r in results[(s["antenna"], d)].values())
                                       for d in dwells) for s in streams}
    for col, dwell in enumerate(dwells):
        for row, stream in enumerate(streams):
            res = results[(stream["antenna"], dwell)]
            ax = axes[row, col]
            peaks = np.array([res[p].peak_snr_db for p in prns])
            found = np.array([res[p].signal_detected for p in prns])
            threshold = next(iter(res.values())).detection_threshold_db
            ax.bar(x, peaks, color=np.where(found, story.ANTENNA_COLOR[stream["antenna"]], "#C9C9C9"), width=0.8)
            ax.axhline(threshold, color=story.EVENT, ls="--", lw=1.5)
            ax.set_xticks(x[::4])
            ax.set_xticklabels(prns[::4], rotation=90, fontsize=9)
            ax.set_ylim(0, max(12.0, row_top[stream["antenna"]]))
            names = ", ".join(p for p, f in zip(prns, found) if f) or "none"
            ax.set_title(f"{stream['antenna']}: {int(found.sum())} detected\n({names})", loc="left", fontsize=11)
            if col == 0:
                ax.set_ylabel("Peak above\nnoise [dB]")
            if row == 0:
                # Placed in points above the two-line title, and kept out of tight_layout.
                label = ax.annotate(f"{dwell:g} ms dwell", xy=(0.5, 1.0), xycoords="axes fraction",
                                    xytext=(0, 40), textcoords="offset points", ha="center",
                                    fontsize=15, fontweight="bold")
                label.set_in_layout(False)
        for k, prn in enumerate(highlights):
            axs = axes[len(streams) + k, col]
            top = 0.0
            for stream in streams:
                res = results[(stream["antenna"], dwell)][prn]
                phase_chips, snr = code_phase_slice(res, chip_rate)
                axs.plot(phase_chips, snr, color=story.ANTENNA_COLOR[stream["antenna"]], lw=0.9, alpha=0.85,
                         label=stream["antenna"])
                axs.axhline(res.detection_threshold_db, color=story.ANTENNA_COLOR[stream["antenna"]], ls="--", lw=1.1)
                top = max(top, float(np.max(snr)))
            axs.set_xlim(0, 1023)
            axs.set_ylim(-4, max(14.0, top + 1.0))
            if col == 0:
                axs.set_ylabel(f"{prn} [dB]")
            if k == 0 and col == len(dwells) - 1:
                # Last column: the upper right is empty there, so the legend hides no data.
                axs.legend(loc="upper right", fontsize=10, frameon=False, ncol=2)
            if k == len(highlights) - 1:
                axs.set_xlabel("Code phase [chips]")
            else:
                axs.set_xticklabels([])

    def first_dwell(antenna: str, count: int):
        """Shortest dwell at which this antenna detects at least `count` satellites."""
        return next((d for d in dwells
                     if sum(r.signal_detected for r in results[(antenna, d)].values()) >= count), None)

    # The stream that detects more at the shortest dwell is the strong one.
    count0 = {s["antenna"]: sum(r.signal_detected for r in results[(s["antenna"], dwells[0])].values())
              for s in streams}
    strong, weak = sorted(count0, key=count0.get, reverse=True)
    f1, f2 = first_dwell(weak, 1), first_dwell(weak, 2)
    headline = (f"At {dwells[0]:g} ms the {strong} stream detects {count0[strong]} satellites; "
                f"the {weak} stream needs {f1:g} ms for one and {f2:g} ms for two"
                if f1 and f2 else "Acquisition of the 14:49 L1CA streams versus dwell time")
    story.finish(fig, outdir, "09_l1ca_acquisition_vs_dwell", headline,
                 f"14:49 L1CA, both antennas, same {study['start_s']:g} s start in the file. Coloured bars: detected.",
                 note="Lower rows: correlation against code phase at the best Doppler, antennas overlaid; "
                      "dashed lines: thresholds. Both antennas peak at the same code phase (one B210 sample clock).",
                 top=0.85)
    print(f"Saved to {outdir}")


if __name__ == "__main__":
    main()
