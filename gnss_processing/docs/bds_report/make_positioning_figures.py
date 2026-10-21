"""
Positioning figures on a synthetic world with a known truth (the fixture of
`tests/test_navigation_multisystem.py`), and on the real B2a recording.

    python docs/bds_report/make_positioning_figures.py [TRACKING_FILE.h5]

Without a tracking file only the synthetic figures are made.  The real recording is
`l5_novatel_120s_BDS_B2A_0-120s.h5` (collect 20261014_011745_l5_novatel_120s).
"""

from __future__ import annotations

import dataclasses
import sys
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")
OUT = Path(__file__).resolve().parent / "figures"
OUT.mkdir(exist_ok=True)

from tests.test_navigation_multisystem import (  # noqa: E402
    INTER_SYSTEM_BIAS_M, REFERENCE_ECEF, SETTINGS, build_two_system_truth,
)
from utils import navigation as nav  # noqa: E402
from utils import observables as obs  # noqa: E402
from utils import time_systems  # noqa: E402
from utils.nav.ephemeris import BdsEphemeris  # noqa: E402

plt.rcParams.update({"font.size": 9, "axes.grid": True, "grid.alpha": 0.3, "figure.dpi": 150,
                     "axes.spines.top": False, "axes.spines.right": False})
BLUE, ORANGE, GREEN, RED, GREY = "#1f5fa8", "#d9822b", "#2e8b57", "#c0392b", "#777777"
EPOCH_MS = 100.0  # the notebook default measurement interval
RAIM_GATE_M = 100.0  # the notebook default residual gate


def save(fig, name):
    fig.savefig(OUT / f"{name}.png", bbox_inches="tight")
    plt.close(fig)
    print("wrote", name)


def az_el(ecef_sat, ecef_rx):
    enu = nav.ecf2enu(ecef_rx, np.atleast_2d(ecef_sat))[0]
    az = np.degrees(np.arctan2(enu[0], enu[1])) % 360
    el = np.degrees(np.arctan2(enu[2], np.hypot(enu[0], enu[1])))
    return az, el


def skyplot(ax, items, title):
    ax.set_theta_zero_location("N"); ax.set_theta_direction(-1)
    ax.set_rlim(90, 0); ax.set_rticks([30, 60]); ax.set_title(title, fontsize=8, pad=12)
    for name, az, el, color in items:
        ax.plot(np.radians(az), el, "o", color=color, ms=5)
        ax.text(np.radians(az), el, " " + name, fontsize=6.5, color=color)


