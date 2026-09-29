"""Generate the HERON clock-distribution PCB (KiCad 7, 4 layers, 84 x 56 mm).

Inputs : project/heron_clock.net (exported from the schematic) and the
         schematic file (for symbol UUIDs, so "Update PCB from Schematic"
         in KiCad links every footprint to its symbol).
Output : project/heron_clock.kicad_pcb

Stackup: F.Cu signal / In1.Cu solid GND / In2.Cu power traces + GND fill / B.Cu signal + GND.
Layout : 10 MHz SMAs on the top edge, PPS SMAs on the bottom edge,
         5 V input and PPS input on the left edge.
Board-local coordinates are in mm with (0, 0) at the top-left board corner.
"""
import math, os, re, sys
import pcbnew
from sexp import parse, find, find1

PRJ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "project")
# Stock KiCad 7 footprint folder (set KICAD7_FOOTPRINT_DIR on macOS/Windows).
FPLIB = os.environ.get("KICAD7_FOOTPRINT_DIR", "/usr/share/kicad/footprints")
OX, OY = 100.0, 100.0          # sheet offset of the board corner
BW, BH = 84.0, 56.0            # board size
REF_X = [20.0, 36.0, 52.0, 68.0]   # SMA columns (top: 10 MHz, bottom: PPS)
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


def build():
    # -------------------------------------------------- mechanical
    for ref, (x, y) in {"H1": (4, 4), "H2": (BW - 4, 4), "H3": (4, BH - 4), "H4": (BW - 4, BH - 4)}.items():
        place(ref, x, y, show_ref=False)
    for ref, (x, y) in {"FID1": (10.0, 3.0), "FID2": (BW - 4, 10), "FID3": (BW - 4, BH - 10)}.items():
        place(ref, x, y, show_ref=False)
    # -------------------------------------------------- connectors
    for k, x in enumerate(REF_X):
        place(f"J{3+k}", x, 2.54 + SMA_INSET, 90)
        place(f"J{7+k}", x, BH - 2.54 - SMA_INSET, 270)
    place("J2", 2.54 + SMA_INSET, 42.0, 180)
    place("J1", 3.1, 14.0, 270)
    # -------------------------------------------------- 10 MHz chains (top)
    ref_chains = []
    for k, xc in enumerate(REF_X):
        r = lambda n: f"R{n + 4*k}"
        c = lambda n: f"C{n + 4*k}"
        items = [(f"D{4+k}", "P"), (r(8), "P"), (r(7), "S"), (r(6), "P"), (c(15), "P"),
                 (f"L{2+2*k}", "S"), (c(14), "P"), (f"L{1+2*k}", "S"), (c(13), "P"),
                 (c(12), "S"), (r(5), "S")]
        conn = (f"J{3+k}", "1")
        end = column(xc, 5.88 + SMA_INSET, items, +1, padnet[conn])
        ref_chains.append((items, conn, end))
    # -------------------------------------------------- PPS chains (bottom)
    pps_chains = []
    for k, xc in enumerate(REF_X):
        items = [(f"D{10+k}", "P"), (f"R{25+k}", "S")]
        conn = (f"J{7+k}", "1")
        end = column(xc, BH - 5.88 - SMA_INSET, items, -1, padnet[conn])
        pps_chains.append((items, conn, end))
    # -------------------------------------------------- core ICs
    place("U4", 44.0, 31.0, 90)          # LMK1C1104
    fps["U4"].Reference().SetPosition(P(46.9, 32.6))
    fps["U4"].Reference().SetTextAngleDegrees(0)
    place("U3", 36.5, 32.0, 90)          # SiT5155
    place("U6", 44.0, 42.0, 0)           # SN74LVC125A
    place("R3", 40.5, 30.825, 0, False)  # 22R TCXO -> buffer
    place("R4", 43.675, 36.6, 90, False) # 1G pull-up
    place("C10", 48.4, 27.4, 0, False)
    place("C11", 48.6, 29.6, 0, False)
    # C8 (100 nF) sits directly above U3 pin 9 (VDD). The SiT5155 datasheet
    # (Layout Guidelines, p.35) wants this capacitor 1-2 mm from the VDD pin.
    place("C8", 34.5, 29.2, 0, False)
    place("C9", 31.0, 31.5, 270, False)
    place("R2", 32.5, 35.2, 0, False)
    place("C29", 51.0, 40.05, 0, False)
    # -------------------------------------------------- power input (left)
    place("F1", 8.8, 12.2, 0, False)
    place("D1", 8.8, 8.6, 180, False)
    place("D2", 13.2, 12.2, 180, False)
    place("FB1", 16.0, 14.4, 270, False)
    place("C1", 12.6, 16.2, 180, False)
    place("C2", 16.0, 17.2, 270, False)
    place("U1", 8.0, 23.5, 0)
    place("C3", 4.2, 23.5, 90, False)
    place("C4", 11.6, 22.6, 90, False)
    place("U2", 8.0, 30.0, 0)
    place("C5", 4.2, 30.0, 90, False)
    place("C6", 11.6, 29.1, 90, False)
    place("C7", 14.0, 29.1, 90, False)
    place("R1", 13.6, 33.2, 180, False)
    place("D3", 11.0, 33.2, 0, False)
    # -------------------------------------------------- PPS input (left)
    place("D8", 6.9, 41.65, 90, False)
    place("R23", 8.1, 41.49, 90, False)
    place("R22", 9.6, 42.0, 0, False)
    place("R21", 11.1, 41.49, 90, False)
    place("U5", 13.8, 42.0, 0)
    place("C28", 16.4, 39.2, 0, False)
    place("R24", 10.0, 47.0, 0, False)
    place("D9", 13.0, 47.0, 180, False)
    # -------------------------------------------------- test points
    for ref, (x, y) in {"TP1": (9.8, 19.6), "TP2": (13.8, 25.6), "TP3": (16.6, 33.2),
                        "TP4": (40.9, 28.6), "TP5": (35.0, 36.4), "TP6": (17.0, 44.8),
                        "TP7": (26.0, 39.0)}.items():
        place(ref, x, y, show_ref=True)
    return ref_chains, pps_chains


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


