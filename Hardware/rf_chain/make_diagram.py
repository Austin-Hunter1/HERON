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
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle  # noqa: E402

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
            "provisional (0 dB). Port assignment follows the 2026-10-01 test (D-029).",
            fontsize=7.5, style="italic")
    return fig


# Presentation style: flat colors, large text, 16:9. Looks only, not design values.
P_COLORS = {"ant": "#2b6cb0", "tee": "#c05621", "sdr": "#2f855a", "aux": "#4a5568",
            "dc": "#c05621", "bg": "#f7fafc", "ink": "#1a202c", "muted": "#4a5568"}


def minus(text: str) -> str:
    """Use the true minus sign in a label, so negative levels read well on a slide."""
    return re.sub(r"-(?=\d)", "−", text)


def p_wrap(text: str, width: int) -> str:
    """Wrap a part description. Part numbers with hyphens stay in one piece."""
    return textwrap.fill(text, width, break_long_words=False, break_on_hyphens=False)


def p_block(ax, x, y, w, h, title, lines, color, title_size=14, body_size=12):
    """Flat block with a colored header band, a white body, and centered text lines."""
    band = 5.5
    ax.add_patch(Rectangle((x, y), w, h, fc="white", ec=color, lw=2.5, zorder=2))
    ax.add_patch(Rectangle((x, y + h - band), w, band, fc=color, ec=color, lw=2.5, zorder=3))
    ax.text(x + w / 2, y + h - band / 2, title, ha="center", va="center", color="white",
            fontsize=title_size, fontweight="bold", zorder=4)
    if lines:
        ax.text(x + w / 2, y + (h - band) / 2, NL.join(lines), ha="center", va="center",
                fontsize=body_size, color=P_COLORS["ink"], linespacing=1.45, zorder=4)


def p_arrow(ax, p0, p1, label="", color="#1a202c", dashed=False, head=22, label_size=11.5,
            side=False):
    """Thick arrow with an optional label above (or beside) its middle."""
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="-|>", mutation_scale=head, lw=3.0,
                                 ls=(0, (4, 3)) if dashed else "-", color=color, zorder=5))
    if label:
        mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
        if side:
            ax.text(mx + 1.5, my, label, ha="left", va="center", fontsize=label_size,
                    color=color, fontweight="bold", zorder=6)
        else:
            ax.text(mx, my + 1.3, label, ha="center", va="bottom", fontsize=label_size,
                    color=color, fontweight="bold", zorder=6)


def p_card(ax, x, y, w, h, title, metrics, foot, margin=None, margin_ok=True):
    """Result card: a title, three big numbers with small labels, a margin line, a footnote."""
    ax.add_patch(Rectangle((x, y), w, h, fc="white", ec="#cbd5e0", lw=2, zorder=2))
    ax.text(x + 2, y + h - 1.6, title, fontsize=14, fontweight="bold", va="top",
            color=P_COLORS["ink"], zorder=4)
    step = w / len(metrics)
    for i, (value, label) in enumerate(metrics):
        cx = x + step * (i + 0.5)
        ax.text(cx, y + h * 0.57, value, ha="center", va="center", fontsize=24,
                fontweight="bold", color=P_COLORS["ant"], zorder=4)
        ax.text(cx, y + h * 0.40, label, ha="center", va="center", fontsize=10.5,
                color=P_COLORS["muted"], zorder=4)
    if margin:
        ax.text(x + w / 2, y + 3.9, margin, ha="center", va="bottom", fontsize=11,
                fontweight="bold", color=P_COLORS["sdr"] if margin_ok else "#c53030", zorder=4)
    ax.text(x + w / 2, y + 1.0, foot, ha="center", va="bottom", fontsize=10,
            color=P_COLORS["muted"], zorder=4)


def margin_text(req: dict | None) -> tuple[str | None, bool]:
    """Card line for the LNA gain margin against the C/N0 target, and whether it is met."""
    if req is None:
        return None, True
    if req["required_gain_db"] is None:
        return "no LNA gain is enough for the target", False
    ok = req["margin_db"] >= 0
    return minus(f"LNA gain margin {req['margin_db']:+.1f} dB "
                 f"(needs {req['required_gain_db']:.1f} dB)"), ok