def synthetic():
    ephs, truth = build_two_system_truth(num_epochs=1)
    pos = nav.satellite_positions(truth, ephs, apply_sagnac=True, receiver_position_ecef_m=REFERENCE_ECEF)
    items = []
    for j, sid in enumerate(truth.sat_ids):
        az, el = az_el(pos[0, j], REFERENCE_ECEF)
        items.append((sid, az, el, BLUE if sid.startswith("G") else ORANGE))
    rng = np.random.default_rng(3)
    num = 120
    results = {}
    for label, gps, bds in (("GPS only (6)", 6, 0), ("BDS only (5)", 0, 5), ("GPS + BDS (11)", 6, 5)):
        if gps == 0:
            ephs_l, obs_l = build_two_system_truth(num_epochs=num, gps_count=4, bds_count=bds)
            cols = [j for j, s in enumerate(obs_l.sat_ids) if s.startswith("C")]
            shift = -time_systems.GPST_MINUS_BDT_S
            obs_l = obs.Observables(epoch_uptime_ms=obs_l.epoch_uptime_ms, receive_time_s=obs_l.receive_time_s + shift,
                                    sat_ids=[obs_l.sat_ids[j] for j in cols], transmit_time_s=obs_l.transmit_time_s[:, cols] + shift,
                                    pseudorange_m=obs_l.pseudorange_m[:, cols], time_system="BDT")
            sig = "BDS_B2A"
        elif bds == 0:
            ephs_l, obs_l = build_two_system_truth(num_epochs=num, gps_count=gps, bds_count=5)
            cols = [j for j, s in enumerate(obs_l.sat_ids) if s.startswith("G")]
            obs_l = dataclasses.replace(obs_l, sat_ids=[obs_l.sat_ids[j] for j in cols], transmit_time_s=obs_l.transmit_time_s[:, cols],
                                        pseudorange_m=obs_l.pseudorange_m[:, cols], signal_type_ids={})
            sig = "GPS_L5"
        else:
            ephs_l, obs_l = build_two_system_truth(num_epochs=num, gps_count=gps, bds_count=bds)
            sig = "GPS_L5"
        noisy = dataclasses.replace(obs_l, pseudorange_m=obs_l.pseudorange_m + rng.normal(0, 3.0, obs_l.pseudorange_m.shape))
        ser = nav.solve_series(noisy, ephs_l, signal_type_id=sig, settings=SETTINGS, initial_position_ecef_m=REFERENCE_ECEF)
        results[label] = ser
    fig = plt.figure(figsize=(7.4, 3.2))
    ax = fig.add_subplot(1, 3, 1, projection="polar")
    skyplot(ax, items, "Synthetic sky\n(blue GPS, orange BeiDou)")
    ax2 = fig.add_subplot(1, 3, (2, 3))
    for (label, ser), color in zip(results.items(), (BLUE, ORANGE, GREEN)):
        enu = ser.enu_about(REFERENCE_ECEF)
        ax2.scatter(enu[:, 0], enu[:, 1], s=6, alpha=0.6, color=color,
                    label=f"{label}: HDOP {np.nanmedian(ser.dop['hdop']):.1f}, 2D rms {np.sqrt(np.nanmean(enu[:, 0]**2 + enu[:, 1]**2)):.1f} m")
    ax2.set_aspect("equal", "datalim"); ax2.set_xlabel("east [m]"); ax2.set_ylabel("north [m]")
    ax2.set_title("Fixes with 3 m pseudorange noise (truth at the origin)", fontsize=8); ax2.legend(fontsize=6.5)
    fig.tight_layout()
    save(fig, "f12_positioning_synthetic")

    # the inter-system bias: recovered for any value, never leaks into the position
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.7))
    biases = [-250.0, 0.0, 87.5, 1000.0, 3000.0]
    est, perr = [], []
    for b in biases:
        e, o = build_two_system_truth(num_epochs=3, isb_m=b)
        ser = nav.solve_series(o, e, signal_type_id="GPS_L5", settings=SETTINGS, initial_position_ecef_m=REFERENCE_ECEF)
        est.append(float(np.nanmean(ser.inter_system_bias_m["C"])))
        perr.append(float(np.max(np.linalg.norm(ser.position_ecef_m - REFERENCE_ECEF, axis=1))))
    axes[0].plot(biases, est, "o-", color=BLUE, ms=4); axes[0].plot(biases, biases, ":", color=GREY)
    axes[0].set_xlabel("true inter-system bias [m]"); axes[0].set_ylabel("estimated [m]"); axes[0].set_title("Bias estimate", fontsize=8)
    axes[1].semilogy(biases, np.maximum(perr, 1e-4), "o-", color=ORANGE, ms=4)
    axes[1].set_xlabel("true inter-system bias [m]"); axes[1].set_ylabel("position error [m] (log)"); axes[1].set_title("Position error stays below 5 cm", fontsize=8)
    fig.tight_layout()
    save(fig, "f13_isb")

    # the time scale
    e, o = build_two_system_truth(num_epochs=3)
    good = nav.solve_series(o, e, signal_type_id="GPS_L5", settings=SETTINGS, initial_position_ecef_m=REFERENCE_ECEF)
    wrong_eph = {}
    for sid, eph in e.items():
        wrong_eph[sid] = dataclasses.replace(eph, toe=eph.toe + 14.0, toc=eph.toc + 14.0) if isinstance(eph, BdsEphemeris) else eph
    bad = nav.solve_series(o, wrong_eph, signal_type_id="GPS_L5", settings=SETTINGS, initial_position_ecef_m=REFERENCE_ECEF)
    fig, ax = plt.subplots(figsize=(4.6, 2.7))
    names = o.sat_ids
    ax.bar(np.arange(len(names)) - 0.2, np.nanmax(np.abs(good.residuals_m), axis=0), 0.4, color=GREEN, label="BDT used correctly")
    ax.bar(np.arange(len(names)) + 0.2, np.nanmax(np.abs(bad.residuals_m), axis=0), 0.4, color=RED, label="BeiDou orbit and clock 14 s off")
    ax.set_xticks(np.arange(len(names))); ax.set_xticklabels(names, rotation=90, fontsize=6.5)
    ax.set_yscale("log"); ax.set_ylim(1e-3, 1e6); ax.set_ylabel("post-fit residual [m]"); ax.legend(fontsize=6.5, loc="upper left")
    ax.set_title("A 14 s time-scale error is visible in the residuals", fontsize=8)
    save(fig, "f14_time_scale")


