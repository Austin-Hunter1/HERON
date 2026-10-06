"""Generate the HERON clock-distribution PCB, rev B (KiCad 7, 4 layers, 118 x 44 mm).

Rev B is the rev A circuit on a long strip that mounts on the rear plate of the
payload rack (see ../payload_rack). The component side (F) faces forward, toward
the SDRs. Each SDR has a pair of vertical SMA jacks (Amphenol 132134), coaxial with
that SDR's rear reference SMAs, 25 mm apart, joined by short RG316 jumpers.

Board-local coordinates: mm, (0, 0) = top-left corner seen from the component side.
Rack coordinates: world X = 46 + x, world Z = 54 - y, board face at world Y = 203.2.

Inputs : project/heron_clock.net, project/heron_clock.kicad_sch (symbol UUIDs)
Output : project/heron_clock.kicad_pcb (+ drc.rpt)
Routing: hand routes for the TCXO / buffer / LDO clusters (copied from rev A,
         rigidly moved), the 10 MHz filter columns and the PPS output chains;
         Freerouting (headless, via Xvfb) for the remaining non-GND nets.
         GND = In1 solid plane + pours + via at every SMD GND pad.
"""

import math, os, re, sys
import pcbnew
from sexp import parse, find, find1

PRJ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "project")
# Stock KiCad 7 footprint folder (set KICAD7_FOOTPRINT_DIR on macOS/Windows).
FPLIB = os.environ.get("KICAD7_FOOTPRINT_DIR", "/usr/share/kicad/footprints")
OX, OY = 100.0, 100.0          # sheet offset of the board corner
BW, BH = 118.0, 44.0           # board size (rev B)
XC = [13.16, 43.16, 73.16, 103.16]   # jack columns, 30 mm pitch = SDR blade pitch
Y_PPS, Y_REF = 10.84, 30.78           # PPS row / 10 MHz row (from the Ettus rear SMA positions)
COL_DX = 6.0                          # 10 MHz filter column, right of each jack
REF_X = XC
SMA_INSET = 0.3               # edge-launch pads end 0.3 mm inside the board edge
W_RF = 0.35                    # ~50 ohm microstrip over In1 GND (0.21 mm prepreg)
W_SIG = 0.25
W_PWR = 0.5
VIA_D, VIA_H = 0.6, 0.3

board = pcbnew.BOARD()
F, B, IN1, IN2 = pcbnew.F_Cu, pcbnew.B_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu


def P(x, y):
    """Board-local mm -> KiCad internal units."""
    return pcbnew.VECTOR2I(pcbnew.FromMM(OX + x), pcbnew.FromMM(OY + y))


def L(v):
    """KiCad position -> board-local mm tuple."""
    return (pcbnew.ToMM(v.x) - OX, pcbnew.ToMM(v.y) - OY)


# ------------------------------------------------------------------ netlist
netl = parse(open(f"{PRJ}/heron_clock.net").read())
comps = {}
for c in find(find1(netl, "components"), "comp"):
    comps[find1(c, "ref")[1]] = dict(value=find1(c, "value")[1], fp=find1(c, "footprint")[1],
                                     fields={f[1][1]: f[2] for f in find(find1(c, "fields") or [], "field") if len(f) > 2})
padnet = {}
for n in find(find1(netl, "nets"), "net"):
    for node in find(n, "node"):
        padnet[(find1(node, "ref")[1], find1(node, "pin")[1])] = find1(n, "name")[1]
# symbol UUIDs from the schematic
sch = parse(open(f"{PRJ}/heron_clock.kicad_sch").read())
symuuid, dnp = {}, set()
for s in find(sch, "symbol"):
    ref = [p[2] for p in find(s, "property") if p[1] == "Reference"][0]
    symuuid[ref] = find1(s, "uuid")[1]
    if find1(s, "dnp") and str(find1(s, "dnp")[1]) == "yes":
        dnp.add(ref)

nets = {}


def net(name):
    if name not in nets:
        ni = board.FindNet(name)
        if ni is None:
            ni = pcbnew.NETINFO_ITEM(board, name)
            board.Add(ni)
        nets[name] = ni
    return nets[name]


def N(short):
    """Short net name ('REF1_Y', 'GND', '+3V3_CLK') -> full netlist name."""
    for cand in (short, "/" + short):
        if cand in {v for v in padnet.values()}:
            return cand
    raise KeyError(short)


# ------------------------------------------------------------------ placement
fps = {}
SIDE = {}


def place(ref, x, y, rot=0, show_ref=True):
    """Load the footprint named in the netlist and put it at (x, y)."""
    lib, name = comps[ref]["fp"].split(":")
    path = f"{PRJ}/HERON_Clock.pretty" if lib == "HERON_Clock" else f"{FPLIB}/{lib}.pretty"
    fp = pcbnew.FootprintLoad(path, name)
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    fp.SetReference(ref)
    fp.SetValue(comps[ref]["value"])
    fp.SetPath(pcbnew.KIID_PATH("/" + symuuid[ref]))
    for k, v in comps[ref]["fields"].items():
        fp.SetProperty(k, v)
    board.Add(fp)
    fp.SetPosition(P(x, y))
    fp.SetOrientationDegrees(rot)
    for pad in fp.Pads():
        key = (ref, pad.GetNumber())
        if key in padnet:
            pad.SetNet(net(padnet[key]))
    fp.Reference().SetVisible(show_ref)
    fp.Reference().SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(0.8), pcbnew.FromMM(0.8)))
    fp.Reference().SetTextThickness(pcbnew.FromMM(0.12))
    if ref.startswith(("TP", "H", "FID")):
        fp.SetAttributes(fp.GetAttributes() | pcbnew.FP_EXCLUDE_FROM_BOM | pcbnew.FP_EXCLUDE_FROM_POS_FILES)
    if ref in dnp:
        fp.SetAttributes(fp.GetAttributes() | pcbnew.FP_EXCLUDE_FROM_BOM | pcbnew.FP_EXCLUDE_FROM_POS_FILES)
    fps[ref] = fp
    return fp


