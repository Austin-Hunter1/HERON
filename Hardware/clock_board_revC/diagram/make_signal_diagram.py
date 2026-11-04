"""Make a signal-flow circuit diagram of the HERON clock board rev C.

Run: python make_signal_diagram.py  ->  clock_board_revC_signal_diagram.svg

Why this file exists: the KiCad schematic is hard to read when you want to
follow one signal. This diagram shows each signal path from left to right with
the real reference designators and net names. The connections come from the
rev C netlist (`../project/heron_clock.net`), checked by hand on 2026-10-07.
It is a drawing aid. The KiCad schematic stays the source of truth.
"""
from pathlib import Path

OUT = Path(__file__).with_name("clock_board_revC_signal_diagram.svg")

# Colours by signal type. One table, so a colour change is one edit.
COL = {"pwr": "#c0392b", "clk": "#1f5fbf", "pps": "#d27a00", "gnd": "#555555", "ink": "#222222"}

parts: list[str] = []


def add(s: str) -> None:
    parts.append(s)


def text(x, y, s, size=12, anchor="middle", color=None, weight="normal", italic=False):
    st = ' font-style="italic"' if italic else ""
    add(f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" fill="{color or COL["ink"]}" '
        f'font-weight="{weight}"{st}>{s}</text>')


def line(x1, y1, x2, y2, c="ink", w=2, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{COL[c]}" stroke-width="{w}"{d}/>')


def box(x, y, w, h, ref, val, c="ink", fill="#ffffff", dnp=False):
    """A part: reference on top line, value on the second line."""
    dash = ' stroke-dasharray="5 3"' if dnp else ""
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="{fill}" stroke="{COL[c]}" '
        f'stroke-width="1.6"{dash}/>')
    if val:
        text(x + w / 2, y + h / 2 - 2, ref, 12, weight="bold")
        text(x + w / 2, y + h / 2 + 12, val, 11)
    else:
        text(x + w / 2, y + h / 2 + 4, ref, 12, weight="bold")


def gnd(x, y):
    """Ground symbol, top at (x, y)."""
    line(x, y, x, y + 6, "gnd", 1.6)
    for i, hw in enumerate((10, 6, 2)):
        line(x - hw, y + 6 + i * 4, x + hw, y + 6 + i * 4, "gnd", 1.6)


def dot(x, y, c="ink"):
    add(f'<circle cx="{x}" cy="{y}" r="3.2" fill="{COL[c]}"/>')


def net(x, y, name, c="ink", anchor="middle"):
    text(x, y, name, 11, anchor, COL[c], "bold", True)


def shunt(x, ybase, ref, val, c, dnp=False, drop=26):
    """A part from the signal line (at ybase) down to ground."""
    line(x, ybase, x, ybase + drop, c)
    box(x - 24, ybase + drop, 48, 36, ref, val, c, dnp=dnp)
    gnd(x, ybase + drop + 36)
    dot(x, ybase, c)


# ---------------------------------------------------------------- title
text(20, 28, "HERON clock board rev C: signal-flow circuit diagram", 20, "start", weight="bold")
text(20, 48, "Source: rev C netlist (heron_clock.net). Reference designators match the KiCad board. "
     "Ground symbols are all one GND net.", 12, "start", "#555555")

# ---------------------------------------------------------------- legend
for i, (k, lab) in enumerate((("pwr", "5 V / 3.3 V power"), ("clk", "10 MHz path"), ("pps", "PPS path"))):
    line(900 + i * 190, 24, 940 + i * 190, 24, k, 4)
    text(948 + i * 190, 28, lab, 12, "start")

