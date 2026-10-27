"""Generate the HERON clock board rev D (KiCad 10, 4 layers, 118 x 60 mm).

Rev D puts each jack on the SDR connector it must mate with (D-042):
the 10 MHz jacks J3-J6 face the SDR "10MHz" SMA and the PPS jacks J7-J10
face the "PPS" SMA. The SDR USB-B connector sits between the two, so the
board has a hole (or, at the right edge, a notch) for each USB plug. A
copper and part keep-out ring around each hole lets the hole grow later
without new layout. The circuit is the rev C circuit (same schematic and
parts, including the JST XH J1 of D-040 and the Panjit D2 land of D-041).

Run it with the Python that comes with KiCad 10 (it needs pcbnew), from
this folder:

    KICAD10_FOOTPRINT_DIR=<stock footprints> FREEROUTING_JAR=<jar> \\
        <KiCad 10>/bin/python3 gen_pcb.py [--no-route]

Inputs : project/heron_clock.net (kicad-cli sch export netlist),
         project/heron_clock.kicad_sch (symbol UUIDs), revd_geom.py
Output : project/heron_clock.kicad_pcb

Routing: by hand for the 10 MHz filter columns, the PPS output chains, and
the TCXO supply and clock (SiT5155 layout guide). A short stub and via for
every SMD GND pad. Freerouting 1.9.0 for the other nets. GND is the solid
In1 plane, plus pours on F.Cu, In2.Cu and B.Cu and a stitching-via grid.
"""

import os
import re
import subprocess
import sys

import pcbnew

import revd_geom as G
from pcbgen import Gen, F, IN2, B, W_RF, W_SIG, W_PWR, autoroute

HERE = os.path.dirname(os.path.abspath(__file__))
PRJ = os.path.join(HERE, "project")
PCB = os.path.join(PRJ, "heron_clock.kicad_pcb")
FPLIB = os.environ.get("KICAD10_FOOTPRINT_DIR", "/usr/share/kicad/footprints")
KICAD_CLI = os.environ.get("KICAD_CLI", "kicad-cli")
FREEROUTING = os.environ.get("FREEROUTING_JAR", os.path.expanduser("~/.local/share/freerouting/freerouting-1.9.0.jar"))

XJ, Y_REF, Y_PPS = G.XJ, G.Y_REF, G.Y_PPS

# ---------------------------------------------------------------- 10 MHz filter columns
# The rev C column (make_revc.py), mirrored: the column runs on the line
# x = XJ + COL_DX, left of its jack, and the jack turns 180 degrees so that
# its signal tab points at the column. All series parts have pin 1 at the top.
COL_DX = -7.5                  # line offset from the jack axis (keeps the column vias 0.45 mm off the USB keep-out)
SERIES_DY = {                  # centre y relative to the node at the jack tab (rev C values)
    "R0": -15.03, "C_DC": -11.93, "L1": -8.56, "L2": -4.92, "R_SER": -1.55, "R_SH2": 1.55,
}
SH_DX, ESD_DX = 1.72, 1.57     # shunt and ESD axis offsets from the line (rev C values)
# (role, side: +1 toward the jack / -1 away, y of the pin-1 stub relative to the
# tab node, direction of pin 2: -1 up / +1 down). Rev C values, sides mirrored.
SHUNTS = [
    ("C_SH1", +1, -10.37, -1), ("C_SH2", +1, -6.74, +1), ("C_SH3", -1, -3.78, -1),
    ("R_SH1", -1, -2.28, +1), ("ESD", -1, 0.92, +1),
]
COLUMNS = [
    dict(R0="R5", C_DC="C12", C_SH1="C13", L1="L1", C_SH2="C14", L2="L2", C_SH3="C15",
         R_SH1="R6", R_SER="R7", R_SH2="R8", ESD="D4", J="J3"),
    dict(R0="R9", C_DC="C16", C_SH1="C17", L1="L3", C_SH2="C18", L2="L4", C_SH3="C19",
         R_SH1="R10", R_SER="R11", R_SH2="R12", ESD="D5", J="J4"),
    dict(R0="R13", C_DC="C20", C_SH1="C21", L1="L5", C_SH2="C22", L2="L6", C_SH3="C23",
         R_SH1="R14", R_SER="R15", R_SH2="R16", ESD="D6", J="J5"),
    dict(R0="R17", C_DC="C24", C_SH1="C25", L1="L7", C_SH2="C26", L2="L8", C_SH3="C27",
         R_SH1="R18", R_SER="R19", R_SH2="R20", ESD="D7", J="J6"),
]
SMP_REF_ROT, SMP_PPS_ROT = 180, 0     # signal tab toward -x (10 MHz) and +x (PPS)