def pad(ref, num):
    """Board-local centre of a pad."""
    return L(fps[ref].FindPadByNumber(str(num)).GetPosition())


def courtyard(fp):
    """(xmin, ymin, xmax, ymax) of the front courtyard, board-local."""
    xs, ys = [], []
    for it in fp.GraphicalItems():
        if it.GetLayer() == pcbnew.F_CrtYd:
            bb = it.GetBoundingBox()
            xs += [pcbnew.ToMM(bb.GetX()) - OX, pcbnew.ToMM(bb.GetRight()) - OX]
            ys += [pcbnew.ToMM(bb.GetY()) - OY, pcbnew.ToMM(bb.GetBottom()) - OY]
    return min(xs), min(ys), max(xs), max(ys)


def pad_extent(fp):
    """(xmin, ymin, xmax, ymax) of all copper pads of a footprint, board-local."""
    xs, ys = [], []
    for p in fp.Pads():
        bb = p.GetBoundingBox()
        xs += [pcbnew.ToMM(bb.GetX()) - OX, pcbnew.ToMM(bb.GetRight()) - OX]
        ys += [pcbnew.ToMM(bb.GetY()) - OY, pcbnew.ToMM(bb.GetBottom()) - OY]
    return min(xs), min(ys), max(xs), max(ys)


# ------------------------------------------------------------------ routing primitives
def track(netname, pts, w=W_SIG, layer=F):
    for a, b in zip(pts, pts[1:]):
        if a == b:
            continue
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(P(*a)); t.SetEnd(P(*b)); t.SetWidth(pcbnew.FromMM(w))
        t.SetLayer(layer); t.SetNet(net(netname))
        board.Add(t)


def via(netname, xy, d=VIA_D, h=VIA_H):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(P(*xy)); v.SetDrill(pcbnew.FromMM(h)); v.SetWidth(pcbnew.FromMM(d))
    v.SetViaType(pcbnew.VIATYPE_THROUGH); v.SetLayerPair(F, B)
    v.SetNet(net(netname))
    board.Add(v)
    return xy


def gnd_via_at_pad(ref, num, dx, dy, w=0.4):
    """Short track from a pad to a GND via at an offset."""
    x, y = pad(ref, num)
    v = (round(x + dx, 3), round(y + dy, 3))
    track("GND", [(x, y), v], w)
    via("GND", v)


# ------------------------------------------------------------------ helpers for 2-pin chains
def signal_pad(ref):
    """The pad of a shunt part that is not on GND."""
    for n in ("1", "2"):
        if padnet.get((ref, n)) != "GND":
            return n


def shunt(ref, xc, y, side):
    """Shunt part: signal pad centred on the trace line x = xc, GND pad to the side."""
    sp = signal_pad(ref)
    # default pads lie on the x axis, pad "1" at -x
    rot = 0 if (sp == "1") == (side > 0) else 180
    fp = place(ref, 0, 0, rot, show_ref=False)
    sx, sy = pad(ref, sp)
    fp.SetPosition(P(xc - sx, y - sy))
    SIDE[ref] = side
    return fp


def series(ref, xc, y, top_net):
    """Series part along a vertical line: the pad on top_net goes up (toward -y)."""
    fp = place(ref, xc, y, 90, show_ref=False)
    p1 = pad(ref, "1")
    if (padnet[(ref, "1")] == top_net) != (p1[1] < y):
        fp.SetOrientationDegrees(270)
    return fp


def column(xc, y_start, items, direction=+1, first_net=None):
    """Stack a chain of parts along x = xc starting at y_start.

    items: list of (ref, kind) with kind 'S' (series) or 'P' (shunt).
    direction: +1 to stack downward (top edge SMAs), -1 upward (bottom edge).
    Returns the list of signal pads on the line, ordered from the connector.
    """
    y = y_start
    side = 1
    prev_net = first_net
    for ref, kind in items:
        if kind == "P":
            shunt(ref, xc, 0, side)
            side = -side
        else:
            top = prev_net if direction > 0 else [n for n in (padnet[(ref, "1")], padnet[(ref, "2")]) if n != prev_net][0]
            series(ref, xc, 0, top)
        fp = fps[ref]
        x0, y0, x1, y1 = courtyard(fp)
        h = y1 - y0
        cy = (y0 + y1) / 2
        target = y + direction * (h / 2)
        fp.Move(pcbnew.VECTOR2I(0, pcbnew.FromMM(target - cy)))
        y = y + direction * (h + 0.02)
        if kind == "S":
            prev_net = [n for n in (padnet[(ref, "1")], padnet[(ref, "2")]) if n != prev_net][0]
    return y


def route_column(items, conn_pad):
    """Join the signal pads of a placed chain and drop GND vias for the shunts."""
    pts = [conn_pad]
    for ref, kind in items:
        fp = fps[ref]
        if kind == "P":
            sp = signal_pad(ref)
            gp = "2" if sp == "1" else "1"
            gnd_via_at_pad(ref, gp, SIDE[ref] * 0.8, 0)
            pts.append((ref, sp))
        else:
            pts.append((ref, "1")); pts.append((ref, "2"))
    # connect consecutive pads that share a net
    coords = [(r, n, pad(r, n)) for r, n in pts[1:]]
    first = pts[0]
    seq = [(None, padnet[first[0], first[1]], pad(*first))] + \
          [(r, padnet[(r, n)], c) for r, n, c in coords]
    seq.sort(key=lambda e: e[2][1])
    for a, b in zip(seq, seq[1:]):
        if a[1] == b[1] and a[0] != b[0] or (a[1] == b[1] and a[0] is None):
            track(a[1], [a[2], b[2]], W_RF)