# ---------------------------------------------------------------- A: power input
text(20, 84, "A. POWER INPUT (5 V from payload rail)", 14, "start", COL["pwr"], "bold")
yb = 120
box(20, yb - 22, 70, 44, "J1", "5V JST-GH", "pwr")
line(90, yb, 130, yb, "pwr")
box(130, yb - 22, 70, 44, "F1", "500 mA PTC", "pwr")
line(200, yb, 290, yb, "pwr")
shunt(250, yb, "D1", "SMF5.0A", "pwr")
box(290, yb - 22, 80, 44, "D2", "SS1030", "pwr")
line(370, yb, 410, yb, "pwr")
box(410, yb - 22, 70, 44, "FB1", "BLM18PG221", "pwr")
line(480, yb, 520, yb, "pwr")
net(500, yb - 28, "VIN_5V", "pwr")
dot(520, yb, "pwr")
text(20, 212, "J1 pin 1 = +5 V, pin 2 = GND. D2 blocks reverse polarity. FB1 filters the rail.", 11, "start", "#555555")
text(20, 227, "VIN_5V decoupling: C1 10 µF, C2 100 nF (at FB1); C3, C5 1 µF (at each LDO). Test point TP1.",
     11, "start", "#555555")

# VIN_5V feeds both LDOs through one bus above them
line(520, yb, 520, 262, "pwr")
line(190, 262, 520, 262, "pwr")
dot(380, 262, "pwr")
text(20, 252, "B. LOW-NOISE LDOs (input max 5.5 V)", 14, "start", COL["pwr"], "bold")
for i, (ref, val, rail, desc) in enumerate((
        ("U1", "LP5907-3.3", "+3V3_OSC", "TCXO only. Out: C4 2.2 µF"),
        ("U2", "TLV75533P", "+3V3_CLK", "Buffers. Out: C6 2.2 µF, C7 10 µF"))):
    x = 130 + i * 190
    line(x + 60, 262, x + 60, 278, "pwr")
    box(x, 278, 120, 48, ref, val, "pwr")
    line(x + 60, 326, x + 60, 346, "pwr")
    net(x + 60, 362, rail, "pwr")
    text(x + 60, 378, desc, 10, color="#555555")
text(20, 306, "IN = VIN_5V", 10, "start", "#555555")

# ---------------------------------------------------------------- C: TCXO + buffer
text(20, 420, "C. 10 MHz SOURCE", 14, "start", COL["clk"], "bold")
box(30, 436, 150, 80, "U3", "SiT5155 TCXO", "clk")
text(105, 502, "10 MHz, ±0.5 ppm, free-running", 10, color="#555555")
text(30, 558, "VDD = +3V3_OSC (C8 100 nF, C9 10 µF)", 10, "start", "#555555")
text(30, 574, "OE (pin 1) = OSC_OE, R2 10 kΩ pull-up = on", 10, "start", "#555555")
text(30, 590, "NC pins = GND (SiTime layout rule). TP5 on OSC_OE.", 10, "start", "#555555")
line(180, 476, 240, 476, "clk")
box(240, 460, 56, 32, "R3", "22 Ω", "clk")
line(296, 476, 420, 476, "clk")
net(358, 466, "CLK10_IN", "clk")
text(358, 492, "TP4", 10, color="#555555")

# U4 buffer. Its four outputs run by elbow lines to the four filter chains.
ROWS = [150, 330, 510, 690]  # baseline y of the four 10 MHz chains
U4X, U4W, U4Y, U4H = 420, 110, 420, 200
PORTS = [450, 500, 550, 600]  # output pin y positions on U4
VX = [536, 544, 544, 552]     # x of each elbow; order stops lines from crossing
box(U4X, U4Y, U4W, U4H, "", "", "clk", fill="#eef4ff")
text(U4X + U4W / 2, 500, "U4", 14, weight="bold")
text(U4X + U4W / 2, 518, "LMK1C1104", 12)
text(U4X + U4W / 2, 534, "1:4 buffer", 12)
text(U4X + 4, 436, "pin 1 = CLK10_IN", 9, "start", "#555555")
text(U4X + 4, 562, "VDD = +3V3_CLK", 9, "start", "#555555")
text(U4X + 4, 574, "C10 100 nF, C11 1 µF", 9, "start", "#555555")
text(U4X + 4, 590, "OE = REF_EN", 9, "start", "#555555")
text(U4X + 4, 602, "R4 10 kΩ pull-up", 9, "start", "#555555")
CHX = 610  # x where the filter chains start

# ---------------------------------------------------------------- D: four 10 MHz chains
text(CHX, 84, "D. 10 MHz OUTPUTS x4: DC block, 5th-order low-pass (fc about 13 MHz, 50 Ω), 3 dB pad, ESD, SMP jack",
     14, "start", COL["clk"], "bold")
