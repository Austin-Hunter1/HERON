"""
Figures for the BeiDou report that need only synthetic data and the code in `utils/`.

Run from `gnss_processing/`:   python docs/bds_report/make_theory_figures.py
The figures are written to `docs/bds_report/figures/`.  `build_report.py` puts them
in the PDF.  Real-data figures are made by `make_real_figures.py`.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")
OUT = Path(__file__).resolve().parent / "figures"
OUT.mkdir(exist_ok=True)

from tests import synthetic  # noqa: E402
from utils import bds_b1c, bds_b2a, bpsk_acquisition, secondary_code  # noqa: E402
from utils.nav import bcnav1, bcnav2, ldpc  # noqa: E402
from utils.signal_interfaces import BdsB1C, BdsB2a, build_acquisition_code_params, build_signals  # noqa: E402

plt.rcParams.update({"font.size": 9, "axes.grid": True, "grid.alpha": 0.3, "figure.dpi": 150,
                     "axes.spines.top": False, "axes.spines.right": False})
BLUE, ORANGE, GREEN, RED, GREY = "#1f5fa8", "#d9822b", "#2e8b57", "#c0392b", "#777777"


def box(ax, x, y, w, h, text, color="#e8eef7", edge=BLUE, fs=8):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02", fc=color, ec=edge, lw=1.2))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs)


def arrow(ax, x0, y0, x1, y1):
    ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle="-|>", mutation_scale=10, color=GREY, lw=1.2))


def save(fig, name):
    fig.savefig(OUT / f"{name}.png", bbox_inches="tight")
    plt.close(fig)
    print("wrote", name)


# ---- 1. pipeline ---------------------------------------------------------------
def pipeline():
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    ax.set_xlim(0, 10); ax.set_ylim(0, 4.4); ax.axis("off"); ax.grid(False)
    top = [("Raw IQ file\n+ metadata.yml", 0.1), ("Acquisition\n(FFT, per PRN)", 2.1), ("Tracking\n(DLL, PLL, FLL)", 4.1),
           ("Tracking file\n(.h5)", 6.1), ("Symbols\n(soft bits)", 8.1)]
    bottom = [("Frames:\nB-CNAV2 / B-CNAV1", 8.1), ("Time anchor +\nephemeris", 6.1), ("Pseudoranges\n(one epoch grid)", 4.1),
              ("Corrections,\nweighted LSQ", 2.1), ("Position, clock,\ninter-system bias", 0.1)]
    for t, x in top:
        box(ax, x, 3.0, 1.8, 1.0, t)
    for t, x in bottom:
        box(ax, x, 0.3, 1.8, 1.0, t, color="#fdf0e0", edge=ORANGE)
    for x in (1.9, 3.9, 5.9, 7.9):
        arrow(ax, x, 3.5, x + 0.2, 3.5)
    arrow(ax, 9.0, 3.0, 9.0, 1.3)
    for x in (8.1, 6.1, 4.1, 2.1):
        arrow(ax, x, 0.8, x - 0.2, 0.8)
    ax.text(4.9, 4.25, "Notebook 01", ha="center", color=BLUE, weight="bold")
    ax.text(4.9, 1.55, "Notebook 02", ha="center", color=ORANGE, weight="bold")
    save(fig, "f01_pipeline")


# ---- 2. signal structure ---------------------------------------------------------
def signal_structure():
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.2))
    specs = [
        ("BeiDou B2a  (1176.45 MHz)", [("B2a data", "I", "BPSK(10), power 1/2", BLUE), ("B2a pilot", "Q", "BPSK(10), power 1/2", ORANGE)],
         "primary 10230 chips = 1 ms\ndata overlay 00010 (5 ms symbol)\npilot overlay 100 chips (100 ms)"),
        ("BeiDou B1C  (1575.42 MHz)", [("B1C data", "I", "BOC(1,1), power 1/4", BLUE), ("B1C pilot", "Q", "BOC(1,1), power 29/44", ORANGE)],
         "primary 10230 chips = 10 ms\nno data overlay (10 ms symbol)\npilot overlay 1800 chips (18 s)\nBOC(6,1) part (1/11) not tracked"),
    ]
    for ax, (title, comps, note) in zip(axes, specs):
        ax.set_xlim(-0.4, 2.2); ax.set_ylim(-0.9, 2.2); ax.set_title(title, fontsize=9)
        ax.axhline(0, color="k", lw=0.6); ax.axvline(0, color="k", lw=0.6)
        for name, branch, label, color in comps:
            if branch == "I":
                ax.annotate("", xy=(1.0, 0), xytext=(0, 0), arrowprops=dict(arrowstyle="-|>", color=color, lw=2))
                ax.text(1.05, -0.12, f"{name}\n{label}", ha="left", va="top", fontsize=7.5, color=color)
            else:
                ax.annotate("", xy=(0, 1.0), xytext=(0, 0), arrowprops=dict(arrowstyle="-|>", color=color, lw=2))
                ax.text(0.06, 1.0, f"{name}\n{label}", ha="left", va="center", fontsize=7.5, color=color)
        ax.text(0.9, 2.15, note, va="top", fontsize=7)
        ax.set_xticks([]); ax.set_yticks([]); ax.set_xlabel("I (0 deg)"); ax.set_ylabel("Q (90 deg)")
        ax.grid(False)
    fig.tight_layout()
    save(fig, "f02_signal_structure")


# ---- 3. codes and correlation -------------------------------------------------------
def code_correlation():
    fig, axes = plt.subplots(1, 3, figsize=(7.4, 2.5))
    code = bds_b2a.generate_pilot_code(1).astype(float)
    f = np.fft.fft(code)
    auto = np.fft.ifft(f * np.conj(f)).real
    lags = np.arange(len(code)); lags = np.where(lags > len(code) // 2, lags - len(code), lags)
    order = np.argsort(lags)
    axes[0].plot(lags[order], auto[order], color=BLUE, lw=0.5)
    axes[0].set_title("B2a pilot, PRN 1\nperiodic autocorrelation", fontsize=8); axes[0].set_xlabel("lag [chips]")
    axes[0].set_ylabel("correlation (10230 = peak)")
    # BPSK vs BOC(1,1) ACF, from a fine chip grid
    n = 16
    c = bds_b1c.generate_data_code(1).astype(float)
    bpsk = np.repeat(c, n)
    sub = np.tile(np.r_[np.ones(n // 2), -np.ones(n // 2)], len(c))
    boc = bpsk * sub
    def acf(x):
        fx = np.fft.fft(x); r = np.fft.ifft(fx * np.conj(fx)).real; return r / r[0]
    rb, rc = acf(bpsk), acf(boc)
    k = np.arange(-2 * n, 2 * n + 1)
    axes[1].plot(k / n, rb[k], color=GREY, label="BPSK (L1 C/A-like)")
    axes[1].plot(k / n, rc[k], color=ORANGE, label="BOC(1,1) (B1C)")
    axes[1].set_title("Autocorrelation near the peak: BOC(1,1)\nhas a narrower peak and negative side lobes", fontsize=8); axes[1].set_xlabel("lag [chips]")
    axes[1].legend(fontsize=6.5, loc="upper right")
    # cross-correlation between PRNs
    c2 = bds_b2a.generate_pilot_code(2).astype(float)
    cross = np.fft.ifft(np.fft.fft(code) * np.conj(np.fft.fft(c2))).real
    axes[2].plot(cross, color=GREEN, lw=0.4)
    axes[2].set_title("B2a pilot PRN 1 vs PRN 2\ncross-correlation", fontsize=8); axes[2].set_xlabel("lag [chips]")
    axes[2].set_ylim(-10230, 10230)
    fig.tight_layout()
    save(fig, "f03_codes")


# ---- 4. acquisition surface -----------------------------------------------------------
def acquisition():
    """
    The acquisition search drawn as a surface.  `bpsk_acquisition` keeps only the bins
    around the peak, so this figure repeats the search in plain NumPy (carrier wipe-off,
    FFT correlation with one primary period, ten 1 ms blocks summed without phase).  The
    detection decision itself is taken from the real `run_acquisition`.
    """
    fs = 25_000_000
    defs = build_signals(BdsB2a, prns=[1])
    cfg = bpsk_acquisition.AcquisitionConfiguration(coherent_duration_replica_ms=1, num_blocks=10, sample_rate=fs,
                                                    min_search_doppler_hz=-5000.0, max_search_doppler_hz=5000.0, fine_search=None)
    rng = np.random.default_rng(2)
    s = synthetic.generate_b2a_samples(prn=1, start_sec=0, duration_sec=cfg.acq_total_duration_ms * 1e-3, samp_rate=fs,
                                       doppler_hz=-2750.0, code_phase_ms=0.37, noise_sigma=8.0, rng=rng)
    res = bpsk_acquisition.run_acquisition(sample_block=s, sample_block_uptime_epoch_ms=0.0, acq_config=cfg,
                                           code_parameters=build_acquisition_code_params(BdsB2a, defs),
                                           prob_false_alarm_total=1e-6, noise_var_method="abscorrvar")["C01"]
    n = fs // 1000
    code = bds_b2a.generate_pilot_code(1).astype(float)
    replica = code[(np.arange(n) * 10.23e6 / fs).astype(int) % len(code)]
    rep_f = np.conj(np.fft.fft(replica))
    dops = np.arange(-5000, 5001, 250)
    t = np.arange(n) / fs
    surf = np.zeros((len(dops), n))
    for i, d in enumerate(dops):
        for blk in range(10):
            x = s[blk * n:(blk + 1) * n] * np.exp(-2j * np.pi * d * (t + blk * 1e-3))
            surf[i] += np.abs(np.fft.ifft(np.fft.fft(x) * rep_f)) ** 2
    surf = surf[:, (-np.arange(n)) % n]  # the FFT gives the lag of the replica: reverse it to a delay
    ms = np.arange(n) / fs * 1e3
    db = 10 * np.log10(surf / surf.max())
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 2.9))
    im = axes[0].pcolormesh(ms, dops, db, cmap="viridis", vmin=-25, vmax=0, shading="auto", rasterized=True)
    axes[0].set_xlabel("code phase [ms]"); axes[0].set_ylabel("Doppler [Hz]")
    axes[0].set_title("Acquisition surface [dB below the peak]\nB2a PRN 1; truth 0.37 ms and -2750 Hz", fontsize=8)
    fig.colorbar(im, ax=axes[0], fraction=0.05)
    i = np.unravel_index(np.argmax(surf), surf.shape)
    axes[1].plot(ms, db[i[0]], color=BLUE, lw=0.6)
    axes[1].set_xlabel("code phase [ms]"); axes[1].set_ylabel("dB below peak")
    axes[0].plot([ms[i[1]]], [dops[i[0]]], "o", mfc="none", mec="white", ms=9)
    axes[1].set_title(f"Cut at {dops[i[0]]} Hz.  run_acquisition: detected = {res.signal_detected},\n"
                      f"code phase {res.acq_code_phase_seconds * 1e3:.3f} ms", fontsize=8)
    fig.tight_layout()
    save(fig, "f04_acquisition")
    return res


# ---- 5. tracking on a synthetic B2a signal ----------------------------------------------
def tracking():
    from tests.test_b2a import _acquire_and_track, SEED_DOPPLER_HZ
    ad = _acquire_and_track(noise_sigma=3.0, buffers=30)
    o = ad.outputs; c = o.output_index
    t = np.asarray(o.uptime_epoch_ms[:c]) * 1e-3
    dur = np.asarray(o.epoch_duration_ms[:c]); pll = np.asarray(o.pll_mode[:c]); syn = np.asarray(o.overlay_synced[:c])
    fig, axes = plt.subplots(2, 2, figsize=(7.4, 4.6))
    ax = axes[0, 0]
    ax.plot(t, np.asarray(o.doppler_freq_hz[:c]), color=BLUE, lw=0.8); ax.axhline(SEED_DOPPLER_HZ, color=RED, ls="--", lw=0.8, label="true 1500 Hz")
    ax.set_title("Doppler estimate", fontsize=8); ax.set_xlabel("time [s]"); ax.set_ylabel("Hz"); ax.legend(fontsize=7)
    ax = axes[0, 1]
    ax.plot(t, dur, color=GREEN, lw=1); ax.set_title("Coherent epoch length", fontsize=8); ax.set_xlabel("time [s]"); ax.set_ylabel("ms")
    for arr, lab, col in ((pll, "PLL locked", ORANGE), (syn, "overlay synced", RED)):
        idx = np.nonzero(arr)[0]
        if len(idx):
            ax.axvline(t[idx[0]], color=col, ls=":", label=lab)
    ax.legend(fontsize=7)
    pr = np.asarray(o.prompt_corr[:c])
    ax = axes[1, 0]
    ax.plot(t, pr[:, 1].real / 1e3, lw=0.5, color=BLUE, label="pilot I")
    ax.plot(t, pr[:, 1].imag / 1e3, lw=0.5, color=GREY, label="pilot Q")
    ax.set_title("Pilot prompt correlator", fontsize=8); ax.set_xlabel("time [s]"); ax.legend(fontsize=7)
    ax = axes[1, 1]
    sel = slice(max(0, c - 300), c)
    ax.scatter(pr[sel, 0].real / 1e3, pr[sel, 0].imag / 1e3, s=3, color=ORANGE, label="data prompt")
    ax.scatter(pr[sel, 1].real / 1e3, pr[sel, 1].imag / 1e3, s=3, color=BLUE, label="pilot prompt")
    ax.set_title("Last 300 prompts: data and pilot\nare 90 degrees apart", fontsize=8); ax.legend(fontsize=7); ax.set_aspect("equal", "datalim")
    fig.tight_layout()
    save(fig, "f05_tracking")


# ---- 6. overlay (secondary code) search --------------------------------------------------
def overlay_search():
    ov = bds_b1c.generate_pilot_secondary_code(1).astype(float)
    rng = np.random.default_rng(1)
    offset = 777
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 2.6))
    for ax, sigma, label in ((axes[0], 1.0, "prompt SNR about 0 dB"), (axes[1], 3.0, "prompt SNR about -10 dB")):
        prompts = ov[(np.arange(200) + offset) % 1800] + rng.normal(0, sigma, 200)
        # circular correlation of 200 prompts with every cyclic offset of the 1800-chip code
        f = np.fft.fft(prompts, 1800)
        score = np.fft.ifft(np.conj(f) * np.fft.fft(ov)).real
        score = np.roll(score[::-1], 1) if False else np.array([np.dot(prompts, ov[(np.arange(200) + k) % 1800]) for k in range(1800)])
        ax.plot(score, lw=0.6, color=BLUE); ax.axvline(offset, color=RED, ls=":", lw=0.8)
        found, conf = secondary_code.fft_search(prompts.astype(complex), ov)
        ax.set_title(f"{label}\nfft_search finds {found} (truth {offset}), confidence {conf:.1f}", fontsize=8)
        ax.set_xlabel("overlay offset [chips]")
    axes[0].set_ylabel("correlation with 200 prompts")
    fig.tight_layout()
    save(fig, "f06_overlay")


# ---- 7. symbol grid ----------------------------------------------------------------------------
def symbol_grid():
    fig, axes = plt.subplots(2, 1, figsize=(7.2, 2.8), sharex=True)
    shift = 3.0  # true symbol edges are 3 ms off the multiples of 5 ms
    edges = [e for e in np.arange(-5 + shift, 21, 5.0)]
    for ax, starts, color, title in (
        (axes[0], np.arange(0, 20, 5.0), ORANGE, "Epochs anchored on multiples of 5 ms of code phase (the old behaviour)"),
        (axes[1], np.array([e for e in edges if e >= 0 and e < 20]), GREEN, "Epochs tied to the overlay counter (D-059)"),
    ):
        ax.set_xlim(0, 20); ax.set_ylim(0, 1); ax.set_yticks([]); ax.grid(False); ax.set_title(title, fontsize=8)
        for st in starts:
            ax.add_patch(Rectangle((st + 0.08, 0.2), min(4.84, 20 - st - 0.08), 0.5, fc=color, ec="k", alpha=0.7))
        for e in edges:
            if 0 <= e <= 20:
                ax.axvline(e, color=RED, lw=1.8)
    axes[0].text(0.1, 0.78, "every epoch holds the end of one symbol and the start of the next: the data component loses amplitude", fontsize=7)
    axes[1].text(0.1, 0.78, "every epoch is exactly one symbol; the first 3 ms are not used", fontsize=7)
    axes[1].set_xlabel("code phase [ms].  Red lines: true symbol edges (3 ms off the 5 ms lattice).  Boxes: 5 ms epochs.")
    fig.tight_layout()
    save(fig, "f07_symbol_grid")


# ---- 8. frames and interleaver ----------------------------------------------------------------
def frames():
    fig, axes = plt.subplots(2, 1, figsize=(7.2, 2.7))
    ax = axes[0]; ax.set_xlim(0, 600); ax.set_ylim(0, 1); ax.axis("off"); ax.grid(False)
    ax.add_patch(Rectangle((0, 0.2), 24, 0.6, fc=ORANGE, ec="k")); ax.add_patch(Rectangle((24, 0.2), 576, 0.6, fc="#cfe0f4", ec="k"))
    ax.text(12, 0.5, "pre.", ha="center", fontsize=7); ax.text(312, 0.5, "LDPC(96,48) codeword: 288 message bits -> 576 symbols\n(PRN 6, type 6, SOW 18, data 234, CRC 24)", ha="center", fontsize=7.5)
    ax.set_title("B-CNAV2 frame: 600 symbols = 3 s", fontsize=8)
    ax = axes[1]; ax.set_xlim(0, 1800); ax.set_ylim(0, 1); ax.axis("off"); ax.grid(False)
    ax.add_patch(Rectangle((0, 0.2), 72, 0.6, fc=ORANGE, ec="k")); ax.add_patch(Rectangle((72, 0.2), 1728, 0.6, fc="#cfe0f4", ec="k"))
    ax.text(36, 0.9, "SF1", ha="center", fontsize=7)
    ax.text(936, 0.5, "interleaved: subframe 2 = LDPC(200,100) -> 1200 symbols (600 bits: WN, HOW, IODC, IODE, ephemeris, clock, delays, CRC)\n"
            "and subframe 3 = LDPC(88,44) -> 528 symbols (264 bits: page id, health, BDGIM ..., CRC)", ha="center", fontsize=7)
    ax.text(36, 0.05, "BCH PRN 21 + SOH 51", ha="left", fontsize=6.5)
    ax.set_title("B-CNAV1 frame: 1800 symbols = 18 s (no preamble)", fontsize=8)
    fig.tight_layout()
    save(fig, "f08_frames")


def interleaver():
    arr = np.zeros((36, 48))
    arr[list(bcnav1._SUBFRAME3_ROWS)] = 1
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 2.8))
    axes[0].imshow(arr, cmap="coolwarm", aspect="auto", vmin=-0.5, vmax=1.5)
    axes[0].set_title("36 x 48 interleaver: red rows = subframe 3,\nblue rows = subframe 2 (written by rows)", fontsize=8)
    axes[0].set_xlabel("column"); axes[0].set_ylabel("row"); axes[0].grid(False)
    # a burst of 36 consecutive channel symbols lands in different codeword positions
    tx = bcnav1.interleave(np.arange(1200), 10_000 + np.arange(528))
    burst = tx[500:536]
    axes[1].plot(np.arange(len(burst)), burst, "o-", ms=3, color=BLUE)
    axes[1].set_title("One burst of 36 channel symbols:\nsymbol index inside its codeword", fontsize=8)
    axes[1].set_xlabel("position in the burst"); axes[1].set_ylabel("symbol index (sub3 + 10000)")
    fig.tight_layout()
    save(fig, "f09_interleaver")


# ---- 9. LDPC performance ----------------------------------------------------------------------
def ldpc_performance():
    sigmas = [0.6, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3]
    codes = [(ldpc.BCNAV2_CODE, "B-CNAV2 LDPC(96,48)", 30), (ldpc.BCNAV1_SUBFRAME2_CODE, "B-CNAV1 LDPC(200,100)", 12),
             (ldpc.BCNAV1_SUBFRAME3_CODE, "B-CNAV1 LDPC(88,44)", 30)]
    rng = np.random.default_rng(5)
    fig, ax = plt.subplots(figsize=(4.6, 3.0))
    for code, label, trials in codes:
        fer = []
        for sg in sigmas:
            bad = 0
            for _ in range(trials):
                msg = rng.integers(0, 64, code.num_message_symbols)
                cw = code.encode(msg)
                soft = 1.0 - 2.0 * ldpc.bits_from_symbols(cw) + rng.normal(0, sg, 6 * code.num_variables)
                res = code.decode(soft)
                bad += not (res.converged and np.array_equal(res.symbols, cw))
            fer.append(bad / trials)
        ax.plot(sigmas, fer, "o-", ms=3, label=label)
    ax.set_xlabel("noise sigma on +/-1 soft bits (BPSK)"); ax.set_ylabel("frame error rate")
    ax.set_title("Soft decoder, simulated", fontsize=8); ax.legend(fontsize=7)
    save(fig, "f10_ldpc")


# ---- 10. time ---------------------------------------------------------------------------------
def time_diagram():
    fig, ax = plt.subplots(figsize=(7.2, 2.6)); ax.axis("off"); ax.grid(False)
    ax.set_xlim(0, 10); ax.set_ylim(0, 3)
    ax.annotate("", xy=(9.8, 2.2), xytext=(0.2, 2.2), arrowprops=dict(arrowstyle="-|>"))
    ax.annotate("", xy=(9.8, 0.8), xytext=(0.2, 0.8), arrowprops=dict(arrowstyle="-|>"))
    ax.text(0.2, 2.45, "satellite time (BDT for BeiDou, GPST for GPS)", fontsize=8)
    ax.text(0.2, 1.05, "receiver clock (sample count + clock bias)", fontsize=8)
    ax.plot([2.0, 2.0], [0.8, 2.2], color=BLUE, lw=1.5)
    ax.plot([8.0, 8.0], [0.8, 2.2], color=ORANGE, lw=1.5)
    ax.text(2.0, 2.6 + 0.0, "", fontsize=7)
    ax.text(2.05, 1.5, "anchor:\ndecoded time of week\nt_anchor\n(first symbol of a frame)", fontsize=7, color=BLUE)
    ax.text(8.05, 1.5, "later epoch:\ncode phase adds\nelapsed satellite time", fontsize=7, color=ORANGE)
    ax.text(5.0, 0.2, "pseudorange  rho = c * (t_receive - t_transmit)   with  t_transmit = t_anchor + (code phase - code phase at anchor)",
            ha="center", fontsize=7.5)
    ax.text(5.0, 2.75, "BDT = GPST - 14 s: BeiDou transmit times are moved by 14 s before they are mixed with GPS", ha="center", fontsize=7.5, color=RED)
    save(fig, "f11_time")


if __name__ == "__main__":
    pipeline(); signal_structure(); code_correlation(); acquisition()
    tracking(); overlay_search(); symbol_grid(); frames(); interleaver(); ldpc_performance(); time_diagram()