def outline():
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_RECT)
    s.SetStart(P(0, 0)); s.SetEnd(P(BW, BH))
    s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(pcbnew.FromMM(0.1))
    board.Add(s)


def setup():
    board.SetCopperLayerCount(4)
    ds = board.GetDesignSettings()
    ds.SetBoardThickness(pcbnew.FromMM(1.6))
    ds.SetAuxOrigin(P(0, BH))          # drill/place origin: bottom-left board corner
    ds.m_TrackMinWidth = pcbnew.FromMM(0.15)
    ds.m_ViasMinSize = pcbnew.FromMM(0.5)
    ds.m_MinThroughDrill = pcbnew.FromMM(0.3)
    ds.m_MinClearance = pcbnew.FromMM(0.15)
    ds.m_CopperEdgeClearance = pcbnew.FromMM(0.3)
    ds.m_HoleToHoleMin = pcbnew.FromMM(0.25)
    nc = ds.m_NetSettings.m_DefaultNetClass
    nc.SetClearance(pcbnew.FromMM(0.15)); nc.SetTrackWidth(pcbnew.FromMM(0.25))
    nc.SetViaDiameter(pcbnew.FromMM(VIA_D)); nc.SetViaDrill(pcbnew.FromMM(VIA_H))


def zone(layer, netname, prio=0, clearance=0.25):
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    z.SetNet(net(netname))
    ol = z.Outline()
    ol.NewOutline()
    m = 0.3
    for x, y in ((m, m), (BW - m, m), (BW - m, BH - m), (m, BH - m)):
        ol.Append(pcbnew.FromMM(OX + x), pcbnew.FromMM(OY + y))
    z.SetAssignedPriority(prio)
    z.SetLocalClearance(pcbnew.FromMM(clearance))
    z.SetMinThickness(pcbnew.FromMM(0.2))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THT_THERMAL)
    z.SetThermalReliefGap(pcbnew.FromMM(0.3))
    z.SetThermalReliefSpokeWidth(pcbnew.FromMM(0.4))
    z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    board.Add(z)
    return z


def seg_dist(p, a, b):
    ax, ay = a; bx, by = b; px, py = p
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def stitch(pitch=2.5, margin=1.2):
    """Drop GND stitching vias on a grid wherever copper of other nets is far enough."""
    obst = []   # (kind, geometry, radius)
    for fp in board.GetFootprints():
        bb = fp.GetBoundingBox(False, False)
        obst.append(("box", (pcbnew.ToMM(bb.GetX()) - OX - 0.45, pcbnew.ToMM(bb.GetY()) - OY - 0.45,
                             pcbnew.ToMM(bb.GetRight()) - OX + 0.45, pcbnew.ToMM(bb.GetBottom()) - OY + 0.45)))
    segs, vias = [], []
    for t in board.GetTracks():
        if t.GetClass() == "PCB_VIA":
            vias.append((L(t.GetPosition()), pcbnew.ToMM(t.GetWidth()) / 2))
        else:
            segs.append((L(t.GetStart()), L(t.GetEnd()), pcbnew.ToMM(t.GetWidth()) / 2,
                         t.GetNetname() == "GND"))
    n = 0
    y = margin
    while y <= BH - margin:
        x = margin
        while x <= BW - margin:
            p = (x, y)
            ok = not any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for k, b in obst)
            ok = ok and all(seg_dist(p, a, b) > r + VIA_D / 2 + 0.25 for a, b, r, isg in segs)
            ok = ok and all(math.hypot(x - v[0][0], y - v[0][1]) > v[1] + VIA_D / 2 + 0.35 for v in vias)
            if ok:
                via("GND", (round(x, 3), round(y, 3)))
                vias.append(((x, y), VIA_D / 2)); n += 1
            x += pitch
        y += pitch
    return n


def text(s, x, y, size=1.0, layer=pcbnew.F_SilkS, angle=0, bold=False):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(s); t.SetPosition(P(x, y)); t.SetLayer(layer)
    t.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(size), pcbnew.FromMM(size)))
    t.SetTextThickness(pcbnew.FromMM(size * 0.15 * (1.4 if bold else 1)))
    t.SetTextAngleDegrees(angle)
    board.Add(t)




# ================================================================== rev B
import json, subprocess, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
FREEROUTING = os.environ.get("FREEROUTING_JAR", os.path.join(HERE, "..", "freerouting.jar"))
NAMES = ["B210_1", "B210_2", "B200_1", "B200_2"]
CORE = ["U3", "U4", "U6", "R3", "R4", "C8", "C9", "R2", "C10", "C11", "C29", "TP4", "TP5"]
POWER = ["F1", "D1", "D2", "FB1", "C1", "C2", "U1", "C3", "C4", "U2", "C5", "C6", "C7",
         "R1", "D3", "TP1", "TP2", "TP3"]