def real(tracking_file: Path):
    from utils import tracking_io
    from utils.nav import bcnav2, symbols
    run = tracking_io.load_tracking_results(tracking_file)
    ephs, anchors, outs, streams = {}, {}, {}, {}
    for sid, res in sorted(run.results.items()):
        o = res.outputs
        st = symbols.extract(o, "BDS_B2A")
        dec = bcnav2.decode(st.soft, expected_prn=int(sid[1:]))
        eph = bcnav2.assemble_ephemeris(dec)
        if eph is None:
            continue
        ephs[sid] = eph; anchors[sid] = obs.anchor_from_bcnav2(st, dec.frames[0], week=eph.week); outs[sid] = o; streams[sid] = (st, dec)
    ref = nav.geodetic_to_ecef(40.009956, -105.243613, 1655.0)

    # 1. tracking summary: Doppler and C/N0 of each satellite
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 2.9))
    for sid, o in outs.items():
        c = o.output_index
        axes[0].plot(np.asarray(o.uptime_epoch_ms[:c]) * 1e-3, np.asarray(o.doppler_freq_hz[:c]) * 1e-3, lw=0.8, label=sid)
        k = o.cn0_index
        if k:
            axes[1].plot(np.asarray(o.cn0_uptime_ms[:k]) * 1e-3, np.asarray(o.cn0_dbhz[:k, 1]), lw=0.6)
    axes[0].set_xlabel("time [s]"); axes[0].set_ylabel("Doppler [kHz]"); axes[0].set_title("Doppler of the nine tracked satellites", fontsize=8)
    axes[0].legend(fontsize=5.5, ncol=3)
    axes[1].set_xlabel("time [s]"); axes[1].set_ylabel("C/N0 [dB-Hz]"); axes[1].set_title("Pilot C/N0", fontsize=8)
    fig.tight_layout(); save(fig, "f15_real_tracking")

    # 2. decoded frames per satellite
    fig, ax = plt.subplots(figsize=(4.8, 2.6))
    ids = list(streams)
    counts = [len(streams[s][1].frames) for s in ids]
    ax.bar(ids, counts, color=BLUE); ax.set_ylabel("CRC-valid B-CNAV2 frames"); ax.tick_params(axis="x", labelrotation=90, labelsize=7)
    ax.set_title("Frames decoded from 120 s of tracking", fontsize=8)
    save(fig, "f16_real_frames")

    # 3. the fix, with the integer-millisecond correction (see the text)
    ob = obs.form_observables(outs, anchors, epoch_interval_ms=EPOCH_MS, max_gap_factor=5.0, guard_after_gap_ms=500.0)
    C = 299792458.0
    pos = nav.satellite_positions(ob, ephs, apply_sagnac=True, receiver_position_ecef_m=ref)
    off = {}
    for j, s in enumerate(ob.sat_ids):
        geo = np.linalg.norm(pos[0, j] - ref) / C
        meas = ob.receive_time_s[0] - ob.transmit_time_s[0, j]
        off[s] = (meas - geo + ephs[s].clock_correction_s(ob.transmit_time_s[0, j])) * 1e3
    base = min(off.values()); k = {s: int(round(off[s] - base)) for s in off}
    fixed = {s: dataclasses.replace(anchors[s], tow_s=anchors[s].tow_s + k[s] * 1e-3) for s in anchors}
    ob2 = obs.form_observables(outs, fixed, epoch_interval_ms=EPOCH_MS, max_gap_factor=5.0, guard_after_gap_ms=500.0)
    ser = nav.solve_series(ob2, ephs, signal_type_id="BDS_B2A", settings=nav.CorrectionSettings(troposphere=True, ionosphere=False), initial_position_ecef_m=ref, residual_gate_m=RAIM_GATE_M)
    enu = ser.enu_about(ref); good = ser.valid
    mean = np.nanmean(ser.position_ecef_m, axis=0)
    enu_mean = nav.ecf2enu(ref, mean[None])[0]
    fig = plt.figure(figsize=(7.4, 3.3))
    ax = fig.add_subplot(1, 3, 1, projection="polar")
    items = []
    for j, s in enumerate(ob2.sat_ids):
        az, el = az_el(pos[0, j], ref)
        items.append((s, az, el, ORANGE))
    skyplot(ax, items, "Sky at the start\n(from the decoded orbits)")
    ax2 = fig.add_subplot(1, 3, 2)
    ax2.scatter(enu[good, 0] - enu_mean[0], enu[good, 1] - enu_mean[1], s=4, color=BLUE)
    ax2.set_aspect("equal", "datalim"); ax2.set_xlabel("east about the mean fix [m]"); ax2.set_ylabel("north [m]")
    ax2.set_title(f"{good.sum()} fixes; HDOP {np.nanmedian(ser.dop['hdop']):.2f}", fontsize=8)
    ax3 = fig.add_subplot(1, 3, 3)
    t = (ser.epoch_uptime_ms - ser.epoch_uptime_ms[0]) * 1e-3
    rms = np.sqrt(np.nanmean(ser.residuals_m ** 2, axis=1))
    ax3.plot(t, rms, color=GREEN, lw=0.8); ax3.set_xlabel("time [s]"); ax3.set_ylabel("post-fit residual rms [m]")
    ax3.set_title("Residual rms per epoch", fontsize=8)
    fig.tight_layout(); save(fig, "f17_real_fix")
    lat, lon, h = nav.ecf2geodetic(mean) if hasattr(nav, "ecf2geodetic") else nav.ecef_to_geodetic(mean)
    summary = dict(lat=float(lat), lon=float(lon), h=float(h), epochs=int(good.sum()), hdop=float(np.nanmedian(ser.dop["hdop"])),
                   rms=float(np.sqrt(np.nanmean(ser.residuals_m ** 2))), sats=[s for s in ob2.sat_ids],
                   offset_m=float(np.linalg.norm(mean - ref)), drift_ppm=float(ser.clock_drift_ppm()[0]),
                   frames={s: len(streams[s][1].frames) for s in streams}, int_ms=k)
    import json
    (OUT / "real_summary.json").write_text(json.dumps(summary, indent=1))
    print(summary)


if __name__ == "__main__":
    synthetic()
    if len(sys.argv) > 1:
        real(Path(sys.argv[1]))