# ---------------------------------------------------------------- PPS output chains
# Jack tab (+x) -> node track -> ESD diode pin 1 (upright, GND pin below) ->
# 22 R lying flat, pin 2 on the node. No dog-leg around a jack leg, so every
# leg pad keeps its full thermal relief (the rev C starved thermals).
PPS_ESD_DX, PPS_R_DX = 6.0, 8.3

# ---------------------------------------------------------------- the other parts
# (x, y, rotation) in board-local mm. Blocks (see the README floorplan):
# power input and LDOs left, TCXO + LMK under the B210_2 hole, PPS buffer
# right of J8, PPS input with J2 in the gap between the B200_1 and B200_2
# blades (a cable on J2 needs that open space in front of the board).
_LDO = (-5.3, 18.6)            # the rev C LDO block moved as one piece, under the B210_1 hole
PARTS = {
    # power input (rev C positions; J1 mouth on the top edge, between two SDR DC plugs)
    "J1": (36.10, 7.85, 180), "F1": (28.25, 13.38, 0), "D1": (28.25, 9.78, 180),
    "D2": (32.65, 13.38, 180), "FB1": (35.45, 15.58, 270), "C1": (32.05, 17.38, 180),
    "C2": (35.45, 18.67, 270), "TP1": (31.20, 22.00, 0),
    # LDOs (rev C block + _LDO)
    "U1": (27.45, 24.67, 0), "U2": (27.45, 31.18, 0), "C3": (23.65, 24.67, 90),
    "C5": (23.65, 31.18, 90), "C4": (31.05, 23.78, 90), "C6": (31.05, 30.28, 90),
    "C7": (33.45, 30.28, 90), "D3": (29.95, 34.37, 0), "R1": (33.05, 34.37, 180),
    "TP2": (33.25, 26.78, 0), "TP3": (36.05, 34.37, 0),
    # TCXO + LMK1C1104 (under the B210_2 hole)
    "U4": (55.0, 43.2, 180), "C10": (49.6, 43.0, 90), "C11": (49.6, 46.6, 90),
    "R4": (61.0, 43.4, 90), "U3": (56.5, 49.3, 180), "TP4": (49.6, 50.6, 0),
    "TP5": (61.8, 47.6, 0),      # beside R2: H6 takes the strip under U3 (D-043)
    # PPS buffer (right of J8)
    "U6": (60.0, 11.0, 0), "C29": (65.6, 7.6, 90),   # U6 under H5 (D-043)
    # PPS input (rev C block moved by (+3.89, -15.1), between the B200 blades)
    "J2": (94.0, 22.5, 0), "D8": (90.68, 16.1, 90), "R23": (92.25, 16.1, 90),
    "R22": (94.6, 16.1, 0), "R21": (96.95, 16.1, 90), "U5": (94.0, 11.9, 0),
    "C28": (97.4, 8.9, 90), "R24": (90.8, 8.5, 90), "D9": (90.8, 5.3, 90),
    "TP6": (97.6, 5.3, 0),
    # mechanical
    "FID1": (4.5, 15.0, 0), "FID2": (114.5, 13.0, 0), "FID3": (114.5, 46.0, 0),
    "TP7": (4.5, 24.0, 0),
}
LDO_REFS = ("U1", "U2", "C3", "C5", "C4", "C6", "C7", "D3", "R1", "TP2", "TP3")
# 2-pin parts placed by pin-1 direction: (centre x, centre y, pin-1 side)
DIR_PARTS = {
    "R3": (52.7, 47.6, "D"),     # pin 1 (U3 CLK) down toward U3 pin 6, pin 2 (CLK10_IN) up
    "C8": (53.6, 51.6, "U"),     # pin 1 (+3V3_OSC) up, 1.9 mm from U3 pin 9
    "C9": (52.4, 54.2, "R"),     # left of C8, pin 1 3 mm from U3 pin 9 (H6 takes the space under U3)
    "R2": (60.4, 50.8, "U"),     # pin 2 (OSC_OE) down, level with U3 pin 1
}
SHOW_REF = {"J1", "J2", "U1", "U2", "U3", "U4", "U5", "U6", "TP1", "TP2", "TP3", "TP4", "TP5", "TP6", "TP7"}