# rev A positions (board-local mm, rotation) -- these clusters are moved rigidly
REVA = {
    "U4": (44.0, 31.0, 90), "U3": (36.5, 32.0, 90), "U6": (44.0, 42.0, 0), "R3": (40.5, 30.825, 0),
    "R4": (43.675, 36.6, 90), "C10": (48.4, 27.4, 0), "C11": (48.6, 29.6, 0), "C8": (34.5, 29.2, 0),
    "C9": (31.0, 31.5, 270), "R2": (32.5, 35.2, 0), "C29": (51.0, 40.05, 0),
    "TP4": (40.9, 28.6, 0), "TP5": (35.0, 36.4, 0),
    "F1": (8.8, 12.2, 0), "D1": (8.8, 8.6, 180), "D2": (13.2, 12.2, 180), "FB1": (16.0, 14.4, 270),
    "C1": (12.6, 16.2, 180), "C2": (16.0, 17.2, 270), "U1": (8.0, 23.5, 0), "C3": (4.2, 23.5, 90),
    "C4": (11.6, 22.6, 90), "U2": (8.0, 30.0, 0), "C5": (4.2, 30.0, 90), "C6": (11.6, 29.1, 90),
    "C7": (14.0, 29.1, 90), "R1": (13.6, 33.2, 180), "D3": (11.0, 33.2, 0),
    "TP1": (9.8, 19.6, 0), "TP2": (13.8, 25.6, 0), "TP3": (16.6, 33.2, 0),
}
HAND = []   # every hand-routed track/via (locked before autorouting)


def T(netname, pts, w=W_SIG, layer=None):
    n0 = board.GetTracks().size() if hasattr(board.GetTracks(), "size") else len(board.GetTracks())
    track(netname, [pad(*p) if isinstance(p[0], str) else p for p in pts], w, layer if layer is not None else F)


def V(netname, xy):
    return via(netname, xy)


def gv(ref, num, dx, dy, w=0.4):
    gnd_via_at_pad(ref, num, dx, dy, w)


def items_snapshot():
    return set(t.m_Uuid.AsString() for t in board.GetTracks())


def move_group(refs, before, angle, dx, dy):
    """Rotate refs + the tracks/vias created since `before` about the origin of
    their bbox centre by `angle` degrees, then translate by (dx, dy)."""
    new = [t for t in board.GetTracks() if t.m_Uuid.AsString() not in before]
    xs, ys = [], []
    for r in refs:
        x0, y0, x1, y1 = courtyard(fps[r]); xs += [x0, x1]; ys += [y0, y1]
    c = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    ctr = P(*c)
    for it in [fps[r] for r in refs] + new:
        if angle:
            it.Rotate(ctr, pcbnew.EDA_ANGLE(angle, pcbnew.DEGREES_T))
        it.Move(pcbnew.VECTOR2I(pcbnew.FromMM(dx), pcbnew.FromMM(dy)))
    return new


def group_bbox(refs):
    xs, ys = [], []
    for r in refs:
        x0, y0, x1, y1 = courtyard(fps[r]); xs += [x0, x1]; ys += [y0, y1]
    return min(xs), min(ys), max(xs), max(ys)


def core_cluster():
    """TCXO + LMK1C1104 + PPS quad buffer, placed and routed exactly as rev A."""
    for r in CORE:
        x, y, rot = REVA[r]
        place(r, x, y, rot, show_ref=(r in ("U3", "U4", "U6", "TP4", "TP5")))
    before = items_snapshot()
    osc, clk, gnd = N("+3V3_OSC"), N("+3V3_CLK"), "GND"
    # --- TCXO U3: supply via -> C8 -> pin 9 (SiT5155 layout guide, p.35)
    V(osc, (32.5, 28.6))
    T(osc, [(32.5, 28.6), (33.1, 29.2), ("C8", 1)], 0.4)
    T(osc, [("C8", 1), (34.02, 30.4), ("U3", 9)], 0.4)
    T(osc, [(32.5, 28.6), (31.0, 30.1), ("C9", 1)], 0.4)
    T(gnd, [("C8", 2), (35.9, 29.2), ("U3", 8)], 0.4)
    V(gnd, (35.9, 28.45)); T(gnd, [(35.9, 29.2), (35.9, 28.45)], 0.4)
    gv("C9", 2, 0, 1.05)
    T(gnd, [("U3", 8), ("U3", 2)], 0.3); T(gnd, [("U3", 7), ("U3", 3)], 0.3)
    T(gnd, [("U3", 10), ("U3", 5)], 0.3); T(gnd, [("U3", 5), ("U3", 4)], 0.3)
    V(gnd, (36.5, 32.0))
    V(gnd, (39.7, 33.4)); T(gnd, [("U3", 4), (39.7, 33.4)], 0.4)
    for p, dy in (("2", 1.425), ("3", 1.425), ("7", -1.425)):
        gv("U3", p, 0, dy)
    T(N("OSC_OE"), [("U3", 1), (34.475, 34.2), (33.475, 35.2), ("R2", 2)], W_SIG)
    T(N("OSC_OE"), [("R2", 2), (34.2, 36.4), ("TP5", 1)], W_SIG)
    V(osc, (30.9, 35.2)); T(osc, [(30.9, 35.2), ("R2", 1)], 0.3)
    T(N("Net-(U3-CLK)"), [("U3", 6), ("R3", 1)], W_SIG)
    c10 = N("CLK10_IN")
    T(c10, [("R3", 2), (42.1, 30.825), (42.1, 34.8), (43.025, 34.8), ("U4", 1)], W_SIG)
    T(c10, [("R3", 2), ("TP4", 1)], W_SIG)
    # --- LMK1C1104 U4
    T(N("REF_EN"), [("U4", 2), ("R4", 2)], W_SIG)
    V(clk, (43.675, 37.9)); T(clk, [("R4", 1), (43.675, 37.9)], 0.3)
    V(clk, (44.325, 26.75)); T(clk, [("U4", 6), (44.325, 26.75)], 0.3)
    T(gnd, [("U4", 4), (45.9, 34.9)], 0.3); V(gnd, (45.9, 34.9))
    V(clk, (47.0, 27.4)); T(clk, [(47.0, 27.4), ("C10", 1)], 0.3)
    V(clk, (47.0, 29.6)); T(clk, [(47.0, 29.6), ("C11", 1)], 0.3)
    gv("C10", 2, 0.9, 0); gv("C11", 2, 1.0, 0)
    # --- SN74LVC125A U6
    buf = N("PPS_BUF")
    for p, v in ((2, (39.9, 40.7)), (5, (39.9, 42.65)), (12, (50.8, 41.35)), (9, (50.8, 43.3))):
        T(buf, [("U6", p), v], W_SIG); V(buf, v)
    for p, v in ((1, (38.9, 40.05)), (4, (38.9, 42.0)), (7, (41.137, 45.0)), (13, (48.6, 40.7)), (10, (48.6, 42.65))):
        T(gnd, [("U6", p), v], W_SIG); V(gnd, v)
    V(clk, (49.8, 40.05)); T(clk, [("U6", 14), (49.8, 40.05), ("C29", 1)], 0.3)
    gv("C29", 2, 0.9, 0)
    # local pieces of rev A's In2 / B.Cu trunks, so each rail is one island inside the cluster
    T(osc, [(32.5, 28.6), (29.8, 28.6), (29.8, 35.2), (30.9, 35.2)], W_PWR, IN2)
    T(clk, [(43.675, 37.8), (49.8, 37.8), (49.8, 40.05)], W_PWR, IN2)
    T(clk, [(47.0, 37.8), (47.0, 26.75), (44.325, 26.75)], W_PWR, IN2)
    T(buf, [(39.9, 38.6), (50.8, 38.6), (50.8, 43.3)], W_SIG, B)
    T(buf, [(39.9, 38.6), (39.9, 42.65)], W_SIG, B)
    return before