def draw_presentation(data: dict) -> plt.Figure:
    """Build the slide-style figure from the JSON data."""
    cfg, results = data["config"], data["results"]
    chains, comps = cfg["chains"], cfg["components"]
    fig, ax = plt.subplots(figsize=(16, 9))
    fig.patch.set_facecolor(P_COLORS["bg"])
    ax.set_xlim(0, 160)
    ax.set_ylim(0, 90)
    ax.axis("off")
    ax.text(3, 87, "HERON RF front end", fontsize=26, fontweight="bold", va="center",
            color=P_COLORS["ink"])
    first_band = chains[0]["bands"][0]
    target = (cfg.get("requirements") or {}).get("target_cn0_dbhz")
    target_text = "" if target is None else f"   |   C/N0 target {target:g} dB-Hz"
    ax.text(3, 82.2, f"Antenna → bias tee → Ettus B210   |   {cfg['environment']['case']} "
            f"case   |   arrow labels: {first_band} signal level{target_text}", fontsize=13,
            va="center", color=P_COLORS["muted"])

    lane_y = [69.0, 35.0]  # Lane centers, top lane first.
    sdr = comps[chains[0]["sdr"]]
    p_block(ax, 113, 24, 26, 56, "Ettus USRP B210", [], P_COLORS["sdr"])
    lo, hi = sdr["gain_range_db"]
    max_in = minus(format(sdr["max_input_dbm"], "g"))
    ax.text(126, 52, f"RX gain {lo:g} to {hi:g} dB{NL}NF about "
            f"{next(iter(sdr['nf_db'].values())):g} dB{NL}max input {max_in} dBm{NL}{NL}"
            f"ADC · FPGA · USB 3.0",
            ha="center", va="center", fontsize=11, color=P_COLORS["ink"], linespacing=1.5,
            zorder=4)
    p_block(ax, 146, 40, 13, 14, "Computer", ["heron_", "recorder"], P_COLORS["aux"],
            title_size=11, body_size=10.5)
    p_arrow(ax, (139.6, 47), (145.4, 47), "USB", color=P_COLORS["muted"], head=18, label_size=10)
    p_block(ax, 146, 60, 13, 14, "Clock", ["10 MHz", "+ PPS"], P_COLORS["aux"],
            title_size=11, body_size=10.5)
    p_arrow(ax, (146, 67), (139.6, 67), color=P_COLORS["muted"], head=18)

    for lane, chain in zip(lane_y, chains, strict=False):
        res = results[chain["name"]]
        ant, tee = comps[chain["active_antenna"]], comps[chain["bias_tee"]]
        band0 = chain["bands"][0]
        rows0 = res[band0]["rows"]
        lna = stage(rows0, chain["stages"][0])
        tee_row = stage(rows0, chain["bias_tee"])
        gains = " · ".join(f"{b} {stage(res[b]['rows'], chain['stages'][0])['gain_db']:+.1f}"
                                for b in chain["bands"])
        p_block(ax, 3, lane - 9, 40, 18, chain["name"],
                [p_wrap(ant["description"].replace(" (LNA)", ""), 32),
                 f"LNA gain {gains} dB", f"LNA NF {lna['nf_db']:g} dB"], P_COLORS["ant"])
        p_block(ax, 62, lane - 9, 32, 18, "Bias tee",
                [p_wrap(tee["description"].replace(" bias tee", ""), 30),
                 f"loss {-tee_row['gain_db']:g} dB"], P_COLORS["tee"])
        p_arrow(ax, (43.6, lane), (61.4, lane), minus(f"{lna['signal_dbm']:.1f} dBm"))
        p_arrow(ax, (94.6, lane), (112.4, lane), minus(f"{tee_row['signal_dbm']:.1f} dBm"))
        port = "RF A · RX2" if lane == lane_y[0] else "RF B · RX2"
        ax.add_patch(Rectangle((116, lane - 5.5), 20, 11, fc=P_COLORS["sdr"], ec="none",
                               zorder=3))
        ax.text(126, lane + 1.7, port, ha="center", va="center", color="white", fontsize=13,
                fontweight="bold", zorder=4)
        ax.text(126, lane - 2.3, f"SDR gain {chain['sdr_gain_db']:g} dB", ha="center",
                va="center", color="white", fontsize=11.5, zorder=4)

    volts = [c.get("supply_voltage_v") for c in chains]
    shown = "TBD" if all(v is None for v in volts) else ", ".join(
        f"{v:g} V" for v in volts if v is not None)
    p_block(ax, 62, 48, 32, 8, "Jackery power", [f"voltage: {shown}"], P_COLORS["aux"],
            title_size=11, body_size=10.5)
    p_arrow(ax, (78, 47.8), (78, 44.4), "DC", color=P_COLORS["dc"], dashed=True, head=14,
            label_size=10, side=True)
    p_arrow(ax, (78, 56.2), (78, 59.6), "DC", color=P_COLORS["dc"], dashed=True, head=14,
            label_size=10, side=True)

    cards = [(c["name"], b) for c in chains for b in c["bands"]]
    gap = 3.0
    width = (154 - gap * (len(cards) - 1)) / len(cards)
    for i, (name, band) in enumerate(cards):
        r = results[name][band]
        margin, margin_ok = margin_text(data.get("requirements", {}).get(name, {}).get(band))
        p_card(ax, 3 + i * (width + gap), 3.5, width, 19.5, f"{name.split(' (')[0]} · {band}",
               [(f"{r['cn0_dbhz']:.1f}", "C/N0 dB-Hz"),
                (f"{r['nf_total_db']:.2f}", "NF dB"),
                (f"{r['gain_to_sdr_input_db']:+.1f}", "gain dB")],
               minus(f"noise {r['noise_at_sdr_input_dbm']:.1f} dBm · "
                     f"headroom {r['headroom_db']:.0f} dB"), margin, margin_ok)
    ax.text(3, 0.9, "Minimum GPS signal at a 0 dBic antenna · noise kTB at 290 K · "
            "SMA link loss provisional (0 dB) · numbers from rf_chain.example.toml",
            fontsize=9, color=P_COLORS["muted"], va="center")
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
