"""Board-building helpers for the rev D generator (KiCad 10 pcbnew).

Why a separate module: gen_pcb.py holds the rev D decisions (where each part
goes and how the sensitive nets run). This module holds the mechanics that do
not change with the layout: loading footprints from the netlist, tracks, vias,
zones, keep-outs, automatic GND vias, stitching, and the Freerouting round
trip. Most of it comes from ../clock_board_revB/gen_pcb.py, changed for the
KiCad 10 API (fields, pads by number, rule areas, zone fills).

Board-local coordinates: mm, (0, 0) = top-left board corner. The board sits at
(OX, OY) on the KiCad sheet.
"""

import math
import os
import subprocess

import pcbnew

from sexp import parse, find, find1

OX, OY = 100.0, 100.0          # sheet offset of the board corner (the same as rev B and rev C)
F, IN1, IN2, B = pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu
VIA_D, VIA_H = 0.6, 0.3        # the project default via
W_SIG, W_RF, W_PWR, W_GND = 0.25, 0.35, 0.5, 0.4


def P(x, y):
    """Board-local mm -> KiCad internal units."""
    return pcbnew.VECTOR2I(pcbnew.FromMM(OX + x), pcbnew.FromMM(OY + y))


def L(v):
    """KiCad position -> board-local mm tuple."""
    return (pcbnew.ToMM(v.x) - OX, pcbnew.ToMM(v.y) - OY)


def seg_dist(p, a, b):
    """Distance from point p to the segment a-b."""
    ax, ay = a; bx, by = b; px, py = p
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0 if l2 == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / l2))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