def power_cluster():
    """Input protection + both LDOs, placed and routed as rev A (J1 moves)."""
    for r in POWER:
        x, y, rot = REVA[r]
        place(r, x, y, rot, show_ref=r.startswith(("U", "TP")))
    before = items_snapshot()
    WP = W_PWR
    T(N("Net-(D1-K)"), [("F1", 2), ("D2", 2)], WP)
    T(N("Net-(D1-K)"), [("F1", 2), ("D1", 1)], WP)
    gv("D1", 2, -1.5, 0, 0.5)
    T(N("Net-(D2-K)"), [("D2", 1), (16.0, 12.2), ("FB1", 1)], WP)
    vin = N("VIN_5V")
    T(vin, [("FB1", 2), ("C2", 1), (13.55, 16.72), ("C1", 1)], WP)
    gv("C2", 2, 0, 0.9); gv("C1", 2, -1.0, 0)
    T(vin, [(13.55, 16.72), (13.55, 19.6), (5.7, 19.6), (5.7, 30.95)], WP)
    for u in ("U1", "U2"):
        for p in (1, 3):
            x, y = pad(u, p)
            T(vin, [(5.7, y), (x, y)], 0.4)
        gv(u, 2, 1.138, 0, 0.3)
    T(vin, [("C3", 1), (5.7, 24.45)], 0.4); gv("C3", 2, 0, -1.1)
    T(vin, [("C5", 1), (5.7, 30.95)], 0.4); gv("C5", 2, 0, -1.1)
    osc = N("+3V3_OSC")
    T(osc, [("U1", 5), (10.2, 22.55), (11.025, 23.375), ("C4", 1), (12.6, 24.4), ("TP2", 1)], 0.4)
    V(osc, (12.6, 24.4)); gv("C4", 2, 0, -1.0)
    clk = N("+3V3_CLK")
    T(clk, [("U2", 5), (10.3, 29.05), (11.125, 29.875), (14.0, 29.875), ("C7", 1), (15.2, 30.05)], 0.4)
    V(clk, (15.2, 30.05))
    gv("C6", 2, 0, -1.0); gv("C7", 2, 0, -1.0)
    V(clk, (15.2, 33.2)); T(clk, [("R1", 1), (15.2, 33.2), ("TP3", 1)], 0.3)
    T(N("Net-(D3-A)"), [("R1", 2), ("D3", 2)], W_SIG); gv("D3", 1, -1.2, 0)
    return before


def place_columns():
    """Jacks, 10 MHz filter columns (upward, right of the 10 MHz jack) and
    PPS output chains (downward, under the PPS jack)."""
    ref_chains, pps_chains = [], []
    for k, xj in enumerate(XC):
        place(f"J{3+k}", xj, Y_REF, 0, show_ref=False)
        place(f"J{7+k}", xj, Y_PPS, 0, show_ref=False)
        r = lambda n: f"R{n + 4*k}"
        c = lambda n: f"C{n + 4*k}"
        items = [(f"D{4+k}", "P"), (r(8), "P"), (r(7), "S"), (r(6), "P"), (c(15), "P"),
                 (f"L{2+2*k}", "S"), (c(14), "P"), (f"L{1+2*k}", "S"), (c(13), "P"),
                 (c(12), "S"), (r(5), "S")]
        conn = (f"J{3+k}", "1")
        end = column(xj + COL_DX, Y_REF + 2.4, items, -1, padnet[conn])
        ref_chains.append((items, conn, xj + COL_DX, end))
        items = [(f"D{10+k}", "P"), (f"R{25+k}", "S")]
        conn = (f"J{7+k}", "1")
        end = column(xj, Y_PPS + 4.25, items, +1, padnet[conn])
        pps_chains.append((items, conn, xj, end))
    return ref_chains, pps_chains