def labels():
    names = ["B210_1", "B210_2", "B200_1", "B200_2"]
    for k, x in enumerate(REF_X):
        text(names[k], x - 6.2, 3.2, 0.8, angle=90)
        text(names[k], x - 6.2, BH - 3.2, 0.8, angle=90)
    text("10 MHz OUT", 60.0, 7.0, 0.9)
    text("PPS OUT", 60.0, BH - 7.0, 0.9)
    text("PPS IN", 6.4, 36.4, 0.8)
    text("+5V IN", 6.0, 18.2, 0.8)
    text("PWR", 11.0, 35.0, 0.8)
    text("PPS", 13.0, 48.8, 0.8)
    text("HERON CLOCK DIST  rev A", 62.0, 33.2, 1.0, bold=True)
    text("SiT5155 10 MHz + PPS x4", 62.0, 35.0, 0.8)
    text("water-soluble flux only", 62.0, 38.8, 0.8)


def drc(path):
    pcbnew.WriteDRCReport(board, path, pcbnew.EDA_UNITS_MILLIMETRES, True)
    txt = open(path).read()
    return txt


if __name__ == "__main__":
    setup()
    outline()
    ref_chains, pps_chains = build()
    for items, conn, end in ref_chains + pps_chains:
        route_column(items, conn)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import route
    route.run(sys.modules[__name__])
    labels()
    for layer in (F, IN1, IN2, B):
        zone(layer, "GND")
    nst = stitch()
    path = f"{PRJ}/heron_clock.kicad_pcb"
    pcbnew.SaveBoard(path, board)
    # Zone fill and DRC need a board loaded from disk (with its project).
    b2 = pcbnew.LoadBoard(path)
    pcbnew.ZONE_FILLER(b2).Fill(b2.Zones())
    pcbnew.SaveBoard(path, b2)
    print("saved; footprints:", len(fps), "stitch vias:", nst)
    pcbnew.WriteDRCReport(b2, f"{PRJ}/drc.rpt", pcbnew.EDA_UNITS_MILLIMETRES, True)
    print(re.findall(r"\*\* Found \d+ [^*]+", open(f"{PRJ}/drc.rpt").read()))