class Gen:
    """One board under construction, built from the KiCad netlist.

    prj: the project folder (netlist, schematic, project library).
    fplib: the stock KiCad 10 footprint folder.
    bw, bh: board size (for the edge checks of the automatic vias).
    board: an existing (loaded) board to work on; default a new empty board.
    """

    def __init__(self, prj, fplib, bw, bh, board=None):
        self.prj, self.fplib, self.bw, self.bh = prj, fplib, bw, bh
        # Why the board argument: a BOARD that is made and then dropped also
        # frees the project of a loaded board in KiCad 10, and the next save crashes.
        self.board = board if board is not None else pcbnew.BOARD()
        self.fps, self.nets, self.side = {}, {}, {}
        netl = parse(open(os.path.join(prj, "heron_clock.net"), encoding="utf-8").read())
        self.comps = {}
        for c in find(find1(netl, "components"), "comp"):
            self.comps[find1(c, "ref")[1]] = dict(
                value=find1(c, "value")[1], fp=find1(c, "footprint")[1],
                fields={f[1][1]: f[2] for f in find(find1(c, "fields") or [], "field") if len(f) > 2})
        self.padnet = {}
        for n in find(find1(netl, "nets"), "net"):
            for node in find(n, "node"):
                self.padnet[(find1(node, "ref")[1], find1(node, "pin")[1])] = find1(n, "name")[1]
        sch = parse(open(os.path.join(prj, "heron_clock.kicad_sch"), encoding="utf-8").read())
        self.symuuid, self.dnp = {}, set()
        for s in find(sch, "symbol"):
            props = [p for p in find(s, "property") if len(p) > 2]
            refs = [p[2] for p in props if p[1] == "Reference"]
            if not refs or not find1(s, "uuid"):
                continue
            self.symuuid[refs[0]] = find1(s, "uuid")[1]
            d = find1(s, "dnp")
            if d and str(d[1]) == "yes":
                self.dnp.add(refs[0])

    # ------------------------------------------------------------ nets
    def net(self, name):
        """The NETINFO_ITEM of a net (made on first use)."""
        if name not in self.nets:
            ni = self.board.FindNet(name)
            if ni is None:
                ni = pcbnew.NETINFO_ITEM(self.board, name)
                self.board.Add(ni)
            self.nets[name] = ni
        return self.nets[name]

    def N(self, short):
        """Short net name ('REF1_Y', 'GND') -> full netlist name ('/REF1_Y', 'GND')."""
        names = set(self.padnet.values())
        for cand in (short, "/" + short):
            if cand in names:
                return cand
        raise KeyError(short)

    # ------------------------------------------------------------ parts
    def place(self, ref, x, y, rot=0, show_ref=True):
        """Load the netlist footprint of ref from the library and put it at (x, y, rot).

        Why from the library: the board copy then equals the library copy, so DRC
        has no lib_footprint_mismatch items (the rev C problem)."""
        lib, name = self.comps[ref]["fp"].split(":")
        folder = os.path.join(self.prj, f"{lib}.pretty") if lib == "HERON_Clock" else os.path.join(self.fplib, f"{lib}.pretty")
        fp = pcbnew.FootprintLoad(folder, name)
        if fp is None:
            raise SystemExit(f"footprint {lib}:{name} not found in {folder}")
        fp.SetFPID(pcbnew.LIB_ID(lib, name))
        fp.SetReference(ref)
        fp.SetValue(self.comps[ref]["value"])
        fp.SetPath(pcbnew.KIID_PATH("/" + self.symuuid[ref]))
        for k, v in self.comps[ref]["fields"].items():
            fp.SetField(k, v)
        for f in fp.GetFields():                 # data fields are for the BOM: keep them off the silk
            if f.GetName() not in ("Reference", "Value"):
                f.SetVisible(False)
        self.board.Add(fp)
        fp.SetPosition(P(x, y))
        fp.SetOrientationDegrees(rot)
        for p in fp.Pads():
            key = (ref, p.GetNumber())
            if key in self.padnet:
                p.SetNet(self.net(self.padnet[key]))
        fp.Reference().SetVisible(show_ref)
        fp.Reference().SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(0.8), pcbnew.FromMM(0.8)))
        fp.Reference().SetTextThickness(pcbnew.FromMM(0.12))
        fp.Value().SetVisible(False)
        if ref.startswith(("TP", "H", "FID")):
            fp.SetExcludedFromBOM(True)
            fp.SetExcludedFromPosFiles(True)
        if ref in self.dnp:
            fp.SetDNP(True)
            fp.SetExcludedFromBOM(True)
            fp.SetExcludedFromPosFiles(True)
        self.fps[ref] = fp
        return fp

    def place_dir(self, ref, x, y, pin1, show_ref=False):
        """Place a 2-pin part centred at (x, y) with pad 1 toward pin1 ('L', 'R', 'U', 'D')."""
        want = {"L": (-1, 0), "R": (1, 0), "U": (0, -1), "D": (0, 1)}[pin1]
        fp = self.place(ref, x, y, 0, show_ref)
        best = None
        for rot in (0, 90, 180, 270):
            fp.SetOrientationDegrees(rot)
            px, py = self.pad(ref, "1")
            score = (px - x) * want[0] + (py - y) * want[1]
            if best is None or score > best[0]:
                best = (score, rot)
        fp.SetOrientationDegrees(best[1])
        return fp

    def pad_obj(self, ref, num):
        """The first pad of ref with this number. (FindPadByNumber gives a raw
        SWIG pointer in KiCad 10, so search the pad list.)"""
        return next(p for p in self.fps[ref].Pads() if p.GetNumber() == str(num))

    @staticmethod
    def pad_xy(p):
        """Board-local centre of a pad object."""
        return L(p.GetPosition())

    def pad(self, ref, num):
        """Board-local centre of a pad."""
        return L(self.pad_obj(ref, num).GetPosition())

    def courtyard(self, ref):
        """(xmin, ymin, xmax, ymax) of the front courtyard, board-local."""
        bb = self.fps[ref].GetCourtyard(pcbnew.F_CrtYd).BBox()
        return (pcbnew.ToMM(bb.GetLeft()) - OX, pcbnew.ToMM(bb.GetTop()) - OY,
                pcbnew.ToMM(bb.GetRight()) - OX, pcbnew.ToMM(bb.GetBottom()) - OY)

    # ------------------------------------------------------------ copper
    def track(self, netname, pts, w=W_SIG, layer=F):
        """A chain of tracks through pts. A point may be (ref, pad) instead of (x, y)."""
        pts = [self.pad(*p) if isinstance(p[0], str) else p for p in pts]
        for a, b in zip(pts, pts[1:]):
            if a == b:
                continue
            t = pcbnew.PCB_TRACK(self.board)
            t.SetStart(P(*a)); t.SetEnd(P(*b)); t.SetWidth(pcbnew.FromMM(w))
            t.SetLayer(layer); t.SetNet(self.net(netname))
            self.board.Add(t)

    def via(self, netname, xy):
        """A through via (project default size)."""
        v = pcbnew.PCB_VIA(self.board)
        v.SetPosition(P(*xy)); v.SetDrill(pcbnew.FromMM(VIA_H)); v.SetWidth(pcbnew.FromMM(VIA_D))
        v.SetViaType(pcbnew.VIATYPE_THROUGH); v.SetLayerPair(F, B)
        v.SetNet(self.net(netname))
        self.board.Add(v)
        return xy

    def gv(self, ref, num, dx, dy, w=W_GND):
        """Short track from a GND pad to a GND via at (dx, dy) from the pad centre."""
        x, y = self.pad(ref, num)
        v = (round(x + dx, 3), round(y + dy, 3))
        self.track("GND", [(x, y), v], w)
        self.via("GND", v)
        return v

    # ------------------------------------------------------------ outline and areas
    def edge_line(self, a, b):
        s = pcbnew.PCB_SHAPE(self.board)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(P(*a)); s.SetEnd(P(*b))
        s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(pcbnew.FromMM(0.1))
        self.board.Add(s)

    def edge_arc(self, a, mid, b):
        s = pcbnew.PCB_SHAPE(self.board)
        s.SetShape(pcbnew.SHAPE_T_ARC)
        s.SetArcGeometry(P(*a), P(*mid), P(*b))
        s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(pcbnew.FromMM(0.1))
        self.board.Add(s)

    def edge_path(self, pts, r):
        """Closed outline through pts (a polygon), with each corner rounded by
        radius r when the corner entry in pts is (x, y, True)."""
        n = len(pts)
        segs = []
        for i in range(n):
            x, y = pts[i][0], pts[i][1]
            rnd = len(pts[i]) > 2 and pts[i][2]
            px, py = pts[i - 1][0], pts[i - 1][1]
            nx, ny = pts[(i + 1) % n][0], pts[(i + 1) % n][1]
            if not rnd:
                segs.append(((x, y), (x, y), None))
                continue
            u = ((px - x) / math.hypot(px - x, py - y), (py - y) / math.hypot(px - x, py - y))
            v = ((nx - x) / math.hypot(nx - x, ny - y), (ny - y) / math.hypot(nx - x, ny - y))
            a = (x + u[0] * r, y + u[1] * r)
            b = (x + v[0] * r, y + v[1] * r)
            c = (x + (u[0] + v[0]) * r, y + (u[1] + v[1]) * r)       # arc centre (90 degree corners)
            m = (c[0] - (u[0] + v[0]) * r / math.sqrt(2), c[1] - (u[1] + v[1]) * r / math.sqrt(2))
            segs.append((a, b, m))
        for i in range(n):
            a, b, m = segs[i]
            if m is not None:
                self.edge_arc(a, m, b)
            self.edge_line(segs[i][1], segs[(i + 1) % n][0])

    def rule_area(self, box, layers, tracks=True, vias=True, pads=True, fills=True, footprints=True, name=""):
        """A keep-out rule area (box = x0, y0, x1, y1) on the given copper layers."""
        z = pcbnew.ZONE(self.board)
        z.SetIsRuleArea(True)
        z.SetDoNotAllowTracks(tracks); z.SetDoNotAllowVias(vias); z.SetDoNotAllowPads(pads)
        z.SetDoNotAllowZoneFills(fills); z.SetDoNotAllowFootprints(footprints)
        ls = pcbnew.LSET()
        for layer in layers:
            ls.AddLayer(layer)
        z.SetLayerSet(ls)
        if name:
            z.SetZoneName(name)
        ol = z.Outline(); ol.NewOutline()
        x0, y0, x1, y1 = box
        for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            ol.Append(pcbnew.FromMM(OX + x), pcbnew.FromMM(OY + y))
        self.board.Add(z)
        return z

    def zone(self, layer, netname, prio=0, clearance=0.25):
        """A full-board copper pour. SMD pads do not join the pour (each gets a via)."""
        z = pcbnew.ZONE(self.board)
        z.SetLayer(layer)
        z.SetNet(self.net(netname))
        ol = z.Outline(); ol.NewOutline()
        m = 0.3
        for x, y in ((m, m), (self.bw - m, m), (self.bw - m, self.bh - m), (m, self.bh - m)):
            ol.Append(pcbnew.FromMM(OX + x), pcbnew.FromMM(OY + y))
        z.SetAssignedPriority(prio)
        z.SetLocalClearance(pcbnew.FromMM(clearance))
        z.SetMinThickness(pcbnew.FromMM(0.2))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THT_THERMAL)
        z.SetThermalReliefGap(pcbnew.FromMM(0.3))
        z.SetThermalReliefSpokeWidth(pcbnew.FromMM(0.4))
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        self.board.Add(z)
        return z

    def text(self, s, x, y, size=1.0, layer=pcbnew.F_SilkS, angle=0, mirrored=False):
        t = pcbnew.PCB_TEXT(self.board)
        t.SetText(s); t.SetPosition(P(x, y)); t.SetLayer(layer)
        t.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(size), pcbnew.FromMM(size)))
        t.SetTextThickness(pcbnew.FromMM(max(0.12, size * 0.15)))
        t.SetTextAngleDegrees(angle)
        t.SetMirrored(mirrored)
        self.board.Add(t)
        return t

    # ------------------------------------------------------------ automatic GND vias
    def obstacles(self):
        """Copper of every net: pads (boxes), vias (circles), tracks (segments)."""
        obs = []
        for fp in self.board.GetFootprints():
            for p in fp.Pads():
                bb = p.GetBoundingBox()
                obs.append(("pad", p.GetNetname(), (pcbnew.ToMM(bb.GetX()) - OX, pcbnew.ToMM(bb.GetY()) - OY,
                                                    pcbnew.ToMM(bb.GetRight()) - OX, pcbnew.ToMM(bb.GetBottom()) - OY),
                            p.GetDrillSize().x > 0))
        for t in self.board.GetTracks():
            if t.GetClass() == "PCB_VIA":
                obs.append(("via", t.GetNetname(), (L(t.GetPosition()), pcbnew.ToMM(t.GetWidth(F)) / 2), True))
            else:
                obs.append(("seg", t.GetNetname(), (L(t.GetStart()), L(t.GetEnd()), pcbnew.ToMM(t.GetWidth()) / 2,
                                                    t.GetLayer()), False))
        return obs

    def in_rule_area(self, p, r):
        """True when a circle (p, r) touches a via keep-out rule area."""
        for z in self.board.Zones():
            if not (z.GetIsRuleArea() and z.GetDoNotAllowVias()):
                continue
            bb = z.GetBoundingBox()
            x0, y0 = pcbnew.ToMM(bb.GetX()) - OX, pcbnew.ToMM(bb.GetY()) - OY
            x1, y1 = pcbnew.ToMM(bb.GetRight()) - OX, pcbnew.ToMM(bb.GetBottom()) - OY
            if x0 - r < p[0] < x1 + r and y0 - r < p[1] < y1 + r:
                return True
        return False

    def via_ok(self, p, netname, obs, clr=0.2, start=None, w=W_GND):
        """True when a via of netname fits at p (and a stub from start to p)."""
        x, y = p
        r = VIA_D / 2
        if not (0.3 + r < x < self.bw - 0.3 - r and 0.3 + r < y < self.bh - 0.3 - r):
            return False
        if self.in_rule_area(p, r):
            return False
        for kind, nn, g, drilled in obs:
            if kind == "pad":
                hole_clr = 0.3 if drilled else 0.0
                if nn != netname or drilled:
                    c = clr + hole_clr
                    if g[0] - r - c < x < g[2] + r + c and g[1] - r - c < y < g[3] + r + c:
                        return False
                if nn != netname and start is not None:
                    cx, cy = (g[0] + g[2]) / 2, (g[1] + g[3]) / 2
                    hx, hy = (g[2] - g[0]) / 2, (g[3] - g[1]) / 2
                    for t in (0.2, 0.4, 0.6, 0.8, 1.0):
                        qx, qy = start[0] + t * (x - start[0]), start[1] + t * (y - start[1])
                        if abs(qx - cx) < hx + w / 2 + clr and abs(qy - cy) < hy + w / 2 + clr:
                            return False
            elif kind == "via":
                need = g[1] + r + (0.25 if nn == netname else clr)
                if math.hypot(x - g[0][0], y - g[0][1]) < need:
                    return False
            elif nn != netname:
                if seg_dist(p, g[0], g[1]) < g[2] + r + clr:
                    return False
                if start is not None and g[3] == F:
                    for t in (0.2, 0.4, 0.6, 0.8):
                        q = (start[0] + t * (x - start[0]), start[1] + t * (y - start[1]))
                        if seg_dist(q, g[0], g[1]) < g[2] + w / 2 + clr:
                            return False
        return True

    def gnd_pad_vias(self, skip=()):
        """Give every SMD GND pad without a GND track a short stub and a via."""
        gtracks = [(L(t.GetStart()), L(t.GetEnd())) for t in self.board.GetTracks()
                   if t.GetNetname() == "GND" and t.GetClass() != "PCB_VIA"]
        added, failed = 0, []
        for fp in self.board.GetFootprints():
            if fp.GetReference() in skip:
                continue
            for p in fp.Pads():
                if p.GetNetname() != "GND" or p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                    continue
                c = L(p.GetPosition())
                if any(math.hypot(c[0] - a[0], c[1] - a[1]) < 0.05 or math.hypot(c[0] - b[0], c[1] - b[1]) < 0.05
                       for a, b in gtracks):
                    continue
                obs = self.obstacles()
                fc = L(fp.GetPosition())
                d = (c[0] - fc[0], c[1] - fc[1]); n = math.hypot(*d)
                dirs = [(d[0] / n, d[1] / n)] if n > 0.05 else []
                dirs += [(1, 0), (-1, 0), (0, 1), (0, -1), (0.7, 0.7), (-0.7, 0.7), (0.7, -0.7), (-0.7, -0.7)]
                bb = p.GetBoundingBox()
                half = max(pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight())) / 2
                done = False
                for dist in (half + 0.5, half + 0.75, half + 1.0, half + 1.4, half + 1.9):
                    for u in dirs:
                        v = (round(c[0] + u[0] * dist, 3), round(c[1] + u[1] * dist, 3))
                        if self.via_ok(v, "GND", obs, start=c):
                            self.track("GND", [c, v], W_GND); self.via("GND", v)
                            gtracks.append((c, v)); added += 1; done = True
                            break
                    if done:
                        break
                if not done:
                    failed.append(f"{fp.GetReference()}.{p.GetNumber()}")
        return added, failed

    def stitch(self, pitch=2.5, margin=1.2):
        """GND stitching vias on a grid wherever the copper of other nets, the
        footprints and the keep-outs leave room."""
        boxes = []
        for fp in self.board.GetFootprints():
            bb = fp.GetBoundingBox(False)
            boxes.append((pcbnew.ToMM(bb.GetX()) - OX - 0.45, pcbnew.ToMM(bb.GetY()) - OY - 0.45,
                          pcbnew.ToMM(bb.GetRight()) - OX + 0.45, pcbnew.ToMM(bb.GetBottom()) - OY + 0.45))
        segs, vias = [], []
        for t in self.board.GetTracks():
            if t.GetClass() == "PCB_VIA":
                vias.append((L(t.GetPosition()), pcbnew.ToMM(t.GetWidth(F)) / 2))
            else:
                segs.append((L(t.GetStart()), L(t.GetEnd()), pcbnew.ToMM(t.GetWidth()) / 2))
        n = 0
        y = margin
        while y <= self.bh - margin:
            x = margin
            while x <= self.bw - margin:
                p = (x, y)
                ok = not any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in boxes)
                ok = ok and not self.in_rule_area(p, VIA_D / 2 + 0.05)
                ok = ok and all(seg_dist(p, a, b) > r + VIA_D / 2 + 0.25 for a, b, r in segs)
                ok = ok and all(math.hypot(x - v[0][0], y - v[0][1]) > v[1] + VIA_D / 2 + 0.35 for v in vias)
                if ok:
                    self.via("GND", (round(x, 3), round(y, 3)))
                    vias.append(((x, y), VIA_D / 2)); n += 1
                x += pitch
            y += pitch
        return n


