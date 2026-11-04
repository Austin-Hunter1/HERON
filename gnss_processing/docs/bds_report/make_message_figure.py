"""
One-page picture of the B-CNAV2 (B2a) and B-CNAV1 (B1C) navigation messages.

    python docs/bds_report/make_message_figure.py

Writes `figures/messages.png` and `figures/messages.pdf`.  Field widths are read from the
decoder modules (`utils.nav.bcnav2`, `utils.nav.bcnav1`), so the picture cannot drift from
the code.  A bar is as wide as its number of bits (or symbols).
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")
OUT = Path(__file__).resolve().parent / "figures"
OUT.mkdir(exist_ok=True)

from utils.nav import bcnav1, bcnav2  # noqa: E402

NAVY, BLUE, ORANGE, GREEN, PURPLE, GREY, RED = "#1f3b66", "#4a7fc1", "#e08a2e", "#3f9a68", "#8a5db3", "#9aa5b8", "#c0392b"
TIME_C, ORBIT_C, CLOCK_C, ID_C, CODE_C, CRC_C, MISC_C = BLUE, ORANGE, GREEN, NAVY, PURPLE, RED, GREY


def bar(ax, y, parts, label, h=0.62, x0=0.0, width=100.0, fs=6.4, sub=None):
    """
    Draw segments (name, size, colour), width proportional to size.  A name that does not fit
    inside its segment goes below the bar, on a leader line, on two alternating levels.
    """
    total = sum(p[1] for p in parts)
    ax.text(x0 - 0.8, y + h / 2, label, ha="right", va="center", fontsize=7.2, color=NAVY, weight="bold", linespacing=1.1)
    x, k = x0, 0
    for name, size, color in parts:
        w = width * size / total
        ax.add_patch(Rectangle((x, y), w, h, fc=color, ec="white", lw=0.8))
        need = (len(name) + len(str(size)) + 4) * 0.62 * fs / 6.4
        if w >= need:
            ax.text(x + w / 2, y + h / 2, f"{name}  ({size})", ha="center", va="center", fontsize=fs, color="white", weight="bold")
        else:
            level = 0.33 + 0.36 * (k % 3)
            ax.plot([x + w / 2, x + w / 2], [y, y - level + 0.08], color=color, lw=0.6)
            ax.text(x + w / 2, y - level, f"{name} ({size})", ha="center", va="top", fontsize=5.8, color=color, weight="bold")
            k += 1
        x += w
    if sub:
        ax.text(x0 + width, y + h + 0.12, sub, ha="right", va="bottom", fontsize=6.3, color="#555")


def lay(layout, *names):
    """Total bits of the named fields of a decoder layout list."""
    return sum(f[1] for f in layout if f[0] in names)


def note(ax, x, y, text, fs=6.9):
    ax.text(x, y, text, ha="left", va="center", fontsize=fs, color=NAVY)


def main():
    fig, ax = plt.subplots(figsize=(12.5, 10.6))
    ax.set_xlim(-12, 101); ax.set_ylim(1.4, 35.6); ax.axis("off")
    ax.text(44, 35.2, "BeiDou navigation messages: B-CNAV2 (B2a) and B-CNAV1 (B1C)", ha="center", fontsize=14, weight="bold", color=NAVY)
    ax.text(44, 34.4, "Bar width is proportional to the number of bits or symbols. Field sizes come from the decoder code.", ha="center", fontsize=8, color="#555")

    # ---------- A: the same 18 s ----------
    y = 31.0
    ax.text(0, y + 1.75, "A.  The same 18 seconds on the two signals", fontsize=10, weight="bold", color=NAVY)
    x = 0.0
    for i in range(6):
        ax.add_patch(Rectangle((x, y + 0.75), 100 / 6, 0.6, fc=BLUE if i % 2 == 0 else "#6f9bd6", ec="white", lw=1))
        ax.text(x + 100 / 12, y + 1.05, "3 s frame", ha="center", va="center", fontsize=6.8, color="white")
        x += 100 / 6
    ax.text(-0.8, y + 1.05, "B2a", ha="right", va="center", fontsize=7.5, weight="bold", color=NAVY)
    ax.add_patch(Rectangle((0, y), 100, 0.6, fc=ORANGE, ec="white", lw=1))
    ax.text(50, y + 0.3, "one 18 s frame (subframes 1, 2 and 3)", ha="center", va="center", fontsize=7.2, color="white", weight="bold")
    ax.text(-0.8, y + 0.3, "B1C", ha="right", va="center", fontsize=7.5, weight="bold", color=NAVY)
    ax.text(0, y - 0.45, "0 s", fontsize=6.5, color="#555"); ax.text(100, y - 0.45, "18 s", fontsize=6.5, color="#555", ha="right")
    note(ax, 0, y - 1.0, "B2a: six frames of 3 s. The message types rotate. A full ephemeris needs type 10 + type 11 + type 30 (30 s of tracking gave all three on every satellite of the real recording).")
    note(ax, 0, y - 1.6, "B1C: one frame of 18 s. Subframe 2 holds the whole ephemeris and the clock, so one good frame is enough.")

    # ---------- B: B-CNAV2 ----------
    y = 26.2
    ax.text(0, y + 1.15, "B.  B-CNAV2 on B2a:  600 symbols at 200 sps = 3 s", fontsize=10, weight="bold", color=NAVY)
    bar(ax, y - 0.5, [("preamble", 24, ORANGE), ("one 64-ary LDPC(96,48) codeword", 576, "#6f9bd6")], "frame\n600 symbols")
    bar(ax, y - 2.0, [("PRN", 6, ID_C), ("type", 6, ID_C), ("SOW", 18, TIME_C), ("message data (types 10, 11, 30, ...)", 234, MISC_C), ("CRC-24Q", 24, CRC_C)],
        "message\n288 bits", sub="after LDPC decoding")
    t10, t11, t30 = bcnav2._TYPE_10_HEAD, bcnav2._TYPE_11_HEAD, bcnav2._TYPE_30_HEAD
    e1 = bcnav2.EPHEMERIS_I_LAYOUT
    bar(ax, y - 3.8, [("WN", lay(t10, "wn"), TIME_C), ("flags", lay(t10, "dif_b2a", "sif_b2a", "aif_b2a", "sismai", "dif_b1c", "sif_b1c", "aif_b1c"), MISC_C),
                      ("IODE", lay(t10, "iode"), ID_C), ("toe, type", lay(e1, "toe_s", "sat_type"), TIME_C),
                      ("orbit part I: dA, dA-dot, dn0, dn0-dot, M0, e, omega", bcnav2.layout_bits(e1) - lay(e1, "toe_s", "sat_type"), ORBIT_C)], "type 10")
    bar(ax, y - 5.8, [("health", lay(t11, "hs"), MISC_C), ("flags", bcnav2.layout_bits(t11) - lay(t11, "hs"), MISC_C),
                      ("orbit part II: Omega0, i0, node and inclination rates, harmonic terms", bcnav2.layout_bits(bcnav2.EPHEMERIS_II_LAYOUT), ORBIT_C)], "type 11")
    clk, gd = bcnav2.CLOCK_LAYOUT, bcnav2._TYPE_30_GROUP_DELAY
    bar(ax, y - 7.8, [("health, flags", bcnav2.layout_bits(bcnav2._TYPE_30_HEAD), MISC_C), ("clock: toc, a0, a1, a2", bcnav2.layout_bits(clk), CLOCK_C),
                      ("IODC", lay(gd, "iodc"), ID_C), ("TGD B2ap, ISC B2ad", lay(gd, "tgd_b2ap_s", "isc_b2ad_s"), CLOCK_C),
                      ("BDGIM ionosphere: 9 terms", bcnav2.layout_bits(bcnav2.BDGIM_LAYOUT), PURPLE), ("TGD B1Cp", bcnav2.layout_bits(bcnav2._TYPE_30_TGD_B1CP), CLOCK_C),
                      ("reserved", bcnav2._TYPE_30_RESERVED_BITS, GREY)], "type 30")
    for name, layout_total in (("type 10", 234),):
        pass
    note(ax, 0, y - 9.4, "Time: SOW names the first symbol of its own frame (BDT).   Orbit: types 10 and 11, next to each other.   Clock and group delays: type 30, matched when IODE = IODC low byte.")

    # ---------- C: B-CNAV1 ----------
    y = 13.8
    ax.text(0, y + 1.15, "C.  B-CNAV1 on B1C:  1800 symbols at 100 sps = 18 s, no preamble", fontsize=10, weight="bold", color=NAVY)
    bar(ax, y - 0.5, [("sf 1", bcnav1.SUBFRAME1_SYMBOLS, ORANGE), ("subframes 2 and 3, interleaved in a 36 x 48 array", bcnav1.INTERLEAVED_SYMBOLS, "#6f9bd6")],
        "frame\n1800 symbols")
    bar(ax, y - 2.2, [("PRN field: BCH(21,6)", bcnav1.PRN_CODE_SYMBOLS, ID_C), ("seconds of hour: BCH(51,8)", bcnav1.SOH_CODE_SYMBOLS, TIME_C)],
        "subframe 1\n72 symbols", sub="zoom: the receiver correlates this field to find the frame start")
    bar(ax, y - 3.9, [("subframe 2: LDPC(200,100)", bcnav1.SUBFRAME2_SYMBOLS, "#6f9bd6"), ("subframe 3: LDPC(88,44)", bcnav1.SUBFRAME3_SYMBOLS, BLUE)],
        "two codewords\n1728 symbols", sub="before interleaving")
    e1b, e2b = bcnav2.EPHEMERIS_I_LAYOUT, bcnav2.EPHEMERIS_II_LAYOUT
    sub2 = [("WN", lay(bcnav1._SUBFRAME2_HEAD, "wn"), TIME_C), ("HOW", lay(bcnav1._SUBFRAME2_HEAD, "how"), TIME_C),
            ("IODC", lay(bcnav1._SUBFRAME2_HEAD, "iodc"), ID_C), ("IODE", lay(bcnav1._SUBFRAME2_HEAD, "iode"), ID_C),
            ("orbit part I", bcnav2.layout_bits(e1b), ORBIT_C), ("orbit part II", bcnav2.layout_bits(e2b), ORBIT_C),
            ("clock: toc, a0, a1, a2", bcnav2.layout_bits(bcnav2.CLOCK_LAYOUT), CLOCK_C),
            ("TGD B2ap, ISC B1Cd, TGD B1Cp", 36, CLOCK_C),
            ("reserved", bcnav1._SUBFRAME2_RESERVED_BITS, GREY), ("CRC-24Q", 24, CRC_C)]
    assert sum(p[1] for p in sub2) == bcnav1.SUBFRAME2_BITS
    bar(ax, y - 5.9, sub2, "subframe 2\n600 bits")
    h1 = bcnav1._PAGE1_HEAD
    sub3 = [("page id", lay(h1, "page_id"), ID_C), ("health, flags, SISMAI, SISAI", bcnav2.layout_bits(h1) - lay(h1, "page_id"), MISC_C),
            ("BDGIM ionosphere: 9 terms", bcnav2.layout_bits(bcnav2.BDGIM_LAYOUT), PURPLE), ("BDT-UTC offset", 97, TIME_C),
            ("reserved", 27, GREY), ("CRC-24Q", 24, CRC_C)]
    assert sum(p[1] for p in sub3) == bcnav1.SUBFRAME3_BITS
    bar(ax, y - 8.0, sub3, "subframe 3\npage 1 of 4\n264 bits")
    note(ax, 0, y - 9.5, "Subframe 3 has four page types: page 1 as shown, page 2 reduced almanac, page 3 earth orientation and BGTO, page 4 midi almanac.")
    note(ax, 0, y - 10.1, "Time: frame start = HOW x 3600 + SOH x 18 s (BDT), with WN from subframe 2.   Ephemeris, clock, group delays and week: subframe 2 only.")
    note(ax, 0, y - 10.7, "Receiver steps: find the start with the PRN field and the SOH field, undo the interleaver, LDPC-decode both codewords, check the CRC-24Q of each subframe.")

    # ---------- legend ----------
    items = [("time", TIME_C), ("orbit", ORBIT_C), ("clock, group delay", CLOCK_C), ("identity, issue numbers", ID_C),
             ("ionosphere", PURPLE), ("health, integrity", MISC_C), ("CRC-24Q", CRC_C)]
    x = 0.0
    for name, c in items:
        ax.add_patch(Rectangle((x, 1.9), 1.5, 0.55, fc=c, ec="none"))
        ax.text(x + 2.0, 2.17, name, fontsize=6.8, va="center", color="#333")
        x += 2.4 + len(name) * 0.78 + 1.6
    fig.savefig(OUT / "messages.png", dpi=170, bbox_inches="tight", facecolor="white")
    fig.savefig(OUT / "messages.pdf", bbox_inches="tight", facecolor="white")
    print("wrote messages.png and messages.pdf")


if __name__ == "__main__":
    main()
