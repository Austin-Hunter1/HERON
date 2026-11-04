"""Extract small teaching data sets from HERON tracking outputs.

The GNSS course page (docs/teaching/gnss_course.html) shows real HERON
tracking results. The page must work with no network and no Python, so
this script reads the large tracking HDF5 files once and writes one
small JSON file that the page embeds.

Run it from the gnss_processing environment, because that environment
has h5py and Pillow:

    cd gnss_processing
    uv run python ../docs/teaching/tools/extract_heron_data.py \
        --tracking-dir D:/01_Data/outputs/tracking/20261013_Jiawei_Testing_x2 \
        --reports-dir  D:/01_Data/outputs/reports/20261013_Jiawei_Testing_x2

All paths and choices are arguments, so a different collect can feed
the page without code changes.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
from pathlib import Path

import h5py
import numpy as np
from PIL import Image

# Signals to export: (label, file glob, carrier frequency in Hz).
# The carrier frequency converts carrier cycles to metres.
SIGNALS = [
    ("GPS L1 C/A", "*GPS_L1CA_0-120s.h5", 1575.42e6),
    ("GPS L1C", "*GPS_L1C_0-120s.h5", 1575.42e6),
    ("BDS B1C", "*BDS_B1C_0-120s.h5", 1575.42e6),
    ("GPS L5", "*GPS_L5_0-120s.h5", 1176.45e6),
    ("GAL E5a", "*GAL_E5A_0-120s.h5", 1176.45e6),
    ("BDS B2a", "*BDS_B2A_0-120s.h5", 1176.45e6),
]

# Report figures to embed: (key, relative glob under the reports dir).
FIGURES = [
    (
        "acq_slices_l1ca",
        "*L1_20261103_132112/figures/01_GpsL1CA/02_acquisition_code_phase_slices.png",
    ),
    (
        "acq_peaks_l1ca",
        "*L1_20261103_132112/figures/01_GpsL1CA/02b_acquisition_peak_snr_all_prns.png",
    ),
    ("acq_dwell_l1ca", "*L1_20261103_132112/figures/01_GpsL1CA/01_acquisition_dwell_layout.png"),
    ("raw_psd_l5", "*L5_*/figures/00_raw/03_welch_psd.png"),
    ("code_doppler_g01", "*L1_20261102_114408/figures/*/04_code_delay_doppler_G01.png"),
]

SPEED_OF_LIGHT = 299_792_458.0


def prompt_index(n_taps: int) -> tuple[int, int, int]:
    """Return the (early, prompt, late) tap indices for a tap layout.

    The HERON tracker writes three taps as (early, prompt, late). It
    writes five taps for BOC signals as (prompt, early, late,
    subcarrier-early, subcarrier-late). See
    gnss_processing/utils/tracking_channel.py.
    """
    if n_taps == 3:
        return 0, 1, 2
    if n_taps == 5:
        return 1, 0, 2
    raise ValueError(f"unknown tap layout with {n_taps} taps")


def best_prn(f: h5py.File) -> str:
    """Pick the satellite with the highest mean C/N0 and a full track."""
    scores = {}
    for key, grp in f.items():
        if isinstance(grp, h5py.Group) and "cn0_dbhz" in grp:
            span = float(grp["uptime_epoch_ms"][-1])
            if span < 110_000:  # Skip tracks that ended early.
                continue
            scores[key] = float(np.nanmean(grp["cn0_dbhz"][:]))
    return max(scores, key=scores.get)


def decimate_mean(t: np.ndarray, y: np.ndarray, step_s: float) -> tuple[list, list]:
    """Average y into bins of step_s seconds to make a light time series."""
    edges = np.arange(t[0], t[-1] + step_s, step_s)
    idx = np.digitize(t, edges)
    tt, yy = [], []
    for k in np.unique(idx):
        m = idx == k
        tt.append(round(float(t[m].mean()), 3))
        yy.append(round(float(np.nanmean(y[m])), 4))
    return tt, yy


def iq_window(t, z, t0, t1, max_pts):
    """Return a thinned list of [t, I, Q] values between t0 and t1."""
    m = (t >= t0) & (t < t1)
    idx = np.flatnonzero(m)
    if idx.size > max_pts:
        idx = idx[np.linspace(0, idx.size - 1, max_pts).astype(int)]
    return [
        [round(float(t[i]), 4), round(float(z[i].real), 3), round(float(z[i].imag), 3)] for i in idx
    ]


def export_signal(path: Path, label: str, f_carrier: float) -> dict:
    """Read one tracking file and return a compact dictionary for the page."""
    with h5py.File(path, "r") as f:
        prn = best_prn(f)
        g = f[prn]
        t = g["uptime_epoch_ms"][:] / 1000.0
        corr = g["corr"][:]
        e_i, p_i, l_i = prompt_index(corr.shape[1])
        # Normalise the correlators by the steady-state prompt magnitude so
        # that all signals plot on the same axes.
        n_comp = corr.shape[2]
        comps = []
        for c in range(n_comp):
            p = corr[:, p_i, c]
            scale = float(np.median(np.abs(p[len(p) // 2 :]))) or 1.0
            comps.append(
                {
                    "scale": scale,
                    "pull_in": iq_window(t, p / scale, t[0], t[0] + 3.0, 1500),
                    "steady": iq_window(t, p / scale, 60.0, 63.0, 1500),
                }
            )
        lam = SPEED_OF_LIGHT / f_carrier
        # Range change from the integrated carrier Doppler. The stored
        # carr_phase_cycles resets once when the loop changes its epoch
        # length, so the page uses the integral of the Doppler instead.
        dop = g["doppler_freq_hz"][:]
        cyc = np.concatenate([[0.0], np.cumsum(0.5 * (dop[1:] + dop[:-1]) * np.diff(t))])
        carr_m = -cyc * lam
        e_mag = np.abs(corr[:, e_i, -1])
        l_mag = np.abs(corr[:, l_i, -1])
        p_mag = np.abs(corr[:, p_i, -1])
        series = {}
        for name, y in [
            ("doppler_hz", g["doppler_freq_hz"][:]),
            ("code_err_chips", g["code_phase_errors_chips"][:]),
            ("carr_err_cyc", g["carr_phase_errors_cycles"][:]),
            ("carr_range_m", carr_m - carr_m[0]),
            ("e_over_p", e_mag / np.maximum(p_mag, 1e-12)),
            ("l_over_p", l_mag / np.maximum(p_mag, 1e-12)),
        ]:
            series[name] = decimate_mean(t, y, 0.25)[1]
        series["t"] = decimate_mean(t, t, 0.25)[0]
        # Jitter: standard deviation of the loop errors in 1 s windows.
        cn0_t = g["cn0_uptime_ms"][:] / 1000.0
        cn0 = g["cn0_dbhz"][:, 0]
        return {
            "label": label,
            "prn": prn,
            "file": path.name,
            "f_carrier_hz": f_carrier,
            "wavelength_m": lam,
            "n_components": n_comp,
            "epoch_ms": sorted({float(x) for x in np.unique(g["epoch_duration_ms"][:])}),
            "cn0_mean": round(float(np.nanmean(cn0)), 1),
            "cn0": {
                "t": [round(float(x), 2) for x in cn0_t],
                "v": [round(float(x), 2) for x in cn0],
            },
            "series": series,
            "iq": comps,
            "code_err_std_chips": round(
                float(np.nanstd(g["code_phase_errors_chips"][len(t) // 4 :])), 4
            ),
            "carr_err_std_cyc": round(
                float(np.nanstd(g["carr_phase_errors_cycles"][len(t) // 4 :])), 4
            ),
        }


def encode_png(path: Path, max_w: int) -> str:
    """Shrink a figure and return it as a data URI to embed in the page."""
    im = Image.open(path).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=82)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


def clean(obj):
    """Replace NaN and infinite floats with None, recursively."""
    if isinstance(obj, float):
        return obj if np.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    return obj


def main() -> None:
    """Parse arguments, export the data, and write the JSON file."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tracking-dir", type=Path, required=True)
    ap.add_argument("--reports-dir", type=Path, required=True)
    ap.add_argument(
        "--out", type=Path, default=Path(__file__).parents[1] / "src" / "data" / "heron_data.json"
    )
    ap.add_argument("--fig-width", type=int, default=1000)
    args = ap.parse_args()

    out = {"collect": args.tracking_dir.name, "signals": [], "figures": {}}
    for label, pattern, fc in SIGNALS:
        hits = sorted(args.tracking_dir.glob(pattern))
        if not hits:
            print(f"skip {label}: no file matches {pattern}")
            continue
        sig = export_signal(hits[0], label, fc)
        print(f"{label}: {sig['prn']} C/N0 {sig['cn0_mean']} dB-Hz")
        out["signals"].append(sig)
    for key, pattern in FIGURES:
        hits = sorted(args.reports_dir.glob(pattern))
        if hits:
            out["figures"][key] = encode_png(hits[0], args.fig_width)
        else:
            print(f"skip figure {key}: no file matches {pattern}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # JSON has no NaN. Write null, which the page treats as a gap.
    args.out.write_text(json.dumps(clean(out), separators=(",", ":"), allow_nan=False))
    print(f"wrote {args.out} ({args.out.stat().st_size / 1e3:.0f} kB)")


if __name__ == "__main__":
    main()