def route_chain(items, conn, xc, stub):
    """Join the chain pads (consecutive pads on the same net), GND vias for shunts,
    and the stub from the jack centre pin."""
    pts = []
    for ref, kind in items:
        if kind == "P":
            sp = signal_pad(ref)
            gp = "2" if sp == "1" else "1"
            gnd_via_at_pad(ref, gp, SIDE[ref] * 0.8, 0)
            pts.append((ref, sp))
        else:
            pts += [(ref, "1"), (ref, "2")]
    seq = sorted([(padnet[(r, n)], pad(r, n), r) for r, n in pts], key=lambda e: e[1][1])
    for a, b in zip(seq, seq[1:]):
        if a[0] == b[0] and a[2] != b[2]:
            track(a[0], [a[1], b[1]], W_RF)
    # stub from the jack centre pin to the first chain pad on the jack's net
    jn = padnet[conn]
    first = [e for e in seq if e[0] == jn]
    tgt = min(first, key=lambda e: math.hypot(e[1][0] - xc, e[1][1] - pad(*conn)[1]))[1]
    jp = pad(*conn)
    track(jn, stub(jp, tgt), W_RF)


def place_bays():
    # ---- bay 1 (between column 0 and column 1): power
    before_p = power_cluster()
    bx0 = XC[0] + COL_DX + 2.2; bx1 = XC[1] - 4.3
    x0, y0, x1, y1 = group_bbox(POWER)
    move_group(POWER, before_p, 0, (bx0 + bx1) / 2 - (x0 + x1) / 2, 8.6 - y0)
    # J1 on the top edge, mouth up, centred on the bay
    place("J1", (bx0 + bx1) / 2, 3.25, 180)
    # ---- bay 2: TCXO + buffers, rotated 90 deg
    before_c = core_cluster()
    bx0 = XC[1] + COL_DX + 2.2; bx1 = XC[2] - 4.3
    core_tracks = move_group(CORE, before_c, 90, 0, 0)
    x0, y0, x1, y1 = group_bbox(CORE)
    dx, dy = (bx0 + bx1) / 2 - (x0 + x1) / 2, BH / 2 - (y0 + y1) / 2
    for it in [fps[r] for r in CORE] + core_tracks:
        it.Move(pcbnew.VECTOR2I(pcbnew.FromMM(dx), pcbnew.FromMM(dy)))
    # ---- bay 3: PPS input (vertical SMA J2 + Schmitt buffer + LED)
    cx = (XC[2] + COL_DX + 2.2 + XC[3] - 4.3) / 2
    place("J2", cx, BH - 6.4, 0, show_ref=True)
    for ref, (dx, y, rot) in {"D8": (-2.6, 31.2, 90), "R23": (-1.1, 31.2, 90), "R22": (0.6, 31.2, 0),
                              "R21": (2.4, 31.2, 90), "U5": (0.0, 27.0, 0), "C28": (3.4, 24.6, 90),
                              "R24": (-3.2, 23.6, 90), "D9": (-3.2, 20.4, 90), "TP6": (3.6, 20.4, 0)}.items():
        place(ref, cx + dx, y, rot, show_ref=ref in ("U5", "J2", "TP6"))
    # ---- mechanical
    for ref, (x, y) in {"H1": (4, 4), "H2": (BW - 4, 4), "H3": (4, BH - 4), "H4": (BW - 4, BH - 4)}.items():
        place(ref, x, y, show_ref=False)
    for ref, (x, y) in {"FID1": (4.5, 15.0), "FID2": (BW - 3.5, 13.0), "FID3": (BW - 3.5, 30.0)}.items():
        place(ref, x, y, show_ref=False)
    place("TP7", 4.5, 24.0, show_ref=True)


# ------------------------------------------------------------------ GND vias for every SMD GND pad
def copper_obstacles():
    obs = []
    for fp in board.GetFootprints():
        for p in fp.Pads():
            bb = p.GetBoundingBox()
            obs.append(("pad", p.GetNetname(), (pcbnew.ToMM(bb.GetX()) - OX, pcbnew.ToMM(bb.GetY()) - OY,
                                                pcbnew.ToMM(bb.GetRight()) - OX, pcbnew.ToMM(bb.GetBottom()) - OY)))
    for t in board.GetTracks():
        if t.GetClass() == "PCB_VIA":
            obs.append(("via", t.GetNetname(), (L(t.GetPosition()), pcbnew.ToMM(t.GetWidth()) / 2)))
        else:
            obs.append(("seg", t.GetNetname(), (L(t.GetStart()), L(t.GetEnd()), pcbnew.ToMM(t.GetWidth()) / 2, t.GetLayer())))
    return obs


def via_ok(p, netname, obs, clr=0.2, start=None, w=0.4):
    x, y = p
    r = VIA_D / 2
    if not (0.8 < x < BW - 0.8 and 0.8 < y < BH - 0.8):
        return False
    for kind, nn, g in obs:
        if nn == netname:
            continue
        if kind == "pad":
            if g[0] - r - clr < x < g[2] + r + clr and g[1] - r - clr < y < g[3] + r + clr:
                return False
            if start is not None:      # the stub track must clear the pad too
                cx, cy = (g[0] + g[2]) / 2, (g[1] + g[3]) / 2
                hx, hy = (g[2] - g[0]) / 2, (g[3] - g[1]) / 2
                for t in (0.25, 0.5, 0.75):
                    qx, qy = start[0] + t * (x - start[0]), start[1] + t * (y - start[1])
                    if abs(qx - cx) < hx + w / 2 + clr and abs(qy - cy) < hy + w / 2 + clr:
                        return False
        elif kind == "via":
            if math.hypot(x - g[0][0], y - g[0][1]) < g[1] + r + clr:
                return False
        else:
            if seg_dist(p, g[0], g[1]) < g[2] + r + clr:
                return False
            if start is not None and g[3] == F:
                for t in (0.25, 0.5, 0.75):
                    q = (start[0] + t * (x - start[0]), start[1] + t * (y - start[1]))
                    if seg_dist(q, g[0], g[1]) < g[2] + w / 2 + clr:
                        return False
    # keep clear of other GND vias (hole-to-hole)
    for kind, nn, g in obs:
        if kind == "via" and math.hypot(x - g[0][0], y - g[0][1]) < 2 * r + 0.25:
            return False
    return True


