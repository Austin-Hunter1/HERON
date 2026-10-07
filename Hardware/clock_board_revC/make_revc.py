"""Make the rev C clock board from the rev B KiCad files (D-028).

Rev C has the rev B circuit, board outline, jack positions and routing
concept. The only change is easier assembly: the smallest parts get one
size larger.

- All 0402 resistors and capacitors become 0603.
- The nine TPD1E05U06 ESD diodes change from the X1SON (DPY, 0.6 x 1 mm)
  package to the SOD-523 (DYA, 1.6 x 0.8 mm) package of the same TI part.
  The pinout is the same: pin 1 = I/O, pin 2 = GND (TI datasheet Table 4-1).

Why a script and not the generators: the rev B KiCad files were edited after
the last generator run (the SMP swap), and the full generator flow needs
KiCad 7, Xvfb and Freerouting. This script reads the rev B files and writes
the rev C files, so the change is repeatable and easy to review.

Run it with the Python that comes with KiCad 10 (it needs `pcbnew`):

    <KiCad 10>/bin/python.exe make_revc.py

Inputs : ../clock_board_revB/project/heron_clock.kicad_sch, .kicad_pcb
Outputs: project/heron_clock.kicad_sch, project/heron_clock.kicad_pcb
"""

import os
import re
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "clock_board_revB", "project")
DST = os.path.join(HERE, "project")
FPLIB = os.environ.get(
    "KICAD10_FOOTPRINT_DIR",
    os.path.join(os.path.dirname(sys.executable), "..", "share", "kicad", "footprints"))

# Old footprint -> new footprint. Why one table: a later size decision is one edit.
FP_MAP = {
    "Resistor_SMD:R_0402_1005Metric": "Resistor_SMD:R_0603_1608Metric",
    "Capacitor_SMD:C_0402_1005Metric": "Capacitor_SMD:C_0603_1608Metric",
    "Package_SON:Texas_DPY0002A_0.6x1mm_P0.65mm": "Diode_SMD:D_SOD-523",
}

# Old MPN -> new MPN for the parts that change package. Stock and Digi-Key
# numbers are in bom_sources.csv.
MPN_MAP = {
    "CC0402KRX7R7BB104": "CC0603KRX7R7BB104",      # 100 nF 16 V X7R
    "GRM1555C1H271JA01D": "GRM1885C1H271JA01D",    # 270 pF 50 V C0G
    "GRM1555C1H471JA01D": "GRM1885C1H471JA01D",    # 470 pF 50 V C0G
    "RC0402JR-070RL": "RC0603JR-070RL",            # 0 R jumper
    "RC0402FR-0717R4L": "RC0603FR-0717R4L",
    "RC0402FR-0722RL": "RMCF0603FT22R0",           # 22 R: Stackpole (YAGEO RC0603FR-0722RL out of stock, 2026-10-07)
    "RC0402FR-0749R9L": "RC0603FR-0749R9L",
    "RC0402FR-07100RL": "RC0603FR-07100RL",
    "ERJ-2RKF2940X": "ERJ-3EKF2940V",              # 294 R, Panasonic 0603
    "RC0402FR-071KL": "RC0603FR-071KL",
    "RC0402FR-0710KL": "RC0603FR-0710KL",
    "TPD1E05U06DPYR": "TPD1E05U06DYAR",            # same TI part, SOD-523 package
}
# New MPN -> manufacturer, for the lines where the manufacturer changes.
MFR_MAP = {
    "RMCF0603FT22R0": "Stackpole Electronics Inc",
}


# ------------------------------------------------------------------ schematic
def convert_schematic(src, dst):
    """Change the footprint and MPN properties of the affected symbols.

    Why plain text edits: the schematic is a KiCad 7 file made by design.py.
    A text edit keeps every other byte the same, so the diff shows only the
    package change. The lib_symbols section has no footprint values, so a
    global replace of the quoted names is safe.
    """
    text = open(src, encoding="utf-8").read()
    n_fp = n_mpn = 0
    for old, new in FP_MAP.items():
        n_fp += text.count(f'"{old}"')
        text = text.replace(f'"{old}"', f'"{new}"')
    for old, new in MPN_MAP.items():
        n_mpn += text.count(f'"{old}"')
        text = text.replace(f'"{old}"', f'"{new}"')
    # The Manufacturer property comes just before the MPN property in each symbol.
    for mpn, mfr in MFR_MAP.items():
        text, n = re.subn(r'(\(property "Manufacturer" ")[^"]*("(?:(?!\(property).)*?\(property "MPN" "'
                          + re.escape(mpn) + '")', r'\g<1>' + mfr + r'\g<2>', text, flags=re.S)
        print(f"schematic: manufacturer of {mpn} set on {n} symbols")
    text = re.sub(r'\(rev "B"\)', '(rev "C")', text)
    open(dst, "w", encoding="utf-8", newline="\n").write(text)
    print(f"schematic: {n_fp} footprint and {n_mpn} MPN properties changed")