# ------------------------------------------------------------------ Freerouting
def autoroute(path, jar, passes=60, timeout=3000):
    """Export the board as DSN, route it with Freerouting, and import the result.

    The hand routes are locked first, so Freerouting keeps them. In1 is set
    as a power layer for the export, so Freerouting does not route on the GND
    plane. Return (segments, vias) added."""
    b = pcbnew.LoadBoard(path)
    b.SetLayerType(IN1, pcbnew.LT_POWER)
    for t in b.GetTracks():
        t.SetLocked(True)
    base = os.path.splitext(path)[0]
    dsn, ses = base + ".dsn", base + ".ses"
    if os.path.exists(ses):
        os.remove(ses)
    if not pcbnew.ExportSpecctraDSN(b, dsn):
        raise SystemExit("DSN export failed")
    cmd = ["java", "-jar", jar, "-de", dsn, "-do", ses, "-mp", str(passes), "-inc", "GND"]
    if os.environ.get("DISPLAY") is None and os.path.exists("/usr/bin/xvfb-run"):
        cmd = ["xvfb-run", "-a"] + cmd                 # Linux without a display (rev B flow)
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
    if not os.path.exists(ses):
        raise SystemExit("Freerouting produced no session file")
    added = import_ses(b, ses)
    b.SetLayerType(IN1, pcbnew.LT_SIGNAL)
    for t in b.GetTracks():
        t.SetLocked(False)
    pcbnew.SaveBoard(path, b)
    return added


def import_ses(b, ses):
    """Add the wires and vias of a Freerouting session that the board does not have yet."""
    sx = parse(open(ses).read())
    routes = find1(sx, "routes")
    res = find1(routes, "resolution")
    scale = 1.0 / (float(res[2]) * (1000.0 if res[1] == "um" else 1.0))   # session units -> mm
    netcode = {ni.GetNetname(): ni for ni in b.GetNetInfo().NetsByNetcode().values()}
    layers = {b.GetLayerName(lay): lay for lay in (F, IN1, IN2, B)}
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
        ni = netcode.get(netx[1])
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