def place_jacks_and_columns(g):
    """The 8 SMP jacks, the 4 filter columns with their copper, and the 4 PPS chains."""
    gnd = "GND"
    for k, xj in enumerate(XJ):
        g.place(f"J{3 + k}", xj, Y_REF, SMP_REF_ROT, show_ref=False)
        g.place(f"J{7 + k}", xj, Y_PPS, SMP_PPS_ROT, show_ref=False)
        # ---- filter column
        xl = xj + COL_DX
        parts = COLUMNS[k]
        for role, dy in SERIES_DY.items():
            g.place_dir(parts[role], xl, Y_REF + dy, "U")
        order = ["R0", "C_DC", "L1", "L2", "R_SER", "R_SH2"]
        for a, b in zip(order, order[1:]):
            n = g.padnet[(parts[a], "2")]
            g.track(n, [(parts[a], "2"), (parts[b], "1")], W_RF)
        for role, side, y1, up_down in SHUNTS:
            ref = parts[role]
            dx = ESD_DX if role == "ESD" else SH_DX
            # pin 1 on the stub row, pin 2 up (-1) or down (+1)
            g.place_dir(ref, xl + side * dx, Y_REF + y1, "D" if up_down < 0 else "U")
            half = abs(g.pad(ref, "1")[1] - (Y_REF + y1))
            fp = g.fps[ref]
            fp.Move(pcbnew.VECTOR2I(0, pcbnew.FromMM(up_down * half)))
            g.track(g.padnet[(ref, "1")], [(xl, Y_REF + y1), (ref, "1")], W_RF)
            gx, gy = g.pad(ref, "2")
            g.track(gnd, [(gx, gy), (gx + side * 0.9, gy)], 0.4)
            g.via(gnd, (gx + side * 0.9, gy))
        gx, gy = g.pad(parts["R_SH2"], "2")          # in-line shunt: via below pin 2
        g.track(gnd, [(gx, gy), (gx, gy + 0.9)], 0.4)
        g.via(gnd, (gx, gy + 0.9))
        tab = g.pad(parts["J"], "1")
        g.track(g.padnet[(parts["J"], "1")], [tab, (xl, Y_REF)], W_RF)
        # ---- PPS chain
        d, r, j = f"D{10 + k}", f"R{25 + k}", f"J{7 + k}"
        g.place_dir(d, xj + PPS_ESD_DX, Y_PPS + 0.7, "U")
        g.place_dir(r, xj + PPS_R_DX, Y_PPS, "R")
        node = g.padnet[(j, "1")]
        g.track(node, [g.pad(j, "1"), g.pad(r, "2")], W_RF)
        g.gv(d, "2", 0, 0.95)


def place_rest(g):
    """All other parts from PARTS and DIR_PARTS, and the mounting holes."""
    for ref, (x, y, rot) in PARTS.items():
        if ref in LDO_REFS:
            x, y = x + _LDO[0], y + _LDO[1]
        g.place(ref, x, y, rot, show_ref=ref in SHOW_REF)
    for ref, (x, y, d) in DIR_PARTS.items():
        g.place_dir(ref, x, y, d)
    for i, (x, y) in enumerate(G.MOUNT):
        g.place(f"H{i + 1}", x, y, 0, show_ref=False)


def route_tcxo(g):
    """SiT5155 (U3) supply and clock, by hand (SiT5155 layout guide p.35).

    +3V3_OSC: via -> C8 pin 1 -> U3 pin 9; C9 pin 1 -> U3 pin 9. OSC_OE: U3 pin 1
    -> R2 pin 2 -> TP5. CLK: U3 pin 6 -> R3 pin 1. No track of another net goes
    under U3 (the keep-out in u3_keepout stops the autorouter on In2 and B.Cu)."""
    osc = g.N("+3V3_OSC")
    c8 = g.pad("C8", "1")
    v = g.via(osc, (c8[0], c8[1] - 1.3))
    g.track(osc, [v, ("C8", "1"), ("U3", "9")], 0.4)
    c9, p9 = g.pad("C9", "1"), g.pad("U3", "9")
    g.track(osc, [c9, (p9[0], c9[1]), p9], 0.4)        # along under C8, then straight up to pin 9
    oe = g.N("OSC_OE")
    p1, r2 = g.pad("U3", "1"), g.pad("R2", "2")
    g.track(oe, [p1, (r2[0], p1[1]), ("R2", "2")], W_SIG)
    tp5 = g.pad("TP5", "1")
    g.track(oe, [r2, (tp5[0], r2[1]), tp5], W_SIG)
    p6, r3 = g.pad("U3", "6"), g.pad("R3", "1")
    g.track("Net-(U3-CLK)", [p6, (r3[0] + 0.9, p6[1]), r3], W_SIG)