CH = [  # (REF net, series R, DC block C, shunt 270p, L1, shunt 470p, L2, shunt 270p, shunt 294, series 17.4, shunt 294, ESD, jack, name)
    ("REF1_Y", "R5", "C12", "C13", "L1", "C14", "L2", "C15", "R6", "R7", "R8", "D4", "J3", "B210_1"),
    ("REF2_Y", "R9", "C16", "C17", "L3", "C18", "L4", "C19", "R10", "R11", "R12", "D5", "J4", "B210_2"),
    ("REF3_Y", "R13", "C20", "C21", "L5", "C22", "L6", "C23", "R14", "R15", "R16", "D6", "J5", "B200_1"),
    ("REF4_Y", "R17", "C24", "C25", "L7", "C26", "L8", "C27", "R18", "R19", "R20", "D7", "J6", "B200_2"),
]
for row, ch in zip(ROWS, CH):
    (rn, r0, cdc, c270a, l1, c470, l2, c270b, r294a, r174, r294b, esd, jack, sdr) = ch
    x = CHX
    i_row = ROWS.index(row)
    pyy, vx = PORTS[i_row], VX[i_row]
    line(U4X + U4W, pyy, vx, pyy, "clk")
    line(vx, pyy, vx, row, "clk")
    line(vx, row, x, row, "clk")
    net(U4X + U4W + 4, pyy - 6, rn, "clk", "start") if False else net(x - 30, row - 8, rn, "clk")
    # series parts with their gaps; shunts hang below the gap centres
    series = [(r0, "0 Ω"), (cdc, "100 nF"), (l1, "820 nH"), (l2, "820 nH"), (r174, "17.4 Ω")]
    gap = 96
    sw = 62
    xs = []
    for i, (ref, val) in enumerate(series):
        xs.append(x)
        box(x, row - 18, sw, 36, ref, val, "clk")
        nx = x + sw + gap
        line(x + sw, row, nx, row, "clk")
        x = nx
    # x is now the jack column start
    shunts = {  # gap index after series i -> list of (ref, val)
        1: [(c270a, "270 pF")],
        2: [(c470, "470 pF")],
        3: [(c270b, "270 pF"), (r294a, "294 Ω")],
        4: [(r294b, "294 Ω"), (esd, "ESD")],
    }
    for gi, lst in shunts.items():
        gx = xs[gi - 1] + sw + gap / 2 if gi < 4 else xs[3] + sw + gap / 2
        if gi == 4:
            gx = xs[4] + sw + gap / 2
        n = len(lst)
        if n == 1:
            shunt(gx, row, lst[0][0], lst[0][1], "clk")
        else:
            for k, (rf, vl) in enumerate(lst):
                off = (-28 if k == 0 else 28)
                px = gx + off
                if k == 0:
                    pass
                line(px, row, px, row + 26, "clk")
                box(px - 24, row + 26, 48, 36, rf, vl, "clk", fill="#f7f7f7" if rf.startswith("D") else "#ffffff")
                gnd(px, row + 62)
                dot(px, row, "clk")
    # jack
    jx = x
    box(jx, row - 20, 74, 40, jack, "SMP jack", "clk", fill="#eef4ff")
    text(jx + 82, row + 4, f"to {sdr} 10 MHz", 11, "start", weight="bold")
    # node labels
    text(xs[0] + sw + gap / 2, row - 24, "DC block", 10, color="#555555")
    text(xs[2] + sw + gap / 2 - 10, row - 24, "LPF", 10, color="#555555")
    text(xs[4] + sw + gap / 2, row - 24, "pad + ESD", 10, color="#555555")

text(CHX, 780, "Level at a 50 Ω load: about +7 dBm (1.4 Vpp). C13/C15 = 270 pF, C14 = 470 pF, L = 820 nH (Chebyshev 0.1 dB). "
     "R7, R6, R8 make a 3 dB pi pad.", 11, "start", "#555555")