def gnd_pad_vias():
    """Every SMD GND pad without a hand-routed GND connection gets a short stub + via."""
    gtracks = [(L(t.GetStart()), L(t.GetEnd())) for t in board.GetTracks()
               if t.GetNetname() == "GND" and t.GetClass() != "PCB_VIA"]
    added, failed = 0, []
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname() != "GND" or p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                continue
            c = L(p.GetPosition())
            if any(math.hypot(c[0] - a[0], c[1] - a[1]) < 0.05 or math.hypot(c[0] - b[0], c[1] - b[1]) < 0.05
                   for a, b in gtracks):
                continue
            obs = copper_obstacles()
            fc = L(fp.GetPosition())
            d = (c[0] - fc[0], c[1] - fc[1]); n = math.hypot(*d)
            dirs = [(d[0] / n, d[1] / n)] if n > 0.05 else []
            dirs += [(1, 0), (-1, 0), (0, 1), (0, -1), (0.7, 0.7), (-0.7, 0.7), (0.7, -0.7), (-0.7, -0.7)]
            bb = p.GetBoundingBox()
            half = max(pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight())) / 2
            done = False
            for dist in (half + 0.55, half + 0.8, half + 1.1, half + 1.5):
                for u in dirs:
                    v = (round(c[0] + u[0] * dist, 3), round(c[1] + u[1] * dist, 3))
                    if via_ok(v, "GND", obs, start=c):
                        track("GND", [c, v], 0.4); via("GND", v)
                        gtracks.append((c, v)); added += 1; done = True
                        break
                if done:
                    break
            if not done:
                failed.append(f"{fp.GetReference()}.{p.GetNumber()}")
    return added, failed


# ------------------------------------------------------------------ autorouting
NETCLASSES = [("GND", 0.4, ["GND"]),
              ("PWR", 0.4, ["*VIN_5V", "*+3V3_OSC", "*+3V3_CLK", "Net-(J1-Pin_1)", "Net-(D1-K)", "Net-(D2-K)"]),
              ("RF", 0.35, ["*REF?_Y", "*CLK10_IN", "*PPS?_Y"])]


def write_netclasses():
    pro = f"{PRJ}/heron_clock.kicad_pro"
    d = json.load(open(pro))
    ns = d["net_settings"]
    base = [c for c in ns["classes"] if c["name"] == "Default"][0]
    ns["classes"] = [base]
    ns["netclass_patterns"] = []
    for name, w, pats in NETCLASSES:
        c = dict(base); c["name"] = name; c["track_width"] = w
        ns["classes"].append(c)
        ns["netclass_patterns"] += [{"netclass": name, "pattern": p} for p in pats]
    json.dump(d, open(pro, "w"), indent=2)


def autoroute(path):
    """Export DSN, run Freerouting headless, merge the session's new wiring."""
    b = pcbnew.LoadBoard(path)
    b.SetLayerType(IN1, pcbnew.LT_POWER)          # In1 is the GND plane: never route on it
    for t in b.GetTracks():
        t.SetLocked(True)                         # hand routes are fixed (exported as protect)
    dsn, ses = f"{PRJ}/heron_clock.dsn", f"{PRJ}/heron_clock.ses"
    if os.path.exists(ses):
        os.remove(ses)
    assert pcbnew.ExportSpecctraDSN(b, dsn)
    cmd = ["xvfb-run", "-a", "java", "-jar", FREEROUTING, "-de", dsn, "-do", ses, "-mp", "60", "-inc", "GND"]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1500)
    assert os.path.exists(ses), "Freerouting produced no session file"
    added = import_ses(b, ses, dsn)
    b.SetLayerType(IN1, pcbnew.LT_SIGNAL)
    pcbnew.SaveBoard(path, b)
    return added


def import_ses(b, ses, dsn):
    sx = parse(open(ses).read())
    routes = find1(sx, "routes")
    res = find1(routes, "resolution")
    scale = 1.0 / (float(res[2]) * (1000.0 if res[1] == "um" else 1.0))   # session units -> mm
    nets = {n.GetNetname(): n for n in b.GetNetInfo().NetsByName().values()} if False else None
    netcode = {}
    for code, ni in b.GetNetInfo().NetsByNetcode().items():
        netcode[ni.GetNetname()] = ni
    layers = {b.GetLayerName(l): l for l in (F, IN1, IN2, B)}
    have = set()
    for t in b.GetTracks():
        if t.GetClass() == "PCB_VIA":
            have.add(("v", round(pcbnew.ToMM(t.GetPosition().x), 3), round(pcbnew.ToMM(t.GetPosition().y), 3)))
        else:
            a = (round(pcbnew.ToMM(t.GetStart().x), 3), round(pcbnew.ToMM(t.GetStart().y), 3))
            c = (round(pcbnew.ToMM(t.GetEnd().x), 3), round(pcbnew.ToMM(t.GetEnd().y), 3))
            have.add(("s", t.GetLayer()) + tuple(sorted([a, c])))
    n_seg = n_via = 0
    for netx in find(find1(routes, "network_out"), "net"):
        name = netx[1]
        ni = netcode.get(name)
        if ni is None:
            continue
        for w in find(netx, "wire"):
            pth = find1(w, "path")
            layer = layers[pth[1]]
            width = float(pth[2]) * scale
            xy = [float(v) * scale for v in pth[3:]]
            pts = [(xy[i], -xy[i + 1]) for i in range(0, len(xy), 2)]
            for a, c in zip(pts, pts[1:]):
                key = ("s", layer) + tuple(sorted([(round(a[0], 3), round(a[1], 3)), (round(c[0], 3), round(c[1], 3))]))
                if key in have or a == c:
                    continue
                t = pcbnew.PCB_TRACK(b)
                t.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(a[0]), pcbnew.FromMM(a[1])))
                t.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(c[0]), pcbnew.FromMM(c[1])))
                t.SetWidth(pcbnew.FromMM(width)); t.SetLayer(layer); t.SetNet(ni)
                b.Add(t); n_seg += 1
        for v in find(netx, "via"):
            x, y = float(v[2]) * scale, -float(v[3]) * scale
            if ("v", round(x, 3), round(y, 3)) in have:
                continue
            vv = pcbnew.PCB_VIA(b)
            vv.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
            vv.SetDrill(pcbnew.FromMM(VIA_H)); vv.SetWidth(pcbnew.FromMM(VIA_D))
            vv.SetViaType(pcbnew.VIATYPE_THROUGH); vv.SetLayerPair(F, B); vv.SetNet(ni)
            b.Add(vv); n_via += 1
    return n_seg, n_via


