"""Draw the RF front-end block diagram as a PNG.

The figure shows the structure (antenna, bias tee, B210, supply, computer). All
numbers on it (gain, noise figure, supply range, budget results) come from the
JSON file that `link_budget.py --json` writes. So the figure follows the config.
This tool needs only matplotlib. It does not read the TOML file itself.

Run:

    uv run python ../Hardware/rf_chain/link_budget.py CONFIG.toml --json budget.json
    python make_diagram.py budget.json ../../docs/images/rf_front_end_block_diagram.png
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # No window: the tool only writes a file.
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon, Rectangle  # noqa: E402

# Drawing style. These are looks of the figure, not design values.
COLORS = {"ant": "#dcebf7", "tee": "#fde9c9", "sdr": "#dff0d8", "aux": "#eeeeee"}
EDGE = "#333333"
NL = "\n"


def box(ax, x, y, w, h, text, color, size=9, bold_first=True):
    """Draw a rounded box. The first line is a bold title; the rest is centered below it."""
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.2",
                                fc=color, ec=EDGE, lw=1.3))
    title, _, rest = text.partition(NL) if bold_first else ("", "", text)
    if title:
        ax.text(x + w / 2, y + h - 0.9, title, ha="center", va="top", fontsize=size,
                fontweight="bold")
    ax.text(x + w / 2, y + (h - 2.4) / 2, rest, ha="center", va="center", fontsize=size - 0.5,
            linespacing=1.35)


def arrow(ax, p0, p1, label="", dashed=False, dx=0.0, dy=1.6, ha="center"):
    """Draw an arrow with a small label near its middle."""
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="-|>", mutation_scale=14, lw=1.4,
                                 ls="--" if dashed else "-", color=EDGE))
    if label:
        ax.text((p0[0] + p1[0]) / 2 + dx, (p0[1] + p1[1]) / 2 + dy, label, ha=ha, fontsize=8)


def stage(rows, name):
    """Return the first budget row with this stage name."""
    return next(r for r in rows if r["name"] == name)


def wrap(text, width):
    """Wrap a long part description so that it fits inside a box."""
    return textwrap.fill(text, width)


def budget_text(band, r):
    """Two text lines with the budget result of one band."""
    pad = "  "
    return (f"{band} at the B210 input: gain {r['gain_to_sdr_input_db']:+.1f} dB, "
            f"NF {r['nf_total_db']:.2f} dB, C/N0 {r['cn0_dbhz']:.1f} dB-Hz{NL}"
            f"{pad}signal {r['signal_at_sdr_input_dbm']:.1f} dBm, "
            f"noise {r['noise_at_sdr_input_dbm']:.1f} dBm, headroom {r['headroom_db']:.1f} dB")


def draw_detailed(data: dict) -> plt.Figure:
    """Build the dense engineering figure from the JSON data."""
    cfg, results = data["config"], data["results"]
    chains = cfg["chains"]
    comps = cfg["components"]
    fig, ax = plt.subplots(figsize=(17, 10.5))
    ax.set_xlim(0, 150)
    ax.set_ylim(-14, 70)
    ax.axis("off")
    ax.set_title(f"HERON RF front end: antennas, bias tees, and B210 "
                 f"({cfg['environment']['case']} case)", fontsize=14, fontweight="bold")

    lane_y = [50.0, 8.0]  # Lane centers, top lane first.
    sdr = comps[chains[0]["sdr"]]
    box(ax, 98, -3, 26, 61,
        f"Ettus USRP B210{NL}{NL}RX gain {sdr['gain_range_db'][0]:g} to "
        f"{sdr['gain_range_db'][1]:g} dB{NL}"
        f"NF {next(iter(sdr['nf_db'].values())):g} dB{NL}"
        f"Max input {sdr['max_input_dbm']:g} dBm{NL}{NL}ADC, FPGA, USB 3.0", COLORS["sdr"], 10)
    box(ax, 132, 20, 17, 16, f"Computer{NL}heron_recorder{NL}sc8 files", COLORS["aux"], 9)
    arrow(ax, (124.6, 28), (131.4, 28), "USB")
    box(ax, 98, 62, 26, 5, f"Clock board{NL}10 MHz + PPS", COLORS["aux"], 8, bold_first=False)
    arrow(ax, (111, 61.6), (111, 58.8), dashed=True, dy=0)

    for lane, chain in zip(lane_y, chains, strict=False):
        name = chain["name"]
        res = results[name]
        ant = comps[chain["active_antenna"]]
        tee = comps[chain["bias_tee"]]
        rows0 = res[chain["bands"][0]]["rows"]
        lna_row = stage(rows0, chain["stages"][0])
        tee_row = stage(rows0, chain["bias_tee"])
        gains = "  ".join(f"{b}: {stage(res[b]['rows'], chain['stages'][0])['gain_db']:+.1f} dB"
                          for b in chain["bands"])
        box(ax, 2, lane - 8, 38, 18,
            f"{name}{NL}{wrap(ant['description'], 38)}{NL}LNA gain {gains}{NL}"
            f"LNA NF {lna_row['nf_db']:g} dB{NL}"
            f"Supply {ant['supply_min_v']:g} to {ant['supply_max_v']:g} V, "
            f"{ant['supply_current_ma']:g} mA", COLORS["ant"], 9)
        box(ax, 54, lane - 6, 30, 16,
            f"Bias tee{NL}{wrap(tee['description'], 30)}{NL}RF&DC (SMA male) to{NL}"
            f"RF (SMA female){NL}Loss {-tee_row['gain_db']:g} dB typ", COLORS["tee"], 9)
        arrow(ax, (40.6, lane + 2), (53.4, lane + 2), "RF + DC (SMA)")
        arrow(ax, (84.6, lane + 2), (97.4, lane + 2), "RF only (SMA)")
        port = "RF A: RX2" if lane == lane_y[0] else "RF B: RX2"
        ax.text(98.8, lane - 3.2, f"{port}   SDR gain {chain['sdr_gain_db']:g} dB", fontsize=8.5,
                fontweight="bold")
        y = lane - 11.5  # Budget lines go under the antenna box.
        for band in chain["bands"]:
            ax.text(2, y, budget_text(band, res[band]), fontsize=8, family="monospace",
                    va="top", linespacing=1.4)
            y -= 5.6

    # Supply box between the lanes, with arrows to both DC pins.
    volts = [c.get("supply_voltage_v") for c in chains]
    shown = "TBD" if all(v is None for v in volts) else ", ".join(
        f"{v:g} V" for v in volts if v is not None)
    box(ax, 54, 24, 30, 11,
        f"Jackery power station{NL}voltage: {shown}{NL}wiring to DC pins: TBD", COLORS["aux"], 9)
    arrow(ax, (69, 35.6), (69, 43.4), "DC pin", dx=1.2, dy=0, ha="left")
    arrow(ax, (69, 23.4), (69, 17.6), "DC pin", dx=1.2, dy=0, ha="left")
    ax.text(2, -13, "Gain, noise figure, and levels come from rf_chain.example.toml via "
            "link_budget.py. Minimum signal reference at a 0 dBic antenna; SMA link loss is "
            "provisional (0 dB). Port assignment follows the 2026-10-01 test (D-031).",
            fontsize=7.5, style="italic")
    return fig


# Presentation style: flat colors, big numbers, very little text, 16:9.
# These are looks of the figure, not design values.
P_COLORS = {"ant": "#2b6cb0", "tee": "#c05621", "sdr": "#2f855a", "aux": "#4a5568",
            "dc": "#c05621", "bg": "#f7fafc", "ink": "#1a202c", "muted": "#4a5568",
            "bad": "#c53030"}


def minus(text: str) -> str:
    """Use the true minus sign before a number, so negative values read well on a slide."""
    return re.sub(r"-(?=\d)", "−", text)


def p_block(ax, x, y, w, h, title, color, title_size=16):
    """Flat block with a colored header band and a white body. Returns the body center."""
    band = 6.0
    ax.add_patch(Rectangle((x, y), w, h, fc="white", ec=color, lw=2.5, zorder=2))
    ax.add_patch(Rectangle((x, y + h - band), w, band, fc=color, ec=color, lw=2.5, zorder=3))
    ax.text(x + w / 2, y + h - band / 2, title, ha="center", va="center", color="white",
            fontsize=title_size, fontweight="bold", zorder=4)
    return x + w / 2, y + (h - band) / 2


def p_name_box(ax, x, y, w, h, text, color):
    """Small flat box with one centered white label."""
    ax.add_patch(Rectangle((x, y), w, h, fc=color, ec=color, zorder=3))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", color="white", fontsize=12,
            fontweight="bold", zorder=4)


def p_arrow(ax, p0, p1, color="#1a202c", head=24):
    """Thick arrow."""
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="-|>", mutation_scale=head, lw=3.4,
                                 color=color, zorder=5))


def p_amplifier(ax, cx, cy, gain_db, color):
    """Amplifier triangle with the gain as one big number beside it."""
    ax.add_patch(Polygon([(cx - 15, cy - 6.5), (cx - 15, cy + 6.5), (cx - 3, cy)], closed=True,
                         fc=color, ec=color, zorder=3))
    ax.plot([cx - 21, cx - 15], [cy, cy], color=color, lw=3.4, zorder=3)
    ax.plot([cx - 3, cx + 1], [cy, cy], color=color, lw=3.4, zorder=3)
    ax.text(cx + 4, cy + 1.2, minus(f"{gain_db:+.0f}"), ha="left", va="center", fontsize=40,
            fontweight="bold", color=color, zorder=4)
    ax.text(cx + 4, cy - 6.2, "dB LNA gain", ha="left", va="center", fontsize=12,
            color=P_COLORS["muted"], zorder=4)


def p_card(ax, x, y, w, h, title, cn0, margin_text, margin_ok):
    """Result card: title, one big C/N0 number, and one margin line."""
    ax.add_patch(Rectangle((x, y), w, h, fc="white", ec="#cbd5e0", lw=2, zorder=2))
    ax.text(x + 2.5, y + h - 2, title, fontsize=16, fontweight="bold", va="top",
            color=P_COLORS["ink"], zorder=4)
    ax.text(x + w * 0.36, y + h * 0.50, f"{cn0:.1f}", ha="center", va="center", fontsize=38,
            fontweight="bold", color=P_COLORS["ant"], zorder=4)
    ax.text(x + w * 0.66, y + h * 0.50, "dB-Hz C/N0", ha="left", va="center", fontsize=14,
            color=P_COLORS["muted"], zorder=4)
    if margin_text:
        ax.text(x + w / 2, y + 1.4, margin_text, ha="center", va="bottom", fontsize=14,
                fontweight="bold", color=P_COLORS["sdr"] if margin_ok else P_COLORS["bad"],
                zorder=4)


def margin_text(req: dict | None) -> tuple[str | None, bool]:
    """Card line for the LNA gain margin against the C/N0 target, and whether it is met."""
    if req is None:
        return None, True
    if req["required_gain_db"] is None:
        return "target not reachable", False
    return minus(f"{req['margin_db']:+.0f} dB gain margin"), req["margin_db"] >= 0


def draw_presentation(data: dict) -> plt.Figure:
    """Build the slide-style figure from the JSON data."""
    cfg, results = data["config"], data["results"]
    chains = cfg["chains"]
    fig, ax = plt.subplots(figsize=(16, 9))
    fig.patch.set_facecolor(P_COLORS["bg"])
    ax.set_xlim(0, 160)
    ax.set_ylim(0, 90)
    ax.axis("off")
    ax.text(3, 86.5, "HERON RF front end", fontsize=30, fontweight="bold", va="center",
            color=P_COLORS["ink"])
    target = (cfg.get("requirements") or {}).get("target_cn0_dbhz")
    target_text = "" if target is None else f"     target {target:g} dB-Hz"
    ax.text(3, 80.5, f"Antenna → bias tee → B210{target_text}", fontsize=16,
            va="center", color=P_COLORS["muted"])

    lane_y = [64.0, 36.0]  # Lane centers, top lane first.
    p_block(ax, 108, 22, 32, 58, "Ettus B210", P_COLORS["sdr"])
    p_name_box(ax, 145, 46, 14, 8, "Computer", P_COLORS["aux"])
    p_arrow(ax, (140.6, 50), (144.4, 50), color=P_COLORS["muted"], head=18)
    p_name_box(ax, 145, 62, 14, 8, "Clock", P_COLORS["aux"])
    p_arrow(ax, (145, 66), (140.6, 66), color=P_COLORS["muted"], head=18)

    for lane, chain in zip(lane_y, chains, strict=False):
        res = results[chain["name"]]
        rows0 = res[chain["bands"][0]]["rows"]
        lna = stage(rows0, chain["stages"][0])
        tee_row = stage(rows0, chain["bias_tee"])
        short = chain["name"].split(" (")[0]
        cx, cy = p_block(ax, 3, lane - 11, 52, 22, f"{short} antenna", P_COLORS["ant"])
        p_amplifier(ax, cx - 4, cy - 0.5, lna["gain_db"], P_COLORS["ant"])
        bx, by = p_block(ax, 66, lane - 11, 32, 22, "Bias tee", P_COLORS["tee"])
        ax.text(bx, by + 1.5, minus(f"{tee_row['gain_db']:+.1f} dB"), ha="center", va="center",
                fontsize=26, fontweight="bold", color=P_COLORS["tee"], zorder=4)
        ax.text(bx, by - 5.2, "loss", ha="center", va="center", fontsize=12,
                color=P_COLORS["muted"], zorder=4)
        p_arrow(ax, (55.6, lane), (65.4, lane))
        p_arrow(ax, (98.6, lane), (113.4, lane))
        port = "RF A" if lane == lane_y[0] else "RF B"
        ax.add_patch(Rectangle((114, lane - 8.5), 22, 17, fc=P_COLORS["sdr"], ec="none",
                               zorder=3))
        ax.text(125, lane + 3.2, port, ha="center", va="center", color="white", fontsize=17,
                fontweight="bold", zorder=4)
        ax.text(125, lane - 3.0, f"SDR {chain['sdr_gain_db']:g} dB", ha="center", va="center",
                color="white", fontsize=15, zorder=4)

    # Power: a small badge between the lanes, joined to both bias tees.
    mid = (lane_y[0] + lane_y[1]) / 2
    ax.add_patch(Rectangle((66, mid - 2.2), 32, 4.4, fc=P_COLORS["aux"], ec="none", zorder=3))
    ax.text(82, mid, "Jackery power", ha="center", va="center", color="white", fontsize=12,
            fontweight="bold", zorder=4)
    for y0, y1 in ((mid + 2.2, lane_y[0] - 11), (mid - 2.2, lane_y[1] + 11)):
        ax.plot([82, 82], [y0, y1], color=P_COLORS["dc"], lw=3.0, ls=(0, (3, 2)), zorder=4)

    cards = [(c["name"], b) for c in chains for b in c["bands"]]
    gap = 3.0
    width = (154 - gap * (len(cards) - 1)) / len(cards)
    for i, (name, band) in enumerate(cards):
        text, ok = margin_text(data.get("requirements", {}).get(name, {}).get(band))
        p_card(ax, 3 + i * (width + gap), 3.0, width, 17.5,
               f"{name.split(' (')[0]} · {band}", results[name][band]["cn0_dbhz"], text, ok)
    ax.text(3, 0.9, "Minimum GPS signal level · typical case", fontsize=10,
            color=P_COLORS["muted"], va="center")
    return fig


DRAWERS = {"presentation": draw_presentation, "detailed": draw_detailed}


def main(argv: list[str] | None = None) -> int:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description="Draw the RF front-end block diagram.")
    parser.add_argument("json_file", type=Path, help="output of link_budget.py --json")
    parser.add_argument("png_file", type=Path, help="PNG file to write")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--style", choices=sorted(DRAWERS), default="presentation")
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.json_file.read_text(encoding="utf-8"))
        fig = DRAWERS[args.style](data)
    except (OSError, KeyError, ValueError, StopIteration) as exc:
        print(f"cannot draw the diagram from {args.json_file}: {exc!r}", file=sys.stderr)
        return 2
    args.png_file.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.png_file, dpi=args.dpi, bbox_inches="tight", facecolor="white")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