# ---------------------------------------------------------------- E: PPS
text(20, 830, "E. PPS: SMA input, Schmitt buffer, quad buffer, four outputs (3.3 V CMOS, DC coupled)", 14, "start", COL["pps"], "bold")
py = 920
box(20, py - 22, 74, 44, "J2", "PPS IN (SMA)", "pps", fill="#fff6e8")
text(57, py + 40, "from GNSS receiver", 10, color="#555555")
line(94, py, 215, py, "pps")
shunt(130, py, "D8", "ESD", "pps")
shunt(185, py, "R23", "49.9 Ω DNP", "pps", dnp=True)
net(150, py - 10, "PPS_IN", "pps")
box(215, py - 18, 62, 36, "R22", "100 Ω", "pps")
line(277, py, 340, py, "pps")
shunt(300, py, "R21", "10 kΩ", "pps")
box(340, py - 34, 130, 68, "U5", "74LVC1G17", "pps")
text(405, py + 26, "Schmitt, 5 V tolerant", 9, color="#555555")
text(405, py - 40, "VDD +3V3_CLK, C28 100 nF", 9, color="#555555")
line(470, py, 560, py, "pps")
net(515, py - 10, "PPS_BUF", "pps")
dot(520, py, "pps")
# LED branch and test point
line(520, py, 520, py + 62, "pps")
box(498, py + 62, 44, 32, "R24", "1 kΩ", "pps")
line(520, py + 94, 520, py + 108, "pps")
box(498, py + 108, 44, 32, "D9", "YEL LED", "pps")
gnd(520, py + 140)
text(520, py + 170, "PPS activity LED", 10, color="#555555")
text(578, py - 30, "TP6", 10, "start", "#555555")
# U6 buffer
line(560, py, 560, py, "pps")
box(560, 840, 130, 160, "", "", "pps", fill="#fff6e8")
text(625, 900, "U6", 14, weight="bold")
text(625, 918, "SN74LVC125A", 12)
text(625, 934, "quad buffer", 12)
text(625, 954, "inputs 2,5,9,12 = PPS_BUF", 9, color="#555555")
text(625, 968, "OE pins 1,4,10,13 = GND (on)", 9, color="#555555")
text(625, 982, "VDD +3V3_CLK, C29 100 nF", 9, color="#555555")
line(540, py, 560, py, "pps")
PR = [870, 920, 970, 1020]
pps_chain = [("PPS1_Y", "R25", "D10", "J7", "B210_1"), ("PPS2_Y", "R26", "D11", "J8", "B210_2"),
             ("PPS3_Y", "R27", "D12", "J9", "B200_1"), ("PPS4_Y", "R28", "D13", "J10", "B200_2")]
for y, (rn, rr, dd, jj, sdr) in zip(PR, pps_chain):
    line(690, y, 760, y, "pps")
    net(725, y - 6, rn, "pps")
    box(760, y - 16, 56, 32, rr, "22 Ω", "pps")
    line(816, y, 920, y, "pps")
    # ESD to ground, small
    line(870, y, 870, y + 12, "pps")
    box(850, y + 12, 40, 22, dd, "", "pps", fill="#f7f7f7")
    gnd(870, y + 34)
    dot(870, y, "pps")
    box(920, y - 18, 74, 36, jj, "SMP jack", "pps", fill="#fff6e8")
    text(1004, y + 4, f"to {sdr} PPS", 11, "start", weight="bold")
text(1180, 880, "ESD diodes D10 to D13 go from the signal to GND (pin 2).", 10, "start", "#555555")
text(1180, 895, "Output = 3.3 V CMOS into the SDR PPS input.", 10, "start", "#555555")

# ---------------------------------------------------------------- notes
text(1180, 940, "CHECK POINTS", 12, "start", weight="bold")
for i, s in enumerate((
        "TP1 VIN_5V, TP2 +3V3_OSC, TP3 +3V3_CLK, TP4 CLK10_IN,",
        "TP5 OSC_OE, TP6 PPS_BUF, TP7 GND.",
        "Which SDR SMA is 10 MHz and which is PPS: verify on one SDR (Q-015).")):
    text(1180, 958 + i * 15, s, 10, "start", "#555555")

H = 1100
svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1650 {H}" width="1650" height="{H}" '
       f'font-family="Segoe UI, Arial, sans-serif">'
       f'<rect width="1650" height="{H}" fill="#ffffff"/>' + "".join(parts) + "</svg>")
OUT.write_text(svg, encoding="utf-8")
print("wrote", OUT)