LEG_KEEP = 2.0                 # half size of the no-track box round each SMP leg pad


def jack_leg_keepouts(g):
    """No tracks or vias in a small box round each SMP leg pad (all layers but the
    In1 plane), so the GND pour always reaches the pad with full thermal spokes.
    Why: the autorouter can pass close to a leg and starve its thermal relief
    (the rev C D-039 items). The jack tab track runs between the boxes."""
    for k in range(4):
        for ref in (f"J{3 + k}", f"J{7 + k}"):
            for p in g.fps[ref].Pads():
                if p.GetNumber() != "2":
                    continue
                x, y = g.pad_xy(p)
                g.rule_area((x - LEG_KEEP, y - LEG_KEEP, x + LEG_KEEP, y + LEG_KEEP), (F, IN2, B),
                            tracks=True, vias=True, pads=False, fills=False, footprints=False)


def u3_keepout(g, margin=0.3):
    """No tracks of other nets under U3 on In2.Cu and B.Cu (vias stay allowed)."""
    x0, y0, x1, y1 = g.courtyard("U3")
    g.rule_area((x0 - margin, y0 - margin, x1 + margin, y1 + margin), (IN2, B),
                tracks=True, vias=False, pads=False, fills=False, footprints=False, name="U3_no_tracks")


def outline_and_keepouts(g):
    """Board outline with the USB holes (the last one as a notch) and the keep-out rings."""
    r = G.USB_R
    notch = [k for k in range(4) if G.is_notch(k)]
    pts = [(0, 0), (G.BW, 0)]
    for k in notch:
        x0, y0, x1, y1 = G.usb_hole(k)
        pts += [(G.BW, y0), (x0, y0, True), (x0, y1, True), (G.BW, y1)]
    pts += [(G.BW, G.BH), (0, G.BH)]
    g.edge_path(pts, r)
    for k in range(4):
        if k in notch:
            continue
        x0, y0, x1, y1 = G.usb_hole(k)
        g.edge_path([(x0, y0, True), (x1, y0, True), (x1, y1, True), (x0, y1, True)], r)
    for k in range(4):
        g.rule_area(G.usb_keepout(k), (F, pcbnew.In1_Cu, IN2, B), name=f"USB{k + 1}_keepout")


def labels(g):
    """Front silkscreen texts that name the jacks and the inputs."""
    for k, x in enumerate(XJ):
        g.text(G.NAMES[k], x, 1.6, 0.9)
        g.text("PPS", x, 4.5, 0.8)
        g.text("10M", x, 52.3, 0.8)
    g.text("+5V IN", 27.4, 2.3, 0.9)
    g.text("5.5V MAX", 27.4, 3.8, 0.9)
    g.text("PPS IN", 94.0, 28.0, 0.8)
    g.text("R23: 50 ohm PPS load", 83.4, 14.4, 0.8)
    g.text("DNP - fit if needed", 83.4, 15.8, 0.8)


# ---------------------------------------------------------------- silkscreen art
# Logo outlines from the rev C board (extract_logo.py -> logo_polys.json).
# (centre x, centre y, size mm). The front logo sits in the free strip under
# J5; the back logo under the LDO block, where the back has no through-hole pads.
FRONT_LOGO = (85.4, 55.2, 8.37)
BACK_LOGO = (27.5, 50.5, 16.0)
FRONT_TEXT = [("HERON Clock Distribution Board", (40.5, 56.6), 0.8),     # bottom strip (H5 takes the top middle)
              ("10 MHz + PPS to 4 SDRs - Rev D", (40.5, 58.1), 0.8)]