def u3_keepout(margin=0.3):
    """SiT5155 layout guide (p.35): no traces of other nets under U3.
    The hand routes on F.Cu keep this rule. A track keepout on In2.Cu and
    B.Cu stops the autorouter from passing under U3. Vias stay allowed so the
    GND vias under the body remain legal."""
    x0, y0, x1, y1 = courtyard(fps["U3"])
    z = pcbnew.ZONE(board)
    z.SetIsRuleArea(True)
    z.SetDoNotAllowTracks(True); z.SetDoNotAllowVias(False); z.SetDoNotAllowPads(False)
    z.SetDoNotAllowCopperPour(False); z.SetDoNotAllowFootprints(False)
    ls = pcbnew.LSET(); ls.AddLayer(IN2); ls.AddLayer(B)
    z.SetLayerSet(ls)
    ol = z.Outline(); ol.NewOutline()
    for x, y in ((x0 - margin, y0 - margin), (x1 + margin, y0 - margin), (x1 + margin, y1 + margin), (x0 - margin, y1 + margin)):
        ol.Append(pcbnew.FromMM(OX + x), pcbnew.FromMM(OY + y))
    board.Add(z)


# ------------------------------------------------------------------ labels
def labels_revB():
    for k, x in enumerate(XC):
        text(NAMES[k], x, 1.6, 0.9, bold=True)
        text("PPS", x - 5.6, Y_PPS, 0.8, angle=90)
        text("10M", x - 5.6, Y_REF, 0.8, angle=90)
    text("+5V", (XC[0] + XC[1]) / 2 + 5.2, 1.5, 0.8)
    text("PPS IN", (XC[2] + XC[3]) / 2 + COL_DX / 2 - 0.5, BH - 0.9, 0.8)
    t = "HERON CLOCK DIST rev B   SiT5155 10 MHz + PPS x4   water-soluble flux only"
    tx = pcbnew.PCB_TEXT(board)
    tx.SetText(t); tx.SetPosition(P(BW / 2, BH - 9.0)); tx.SetLayer(pcbnew.B_SilkS)
    tx.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(1.0), pcbnew.FromMM(1.0)))
    tx.SetTextThickness(pcbnew.FromMM(0.15)); tx.SetMirrored(True)
    board.Add(tx)
    tx2 = pcbnew.PCB_TEXT(board)
    tx2.SetText("REAR (faces rack rear plate)  -  jacks face the SDRs"); tx2.SetPosition(P(BW / 2, BH - 7.0))
    tx2.SetLayer(pcbnew.B_SilkS); tx2.SetMirrored(True)
    tx2.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(0.8), pcbnew.FromMM(0.8))); tx2.SetTextThickness(pcbnew.FromMM(0.12))
    board.Add(tx2)


if __name__ == "__main__":
    setup()
    outline()
    place_bays()
    ref_chains, pps_chains = place_columns()
    for items, conn, xc, end in ref_chains:
        route_chain(items, conn, xc, lambda jp, tg: [jp, (tg[0], jp[1]), tg])
    for items, conn, xc, end in pps_chains:
        route_chain(items, conn, xc, lambda jp, tg: [jp, (jp[0], tg[1]), tg])
    u3_keepout()
    added, failed = gnd_pad_vias()
    print("gnd pad vias:", added, "failed:", failed)
    labels_revB()
    path = f"{PRJ}/heron_clock.kicad_pcb"
    pcbnew.SaveBoard(path, board)
    write_netclasses()
    if "--no-route" not in sys.argv:
        print("autoroute: segments, vias =", autoroute(path))
    b = pcbnew.LoadBoard(path)
    board = b
    nets = {}
    for layer in (F, IN1, IN2, B):
        zone(layer, "GND")
    for t in b.GetTracks():
        t.SetLocked(False)
    nst = stitch()
    pcbnew.SaveBoard(path, b)
    b2 = pcbnew.LoadBoard(path)
    pcbnew.ZONE_FILLER(b2).Fill(b2.Zones())
    pcbnew.SaveBoard(path, b2)
    print("stitch vias:", nst)
    pcbnew.WriteDRCReport(b2, f"{PRJ}/drc.rpt", pcbnew.EDA_UNITS_MILLIMETRES, True)
    print(re.findall(r"\*\* Found \d+ [^*]+", open(f"{PRJ}/drc.rpt").read()))