# ------------------------------------------------------------------ PCB swap
def load_fp(lib_id):
    """Load a stock KiCad 10 footprint by 'Lib:Name'."""
    lib, name = lib_id.split(":")
    fp = pcbnew.FootprintLoad(os.path.join(FPLIB, f"{lib}.pretty"), name)
    if fp is None:
        sys.exit(f"footprint {lib_id} not found in {FPLIB}")
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    return fp


def swap_footprint(board, old, new_id):
    """Put a new footprint in the place of `old` and keep its identity.

    The new footprint keeps the position, rotation, side, schematic link
    (path, sheet), attributes (DNP, BOM), pad nets, field values and field
    visibility of the old one. The reference and value texts keep the
    default offset of the new footprint, because the body is larger.
    """
    new = load_fp(new_id)
    board.Add(new)
    new.SetPosition(old.GetPosition())
    new.SetOrientation(old.GetOrientation())
    if old.GetLayer() != new.GetLayer():
        new.Flip(new.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
    new.SetPath(old.GetPath())
    new.SetSheetname(old.GetSheetname())
    new.SetSheetfile(old.GetSheetfile())
    new.SetAttributes(old.GetAttributes())
    new.SetDNP(old.IsDNP())
    new.SetReference(old.GetReference())
    new.SetValue(old.GetValue())
    for o, n in ((old.Reference(), new.Reference()), (old.Value(), new.Value())):
        n.SetVisible(o.IsVisible())
        n.SetLayer(o.GetLayer())
    user = {}
    for field in old.GetFields():
        name = field.GetName()
        if name in ("Reference", "Value", "Footprint", "Datasheet", "Description"):
            continue
        user[name] = MPN_MAP.get(field.GetText(), field.GetText()) if name == "MPN" else field.GetText()
        new.SetField(name, user[name])
    if user.get("MPN") in MFR_MAP:
        user["Manufacturer"] = MFR_MAP[user["MPN"]]
        new.SetField("Manufacturer", user["Manufacturer"])
    # The user fields (MPN, tolerance, ...) are data for the BOM only: hide them.
    for field in new.GetFields():
        if field.GetName() in user:
            field.SetVisible(False)
            field.SetLayer(pcbnew.F_Fab)
            field.SetPosition(new.GetPosition())
    nets = {p.GetNumber(): p.GetNet() for p in old.Pads()}
    for p in new.Pads():
        p.SetNet(nets[p.GetNumber()])
    board.Remove(old)
    return new


def swap_all(board):
    """Swap every footprint named in FP_MAP. Return the new footprints by reference."""
    swapped = {}
    for fp in list(board.GetFootprints()):
        lib_id = f"{fp.GetFPID().GetLibNickname()}:{fp.GetFPID().GetLibItemName()}"
        if lib_id in FP_MAP:
            swapped[fp.GetReference()] = swap_footprint(board, fp, FP_MAP[lib_id])
    print(f"PCB: {len(swapped)} footprints swapped")
    return swapped


# ------------------------------------------------------------------ helpers
OX, OY = 100.0, 100.0          # sheet offset of the board corner (the same as rev B)
W_RF, W_GND = 0.35, 0.4        # 10 MHz track and GND stub widths (the same as rev B)
VIA_D, VIA_H = 0.6, 0.3


def P(x, y):
    """Board-local mm (0, 0 = top-left corner) -> KiCad internal units."""
    return pcbnew.VECTOR2I(pcbnew.FromMM(OX + x), pcbnew.FromMM(OY + y))


def L(v):
    """KiCad position -> board-local mm tuple."""
    return (pcbnew.ToMM(v.x) - OX, pcbnew.ToMM(v.y) - OY)


def track(board, a, b, net, width, layer=pcbnew.F_Cu):
    """Add one straight track from a to b (board-local mm)."""
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(P(*a)); t.SetEnd(P(*b)); t.SetWidth(pcbnew.FromMM(width))
    t.SetLayer(layer); t.SetNet(net)
    board.Add(t)


def via(board, at, net):
    """Add one through via (the rev B size)."""
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(P(*at)); v.SetWidth(pcbnew.FromMM(VIA_D)); v.SetDrill(pcbnew.FromMM(VIA_H))
    v.SetNet(net)
    board.Add(v)


def path(board, pts, net, width):
    """Add a chain of tracks through the points."""
    for a, b in zip(pts, pts[1:]):
        track(board, a, b, net, width)


def pad(fp, num):
    """One pad of a footprint by number. (FindPadByNumber gives a raw SWIG
    pointer in KiCad 10, so search the pad list.)"""
    return next(p for p in fp.Pads() if p.GetNumber() == num)


def pad_xy(fp, num):
    """Board-local position of one pad."""
    return L(pad(fp, num).GetPosition())


def place(fp, x, y, rot):
    fp.SetPosition(P(x, y))
    fp.SetOrientationDegrees(rot)


def in_box(pt, box):
    return box[0] <= pt[0] <= box[2] and box[1] <= pt[1] <= box[3]


# ------------------------------------------------------------------ filter columns
# Rev B put the shunt parts of each 10 MHz filter across the signal line. With
# 0603 parts that stack is about 7 mm too long for the space between the PPS
# jack row and the 10 MHz jack tab. Rev C stands the shunt parts upright beside
# the line, so only the series parts set the column length.
#
# Column k: line x = COL_X0 + 30 k. All series parts have pin 1 at the top
# (rotation -90).
COL_X0 = 19.16                 # rev B line x of column 0 (6 mm right of the jack axis)
COL_PITCH = 30.0               # SDR pitch
Y_TAB = 30.78                  # 10 MHz jack tab row (rev B, set by the rack)
SERIES_DY = {                  # centre y relative to the node at the jack tab
    "R0": -15.03,              # 0 R          (0603)
    "C_DC": -11.93,            # 100 nF block (0603)
    "L1": -8.56,               # 820 nH       (0805)
    "L2": -4.92,               # 820 nH       (0805)
    "R_SER": -1.55,            # 17.4 R pad   (0603)
    "R_SH2": 1.55,             # 294 R pad    (0603), in line, pin 2 to GND
}
# The pitch is half courtyard + half courtyard + 0.05 mm (0603 1.525, 0805
# 1.795). The tab node (Y_TAB) sits in the gap between R_SER pin 2 and R_SH2 pin 1.
SH_DX = 1.72                   # shunt axis offset: 0805 half courtyard + 0603 half + 0.05
ESD_DX = 1.57                  # ESD axis offset: 0603 half courtyard + SOD-523 half + 0.05
# Upright shunts: (role, side -1 left / +1 right, y of the pin-1 stub relative
# to Y_TAB, direction of pin 2: -1 up / +1 down). The stub starts on the line,
# inside the pad or the track of the same node.
SHUNTS = [
    ("C_SH1", -1, -10.37, -1),  # 270 pF at node A (C_DC to L1), up beside C_DC
    ("C_SH2", -1, -6.74, +1),   # 470 pF at node B (L1 to L2), down beside L2
    ("C_SH3", +1, -3.78, -1),   # 270 pF at node C, up beside L2 (stub from L2 pin 2)
    ("R_SH1", +1, -2.28, +1),   # 294 R at node C, down beside R_SER (stub from R_SER pin 1)
    ("ESD", +1, 0.92, +1),      # ESD diode at node D, down beside R_SH2 (stub from R_SH2 pin 1)
]
# Parts of each column by role (rev B reference designators).
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
REVB_REF_END_Y = 14.8          # rev B end point of the REF track on the 0 R pin 1


def clear_column(board, xl, inner_nets):
    """Remove the rev B copper of one column: its own nets, and the GND vias
    and GND tracks in the column area. place_column draws the new copper."""
    box = (xl - 2.6, 13.9, xl + 2.8, 34.6)
    for t in list(board.GetTracks()):
        net = t.GetNetname()
        if t.Type() == pcbnew.PCB_VIA_T:
            if net == "GND" and in_box(L(t.GetPosition()), box):
                board.Delete(t)
            continue
        ends = (L(t.GetStart()), L(t.GetEnd()))
        if net in inner_nets or (net == "GND" and any(in_box(e, box) for e in ends)):
            board.Delete(t)


def place_column(board, k):
    """Place and route one 10 MHz filter column (see SERIES_DY and SHUNTS)."""
    xl = COL_X0 + COL_PITCH * k
    parts = {role: board.FindFootprintByReference(ref) for role, ref in COLUMNS[k].items()}
    gnd = board.FindNet("GND")
    inner = {p.GetNetname() for role in ("C_DC", "L1", "L2", "R_SER") for p in parts[role].Pads()}
    clear_column(board, xl, inner)
    for role, dy in SERIES_DY.items():
        place(parts[role], xl, Y_TAB + dy, -90)
    order = ["R0", "C_DC", "L1", "L2", "R_SER", "R_SH2"]
    for a, b in zip(order, order[1:]):     # the signal line, pad to pad
        net = pad(parts[a], "2").GetNet()
        track(board, pad_xy(parts[a], "2"), pad_xy(parts[b], "1"), net, W_RF)
    for role, side, y1, up_down in SHUNTS:
        fp = parts[role]
        dx = ESD_DX if role == "ESD" else SH_DX
        half = abs(pcbnew.ToMM(pad(fp, "1").GetFPRelativePosition().x))
        place(fp, xl + side * dx, Y_TAB + y1 + up_down * half, 90 if up_down < 0 else -90)
        track(board, (xl, Y_TAB + y1), pad_xy(fp, "1"), pad(fp, "1").GetNet(), W_RF)
        # GND via beside pin 2, on the outer side. C_SH1: above pin 2, because
        # the B.Cu PPS1_Y track runs at y 19.28 left of column 0.
        gx, gy = pad_xy(fp, "2")
        if role == "C_SH1":
            v = (gx, gy - 0.95)
        elif role == "ESD":
            v = (gx + side * 0.88, gy)
        else:
            v = (gx + side * 0.9, gy)
        via(board, v, gnd); track(board, (gx, gy), v, gnd, W_GND)
    gx, gy = pad_xy(parts["R_SH2"], "2")      # in-line shunt: via below pin 2
    via(board, (gx, gy + 0.9), gnd); track(board, (gx, gy), (gx, gy + 0.9), gnd, W_GND)
    # Jack tab -> node D (the rev B track, drawn again).
    tab = pad(parts["J"], "1")
    track(board, (pad_xy(parts["J"], "1")[0], Y_TAB), (xl, Y_TAB), tab.GetNet(), W_RF)
    # REF input: the rev B track ends on the old pin-1 point. Move that end.
    ref_net = pad(parts["R0"], "1").GetNetname()
    new_end = P(*pad_xy(parts["R0"], "1"))
    for t in board.GetTracks():
        if t.Type() != pcbnew.PCB_VIA_T and t.GetNetname() == ref_net:
            for get, put in ((t.GetStart, t.SetStart), (t.GetEnd, t.SetEnd)):
                x, y = L(get())
                if abs(x - xl) < 0.01 and abs(y - REVB_REF_END_Y) < 0.01:
                    put(new_end)


# ------------------------------------------------------------------ edit helpers
def find_tracks(board, net, a=None, b=None, layer=None):
    """Tracks of one net. With a (and b): only the tracks with an end at a
    (and the other end at b). Points are board-local mm, matched to 0.01 mm."""
    def at(v, p):
        x, y = L(v)
        return abs(x - p[0]) < 0.011 and abs(y - p[1]) < 0.011
    out = []
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T or t.GetNetname() != net:
            continue
        if layer is not None and t.GetLayer() != layer:
            continue
        s, e = t.GetStart(), t.GetEnd()
        if a is not None and not (at(s, a) or at(e, a)):
            continue
        if b is not None and not ((at(s, a) and at(e, b)) or (at(s, b) and at(e, a))):
            continue
        out.append(t)
    return out


def delete_tracks(board, net, segs, layer=None):
    """Delete rev B tracks given as (a, b) end pairs. Stop when one is missing,
    because then the rev B file is not the expected one."""
    for a, b in segs:
        found = find_tracks(board, net, a, b, layer)
        if not found:
            sys.exit(f"{net}: rev B track {a} - {b} not found")
        for t in found:
            board.Delete(t)


def find_via(board, net, at):
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T and t.GetNetname() == net:
            x, y = L(t.GetPosition())
            if abs(x - at[0]) < 0.011 and abs(y - at[1]) < 0.011:
                return t
    sys.exit(f"{net}: rev B via at {at} not found")


def move_via(board, net, old, new):
    """Move a via and drag the ends of the same-net tracks that end on it."""
    find_via(board, net, old).SetPosition(P(*new))
    for t in find_tracks(board, net, old):
        for get, put in ((t.GetStart, t.SetStart), (t.GetEnd, t.SetEnd)):
            x, y = L(get())
            if abs(x - old[0]) < 0.011 and abs(y - old[1]) < 0.011:
                put(P(*new))


def set_end(board, net, old, new):
    """Move one track end point from old to new (both ends that sit at old)."""
    ts = find_tracks(board, net, old)
    if not ts:
        sys.exit(f"{net}: no track ends at {old}")
    for t in ts:
        for get, put in ((t.GetStart, t.SetStart), (t.GetEnd, t.SetEnd)):
            x, y = L(get())
            if abs(x - old[0]) < 0.011 and abs(y - old[1]) < 0.011:
                put(P(*new))


def nudge(board, ref, dx, dy):
    """Move a footprint a small distance. Track ends that sit on its pads move
    with it, so the connections stay (the last segment turns a little)."""
    fp = board.FindFootprintByReference(ref)
    ends = []
    for p in fp.Pads():
        for t in board.GetTracks():
            if t.Type() == pcbnew.PCB_VIA_T or t.GetNetCode() != p.GetNetCode() or not p.IsOnLayer(t.GetLayer()):
                continue
            for get, put in ((t.GetStart, t.SetStart), (t.GetEnd, t.SetEnd)):
                if p.HitTest(get()):
                    ends.append((get, put))
    fp.Move(pcbnew.VECTOR2I(pcbnew.FromMM(dx), pcbnew.FromMM(dy)))
    for get, put in ends:
        v = get()
        put(pcbnew.VECTOR2I(v.x + pcbnew.FromMM(dx), v.y + pcbnew.FromMM(dy)))


# ------------------------------------------------------------------ PPS outputs
# Each PPS output is: U6 -> 22 R -> node (ESD pin 1, jack dog-leg). Rev B had
# the 0402 resistor upright under the ESD diode. The 0603 resistor and the
# SOD-523 diode do not fit there. Rev C keeps the ESD diode on the rev B line
# (pin 1 on the line, so the dog-leg stays) and lays the resistor flat.
PPS_X0 = 13.16                 # rev B PPS line x of column 0 (the jack axis)
PPS_ESD_Y = 15.56              # ESD diode row (rev B)
PPS_R_Y = 17.53                # flat resistor row (rev B resistor pin-1 y)


def pps_outputs(board):
    gnd = board.FindNet("GND")
    for k, (d, r) in enumerate((("D10", "R25"), ("D11", "R26"), ("D12", "R27"), ("D13", "R28"))):
        xp = PPS_X0 + COL_PITCH * k
        node = f"Net-({d}-A1)"
        esd = board.FindFootprintByReference(d)
        res = board.FindFootprintByReference(r)
        half = abs(pcbnew.ToMM(pad(esd, "1").GetFPRelativePosition().x))
        place(esd, xp + half, PPS_ESD_Y, 0)            # pin 1 on the line, GND to the right
        # ESD GND via: out of the larger pad, 0.89 mm right of pin 2.
        old_v = (xp + 1.5, PPS_ESD_Y)
        delete_tracks(board, "GND", [((xp + 0.7, PPS_ESD_Y), old_v)], pcbnew.F_Cu)
        g2 = pad_xy(esd, "2")
        new_v = (g2[0] + 0.89, PPS_ESD_Y)
        move_via(board, "GND", old_v, new_v)
        track(board, g2, new_v, gnd, W_GND)
        rh = abs(pcbnew.ToMM(pad(res, "1").GetFPRelativePosition().x))
        if k == 2:
            # Column 3: PPS4_Y and REF3_Y pass just under this spot, so the
            # resistor goes left of the ESD diode, in the ESD row. Pin 2
            # meets ESD pin 1; the via to the B.Cu dog-leg moves down.
            place(res, xp - 2.16, PPS_ESD_Y, 0)
            delete_tracks(board, node, [((72.26, 15.56), (73.16, 15.56)), ((73.16, 15.56), (73.16, 16.5))], pcbnew.F_Cu)
            delete_tracks(board, node, [((72.26, 16.44), (72.26, 15.56))], pcbnew.B_Cu)
            vx = 72.5
            move_via(board, node, (72.26, 15.56), (vx, 16.44))
            set_end(board, node, (72.26, 16.44), (vx, 16.44))
            n = pad(res, "2").GetNet()
            track(board, pad_xy(res, "2"), (xp, PPS_ESD_Y), n, W_RF)
            track(board, (vx, PPS_ESD_Y), (vx, 16.44), n, W_RF)
            delete_tracks(board, "/PPS3_Y", [((71.74, 16.1), (73.16, 17.53)), ((68.22, 16.1), (71.74, 16.1))])
            p1 = pad_xy(res, "1")
            path(board, [(68.22, 16.1), (69.0, 16.1), (69.0 + 16.1 - PPS_ESD_Y, PPS_ESD_Y), p1],
                 pad(res, "1").GetNet(), 0.25)
            continue
        # Columns 1, 2, 4: flat resistor in the rev B pin-1 row. Pin 2 on the line.
        rot = 180 if k in (0, 1) else 0                # pin 1 toward the U6 track
        place(res, xp + (rh if rot == 180 else -rh), PPS_R_Y, rot)
        n = pad(res, "2").GetNet()
        delete_tracks(board, node, [((xp, PPS_ESD_Y), (xp, 16.5))])
        track(board, (xp, PPS_ESD_Y), (xp, PPS_R_Y), n, W_RF)
        p1 = pad_xy(res, "1")
        if k == 0:
            # PPS1_Y: pin 1 -> new via right of the pad -> B.Cu down to the rev B trunk.
            delete_tracks(board, "/PPS1_Y", [((13.16, 17.53), (13.28, 17.65)), ((13.28, 17.65), (14.08, 17.65))])
            delete_tracks(board, "/PPS1_Y", [((14.08, 17.65), (15.71, 19.28))], pcbnew.B_Cu)
            move_via(board, "/PPS1_Y", (14.08, 17.65), (15.71, PPS_R_Y))
            pn = pad(res, "1").GetNet()
            track(board, p1, (15.71, PPS_R_Y), pn, 0.25)
            track(board, (15.71, PPS_R_Y), (15.71, 19.28), pn, 0.25, pcbnew.B_Cu)
        elif k == 1:
            # PPS2_Y: pin 1 -> down -> under L3 (with REF1_Y) -> rev B via to In2.
            delete_tracks(board, "/PPS2_Y", [((43.16, 17.53), (46.26, 20.62)), ((46.26, 20.62), (49.84, 20.62)),
                                             ((49.84, 20.62), (50.24, 20.22))])
            yc = Y_TAB + SERIES_DY["L1"] - 0.3
            path(board, [p1, (p1[0] + 0.49, p1[1] + 0.49), (p1[0] + 0.49, yc - 1.3), (p1[0] + 1.79, yc),
                         (49.9, yc), (50.24, yc - 0.34), (50.24, 20.22)], pad(res, "1").GetNet(), 0.25)
        else:
            # PPS4_Y: the long rev B track ends at the old pin 1; shorten it.
            set_end(board, "/PPS4_Y", (103.16, 17.53), p1)


# ------------------------------------------------------------------ crossings
def reroute_crossings(board):
    """Re-route the tracks that pass under the first inductor (L1 role) of a
    filter column. In rev B they ran under the rev B inductor position; that
    inductor now sits at Y_TAB + SERIES_DY["L1"] = yc. Two tracks share each
    gap: one at yc - 0.3, one at yc + 0.3 (0.25 mm tracks, 0.2 mm to the pads)."""
    yc = Y_TAB + SERIES_DY["L1"]
    up, dn = yc - 0.3, yc + 0.3
    jobs = [   # net, rev B segments to delete, new path
        ("/REF1_Y", [((42.51, 21.03), (51.32, 21.03))],
         [(42.51, 21.03), (42.51 + dn - 21.03, dn), (50.6, dn), (51.32, dn - 0.72), (51.32, 21.03)]),
        ("/REF4_Y", [((68.85, 20.6), (84.33, 20.6))],
         [(68.85, 20.6), (74.5, 20.6), (74.5 + up - 20.6, up), (82.2, up), (82.2 + up - 20.6, 20.6), (84.33, 20.6)]),
        ("/PPS_BUF", [((69.21, 25.15), (66.91, 22.85)), ((86.71, 25.15), (69.21, 25.15))],
         [(66.91, 23.07), (68.5, 23.07), (68.5 + 23.07 - dn, dn), (82.0, dn), (82.0 + 25.15 - dn, 25.15), (86.71, 25.15)]),
    ]
    for net, old, pts in jobs:
        width, n = 0.25, board.FindNet(net)
        delete_tracks(board, net, old)
        path(board, pts, n, width)


# ------------------------------------------------------------------ small moves
def local_fixes(board):
    """Small changes where a larger part touches a neighbour (found by DRC)."""
    gnd = board.FindNet("GND")
    nudge(board, "C2", 0, 0.3)          # courtyard on FB1
    nudge(board, "R2", 0, 0.37)         # courtyard on TP5; its +3V3_OSC via moves too
    move_via(board, "/+3V3_OSC", (59.46, 32.07), (59.46, 32.44))
    nudge(board, "D3", -0.5, 0)         # courtyard on R1
    nudge(board, "C28", 0, -0.3)        # pad on a GND via

    # R3 (22 R, TCXO -> buffer): U4 and U3 leave 2.76 mm, too little for an
    # upright 0603. Lay it flat: pin 1 straight above U3 pin 6, pin 2 up to the
    # CLK10_IN track at y 20.87. TP4 joins that track from the left.
    r3 = board.FindFootprintByReference("R3")
    for net in ("/CLK10_IN", "Net-(U3-CLK)"):
        for t in find_tracks(board, net):
            x0, y0 = L(t.GetStart()); x1, y1 = L(t.GetEnd())
            if 52.0 < min(x0, x1) and max(x0, x1) < 56.0 and 20.0 < min(y0, y1) and max(y0, y1) < 25.0:
                board.Delete(t)
    set_end(board, "/CLK10_IN", (55.09, 20.87), (54.06, 20.87))
    place(r3, 55.09 + 0.825, 22.14, 0)
    clk = board.FindNet("/CLK10_IN")
    track(board, (52.86, 22.07), (54.06, 20.87), clk, 0.25)
    track(board, pad_xy(r3, "2"), (pad_xy(r3, "2")[0], 20.87), clk, 0.25)
    track(board, pad_xy(r3, "1"), (55.09, 24.44), board.FindNet("Net-(U3-CLK)"), 0.25)

    # R4 (REF_EN pull-up): its pad now reaches REF4_Y above and its +3V3_CLK
    # via on the right. Move both down; move the B.Cu PPS_BUF trunk 0.34 mm
    # right to make room for the via.
    nudge(board, "R4", 0, 0.45)
    for a in ((62.86, 12.17), (62.86, 23.07)):
        set_end(board, "/PPS_BUF", a, (63.2, a[1]))
    # Via y window: 0.15 mm from REF4_Y (F.Cu, y 18.64) and PPS2_Y (In2, y 20.22).
    move_via(board, "/+3V3_CLK", (62.16, 19.29), (62.55, 19.4))

    # PPS input (J2 -> R23 term (DNP) / D8 ESD / R22 100 R / R21 10 k): spread
    # the four parts and redraw the node, because the SOD-523 pads do not
    # cover the rev B track ends.
    nudge(board, "R21", 0.55, 0)
    nudge(board, "R23", -0.65, 0)
    for t in find_tracks(board, "Net-(D8-A1)"):
        x0, y0 = L(t.GetStart()); x1, y1 = L(t.GetEnd())
        if max(y0, y1) < 33.0:            # keep the J2 centre-pin track
            board.Delete(t)
    for t in find_tracks(board, "GND", (87.51, 30.05), None, pcbnew.F_Cu):
        board.Delete(t)
    d8 = board.FindFootprintByReference("D8")
    place(d8, 86.79, 31.2, 90)
    node = board.FindNet("Net-(D8-A1)")
    r22p1, r23p1, d8p1 = pad_xy(board.FindFootprintByReference("R22"), "1"), \
        pad_xy(board.FindFootprintByReference("R23"), "1"), pad_xy(d8, "1")
    path(board, [(90.11, 32.81), (r22p1[0], 32.585), r22p1], node, 0.25)
    path(board, [(r22p1[0], 32.585), (r23p1[0], 32.585), r23p1], node, 0.25)
    track(board, (r23p1[0], d8p1[1] + 0.05), (d8p1[0], d8p1[1] + 0.05), node, 0.25)
    track(board, pad_xy(d8, "2"), (87.51, 30.05), gnd, W_GND)
    move_via(board, "GND", (86.2, 31.2), (85.6, 31.2))     # was under the D8 body
    move_via(board, "GND", (93.7, 31.2), (94.1, 31.2))     # was under the R21 body

    # D9 (PPS LED) GND via: the 0603 R24 pad now covers it. Put a new via
    # right of the LED, clear of the D9 anode track (left) and the In2
    # +3V3_CLK track. The rev B via and its stubs go (prune_dangling cleans up).
    for t in find_tracks(board, "GND", (86.91, 22.21)):
        board.Delete(t)
    board.Delete(find_via(board, "GND", (86.91, 22.21)))
    via(board, (88.1, 20.5), gnd)
    track(board, (86.91, 21.19), (88.1, 20.5), gnd, W_GND)

    # U4 reference text: it sat over the flat R3. Put it above U4.
    u4 = board.FindFootprintByReference("U4")
    u4.Reference().SetPosition(P(56.6, 16.4))


def drop_blocked_stitch_vias(board):
    """Delete GND stitching vias (no track on them) that a new rev C track now
    passes too close to. The GND planes do not need them there."""
    clr = pcbnew.FromMM(0.15)
    for v in list(board.GetTracks()):
        if v.Type() != pcbnew.PCB_VIA_T or v.GetNetname() != "GND":
            continue
        pos, r = v.GetPosition(), v.GetWidth(pcbnew.F_Cu) // 2
        if any(t.Type() != pcbnew.PCB_VIA_T and t.GetNetname() == "GND" and
               (t.GetStart() == pos or t.GetEnd() == pos) for t in board.GetTracks()):
            continue
        for t in board.GetTracks():
            if t.Type() == pcbnew.PCB_VIA_T or t.GetNetname() == "GND":
                continue
            if t.HitTest(pos, r + clr):
                print(f"stitching via at {tuple(round(c, 2) for c in L(pos))} removed (near {t.GetNetname()})")
                board.Delete(v)
                break


def _loose(t, segs, vias, pads):
    """True when one end of track t touches no via, pad or other track."""
    layer = t.GetLayer()
    for end in (t.GetStart(), t.GetEnd()):
        if any(v.GetPosition() == end for v in vias):
            continue
        if any(o is not t and o.GetLayer() == layer and end in (o.GetStart(), o.GetEnd()) for o in segs):
            continue
        if any(p.IsOnLayer(layer) and p.HitTest(end) for p in pads):
            continue
        return True
    return False


def _net_items(board, net):
    tracks = [t for t in board.GetTracks() if t.GetNetname() == net]
    return ([t for t in tracks if t.Type() != pcbnew.PCB_VIA_T],
            [t for t in tracks if t.Type() == pcbnew.PCB_VIA_T],
            [p for p in board.GetPads() if p.GetNetname() == net])


def loose_ids(board, net="GND"):
    """IDs of the tracks that are loose already (rev B has some GND stubs that
    end in the copper pour). prune_dangling keeps these."""
    segs, vias, pads = _net_items(board, net)
    return {t.m_Uuid.AsString() for t in segs if _loose(t, segs, vias, pads)}


def prune_dangling(board, keep, net="GND"):
    """Delete the tracks of one net that rev C left loose (an end touches no
    via, pad or other track), for example In2 GND stubs to a moved via.
    Tracks in `keep` were loose in rev B and stay. Delete one track at a
    time and look again, because a deletion can leave the next one loose."""
    while True:
        segs, vias, pads = _net_items(board, net)
        bad = next((t for t in segs if t.m_Uuid.AsString() not in keep and _loose(t, segs, vias, pads)), None)
        if bad is None:
            return
        print(f"loose {net} track removed: {tuple(round(c, 2) for c in L(bad.GetStart()))} - "
              f"{tuple(round(c, 2) for c in L(bad.GetEnd()))} on {board.GetLayerName(bad.GetLayer())}")
        board.Delete(bad)


def set_revision_text(board):
    """The board text names the revision. Change rev B to rev C."""
    for item in board.GetDrawings():
        if isinstance(item, pcbnew.PCB_TEXT) and "rev B" in item.GetText():
            item.SetText(item.GetText().replace("rev B", "rev C"))


def main():
    convert_schematic(os.path.join(SRC, "heron_clock.kicad_sch"), os.path.join(DST, "heron_clock.kicad_sch"))
    board = pcbnew.LoadBoard(os.path.join(SRC, "heron_clock.kicad_pcb"))
    keep = loose_ids(board)
    swap_all(board)
    for k in range(len(COLUMNS)):
        place_column(board, k)
    pps_outputs(board)
    reroute_crossings(board)
    local_fixes(board)
    drop_blocked_stitch_vias(board)
    prune_dangling(board, keep)
    set_revision_text(board)
    # Refill the pours: the old fill follows the old, smaller pads.
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(os.path.join(DST, "heron_clock.kicad_pcb"))


if __name__ == "__main__":
    main()