BACK_TEXT = [("HERON Clock Distribution Board", (78.0, 52.2), 1.1),
             ("10 MHz + PPS to 4 SDRs", (78.0, 53.9), 0.9),
             ("Rev D - 10/2026", (78.0, 55.4), 0.9),
             ("Designed by Nathan Cohen for CU Boulder", (78.0, 56.9), 0.9),
             ("U3 SiT5155: water-soluble flux only", (78.0, 58.4), 0.9)]
LABELS = {"J1": (27.4, 5.5), "U3": (59.4, 47.0), "U6": (54.8, 13.0)}      # labels placed by hand (silk_refs.py keeps J1, J2)


def logo_shapes(board):
    """Add the front and back logos to the board. Return the new shapes.

    Why a separate function: update_logo.py calls it to change only the
    logo on the board as it is, without a new route (see the README)."""
    import json
    from pcbgen import P
    with open(os.path.join(HERE, "logo_polys.json")) as fh:
        data = json.load(fh)
    items = []
    for side, (cx, cy, size), layer in (("front", FRONT_LOGO, pcbnew.F_SilkS), ("back", BACK_LOGO, pcbnew.B_SilkS)):
        k = size / data[side]["size"]
        for pts in data[side]["polys"]:
            sp = pcbnew.SHAPE_POLY_SET(); sp.NewOutline()
            for x, y in pts:
                sp.Append(P(cx + k * x, cy + k * y))
            s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_POLY)
            s.SetPolyShape(sp); s.SetFilled(True); s.SetWidth(0); s.SetLayer(layer)
            board.Add(s); items.append(s)
    return items


def silk_art(g):
    """Logos, board name and title lines, in one group (HERON_SILK_ART)."""
    items = logo_shapes(g.board)
    for text, (x, y), h in FRONT_TEXT:
        items.append(g.text(text, x, y, h))
    for text, (x, y), h in BACK_TEXT:
        items.append(g.text(text, x, y, h, pcbnew.B_SilkS, mirrored=True))
    grp = pcbnew.PCB_GROUP(g.board); grp.SetName("HERON_SILK_ART"); g.board.Add(grp)
    for it in items:
        grp.AddItem(it)
    for ref, (x, y) in LABELS.items():
        g.fps[ref].Reference().SetPosition(P(x, y))
        g.fps[ref].Reference().SetTextAngleDegrees(0)


def setup(g):
    b = g.board
    b.SetCopperLayerCount(4)
    ds = b.GetDesignSettings()
    ds.SetBoardThickness(pcbnew.FromMM(1.6))
    ds.SetAuxOrigin(pcbnew.VECTOR2I(pcbnew.FromMM(100.0), pcbnew.FromMM(100.0 + G.BH)))   # drill/place origin: bottom-left corner


def build():
    g = Gen(PRJ, FPLIB, G.BW, G.BH)
    setup(g)
    outline_and_keepouts(g)
    place_jacks_and_columns(g)
    place_rest(g)
    route_tcxo(g)
    u3_keepout(g)
    jack_leg_keepouts(g)
    added, failed = g.gnd_pad_vias()
    print("gnd pad vias:", added, "failed:", failed)
    labels(g)
    silk_art(g)
    from silk_refs import place_labels
    hidden = place_labels(g.board)
    if hidden:
        print("no clear spot, label hidden:", " ".join(hidden))
    pcbnew.SaveBoard(PCB, g.board)
    return g


def finish():
    """Pours, stitching vias, then fill and DRC with kicad-cli (after routing).

    Why kicad-cli for the fill: pcbnew.ZONE_FILLER crashes the KiCad 10 Python
    on macOS (2026-10-20). kicad-cli fills the zones and saves the board."""
    b = pcbnew.LoadBoard(PCB)
    g = Gen(PRJ, FPLIB, G.BW, G.BH, board=b)
    for layer in (F, pcbnew.In1_Cu, IN2, B):
        g.zone(layer, "GND")
    n = g.stitch()
    print("stitch vias:", n)
    pcbnew.SaveBoard(PCB, b)
    rpt = os.path.join(PRJ, "drc.rpt")
    subprocess.run([KICAD_CLI, "pcb", "drc", "--schematic-parity", "--severity-all", "--units", "mm",
                    "--refill-zones", "--save-board", "-o", rpt, PCB],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(re.findall(r"\*\* Found \d+ [^*]+", open(rpt).read()))


if __name__ == "__main__":
    build()
    if "--no-route" not in sys.argv:
        print("autoroute (segments, vias):", autoroute(PCB, FREEROUTING, passes=100))
    finish()
