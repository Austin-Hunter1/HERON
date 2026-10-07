"""
HERON payload rack, Rev A (concept) -- parametric generator (CadQuery).

Blade layout: every module (NUC, 4x SDR) is a vertical "blade" on a
skeletal laser-cut tray. TPU C-rails clip onto the tray's top and bottom edges
and slide in printed ASA rail strips bolted to the top and bottom plates.
Downwash flows straight down between blades; forward-flight air flows front to
back. No fan.

World frame (mm): X = across blades (left -> right), Y = depth (front face
y=0 -> rear), Z = up. Bottom plate top surface is z=0. Left wall inner face x=0.

Run:  python3 heron_rack.py          -> writes out/ (STEP, DXF, STL, mass.csv)
Every number marked VERIFY is a placeholder to measure on the real part.
"""
import math, os, csv, sys
import cadquery as cq
from OCP.gp import gp_Trsf

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
REF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ref")

# ---------------------------------------------------------------- parameters
T      = 3.0     # laser-cut sheet thickness
KERF   = 0.15    # slot clearance per side for tab/slot joints
H      = 120.0   # interior height (bottom plate top -> top plate underside)

# Ettus B200/B210 board (from the Ettus STEP models, board-only CCA)
SDR_W, SDR_L, PCB_T = 99.949, 154.686, 1.575
SDR_TOP = 14.125          # tallest part above board bottom (USB-B)
SDR_HOLES = [(3.17, -20.19), (96.77, -20.19), (3.17, -140.21), (96.77, -140.21)]  # (bx, by), d3.18
SDR_REAR_SMA = {"ref_a": 14.22, "ref_b": 34.16, "aux": 70.89}   # bx of rear SMAs; which is 10 MHz / PPS: VERIFY on silkscreen
SDR_USB_BX, SDR_PWR_BX = 51.69, 89.43
SMA_BZ = 1.96             # SMA axis height above board bottom
SMA_OVERHANG = 11.48      # SMA tip beyond board edge

# Intel NUC7i3DNB board: 101.6 x 101.6. Hole pattern + top height are VERIFY.
NUC_S   = 101.6
NUC_TOP = 25.0            # VERIFY: tallest part (cooler) above board bottom
NUC_HOLES = [(5.0, 5.0), (96.6, 5.0), (5.0, 96.6), (96.6, 96.6)]  # VERIFY (from board corner)

# 2.5" SATA SSD (SFF-8201)
SSD_L, SSD_W, SSD_T = 100.45, 69.85, 7.0      # use 9.5 for thick drives
SSD_HOLE_IN = 4.07                            # bottom holes inset from long sides
SSD_HOLE_Y  = [(9.85, 14.0), (86.45, 90.6)]   # slot spans cover both SFF datum readings -- VERIFY on drive
SSD_CONN_C  = 24.0        # VERIFY: SATA connector centre from drive long edge
SSD_CONN_W, SSD_CONN_H = 45.0, 12.0           # pocket for the 22-pin receptacle adapter (VERIFY with your part)
SLED_T   = 2.0            # printed sled plate on the drive's bottom (hole) face
SSD_CLR  = 0.3            # clearance per side in the guide channels
SSD_BASE = 13.0           # cage base height: receptacle adapter + cable exit
SSD_Y0_MAX = 136.0        # drive front edge, upper limit (behind the NUC: room for NUC rear I/O and the SATA cable)
SSD_REAR_GAP = 1.0        # PROPOSED: minimum gap, SSD cage rear face to rear plate front face
SSD_XC   = 23.0           # drive + sled centre plane (middle of the NUC slot)

STANDOFF = 6.0            # tray face -> board bottom (M3 nylon standoffs)
Y_BOARD  = 3.0            # board front edge
Z_BOARD  = 9.0            # board lower edge (clears rail strip ribs at z=8)

# rail strip (printed ASA-CF) and TPU C-rail
STRIP_BASE, RIB_H, RIB_T = 2.0, 6.0, 2.5
RAIL_W  = 6.0             # TPU rail overall width (tray 3 + 2x1.5 lips)
# These two clearances set the radial (X, Z) position of every blade. The SMP
# direct SMP mate needs a small radial error, so both are tight. The NUC blade uses
# the same strip code, so it gets the same values.
CHAN_CLR      = 0.2       # PROPOSED (was 0.4): total clearance, strip channel to TPU rail
RAIL_BASE_GAP = 0.1       # PROPOSED (was 0.2): Z gap, TPU rail back to strip base
CHAN_W  = RAIL_W + CHAN_CLR   # channel width
RAIL_BACK, RAIL_GRIP = 2.0, 3.0
STRIP_W = CHAN_W + 2 * RIB_T          # 11.2
# The tray centre stays 5.7 mm from the slot start. The clock board jacks are
# placed for this value (it was STRIP_W / 2 with the old 0.4 mm clearance).
# A change here moves the SDR axes away from the jacks.
TRAY_OFF = 5.7                       # tray centre from slot start
TRAY_Z0 = STRIP_BASE + RAIL_BASE_GAP + RAIL_BACK   # 4.1
TRAY_Z1 = H - TRAY_Z0
LATCH_Z = STRIP_BASE + RIB_H / 2 + 0.5   # height of the latch pivot; the tray stop and preload screws use it too

# SDR tray axial stop (PROPOSED). There is no bullet, so there is no axial
# float. The mate itself is the axial stop: the thumbscrew seats both
# connectors. An M3 set screw in the rear stop block of each SDR rail strip is
# only a backup. It stops the tray STOP_MATE_MARGIN behind the fully mated
# position, so the thumbscrew cannot overload the jacks.
INSERT_D       = 4.0      # hole for an M3 heat-set insert (same as the strip ears)
STOP_ADJ       = 1.5      # PROPOSED: adjust range of the rear stop screw, +/- mm about the nominal screw tip
STOP_MATE_MARGIN = 0.3    # PROPOSED: the stop screw tip is this far behind the fully mated tray rear end (+Y)
STOP_INSERT_L  = 5.5      # M3 heat-set insert length (same as the strip ears)
STOP_WALL      = 2.5      # PROPOSED: block material behind the insert
STOP_BLOCK_L   = STOP_INSERT_L + STOP_WALL   # length of the rear stop block along Y
STOP_BLOCK_H   = 10.0     # PROPOSED: block top height. It is behind the SDR board, so the 9 mm board clearance does not apply.
STOP_SCREW_CLR = 3.4      # clearance hole for the M3 set screw (screw enters from the rear)
LATCH_L        = 15.0     # latch arm length
LATCH_L_PRE    = 16.0     # arm length when it carries the preload screw (boss diameter 8)
PRE_BOSS_D     = 8.0      # PROPOSED: preload boss diameter (equals the latch width)
PRE_BOSS_T     = 7.5      # PROPOSED: preload boss thickness along Y (insert pocket 5.5 + 2 front wall)

# slots (name, kind, pitch)
SLOTS = [("NUC", "nuc", 42.0),
         ("B210_1", "b210", 30.0), ("B210_2", "b210", 30.0),
         ("B200_1", "b200", 30.0), ("B200_2", "b200", 30.0)]
MARGIN = 2.0
W = MARGIN + sum(p for _, _, p in SLOTS) + MARGIN          # interior width
TRAY_Y0 = 0.5
TRAY_Y1 = Y_BOARD + SDR_L                                  # 157.69: tray rear end when fully mated
STOP_TIP_Y = TRAY_Y1 + STOP_MATE_MARGIN                    # nominal stop screw tip: the margin is applied here
STRIP_Y1 = STOP_TIP_Y + STOP_ADJ + STOP_BLOCK_L               # closed rear end of the SDR strip (stop block)
NUC_TRAY_Y1 = Y_BOARD + NUC_S + 3.0                      # NUC front-aligned, short blade
NUC_STRIP_Y1 = NUC_TRAY_Y1 + 0.5 + 3.0

# ---- SMP direct mate stack-up along Y (all distances in mm) ----
# The clock board jacks are SMP male, smooth bore (Amphenol SMP-MSSB-PCT10T).
# Each SDR rear SMA jack carries ONE adapter: Cinch/Johnson 134-1019-451,
# SMA plug to SMP female. The adapter plugs directly onto the board jack.
# There is no bullet. One change here moves the board, the rear plate and
# the walls.
SDR_SMA_TIP_Y = Y_BOARD + SDR_L + SMA_OVERHANG   # plane of the SDR SMA jack tips
# VERIFY: placeholder. It is estimated from the 14.25 mm overall length of the
# adapter, minus the SMA thread overlap and the SMP insertion depth. Measure it
# on a real B210 with the adapter fitted and mated on a jack.
ADAPTER_REACH = 9.0       # SDR SMA tip plane to the board jack mating face, adapter fully mated
# Source: Cinch 134-1019-451 drawing, hex 5.54 mm across flats. The body is
# modelled as a cylinder on the across-corners diameter, so it never under-sizes the hex.
ADAPTER_D     = 5.54 / math.cos(math.radians(30))   # = 6.40
SMP_JACK_H    = 4.09      # board front face to the jack mating face (Amphenol drawing, SMP-MSSB-PCT10T)
CLK_Y0 = SDR_SMA_TIP_Y + ADAPTER_REACH + SMP_JACK_H   # clock board front face
# Clock board standoffs (2026-10-07, option 1). The direct mate moved the
# board 11 mm closer to the SDRs. Longer standoffs keep the rear plate and the
# SSD bay where they were, so the NUC cables keep about 20 mm to cross behind
# the NUC, and the SDR rear USB/power plugs keep about 45 mm to the rear plate.
CLK_STANDOFF = 20.0       # PROPOSED: M3 F-F standoff length, board rear face -> rear plate (a standard length)
REAR_Y0 = CLK_Y0 + 1.6 + CLK_STANDOFF   # rear plate front face
D = REAR_Y0 + T + 3.0     # overall depth (walls/plates)
# The SSD bay must fit between the NUC and the rear plate. The SMP stack-up
# made the rack shorter, so the bay moves forward when 136 mm no longer fits.
SSD_Y0 = min(SSD_Y0_MAX, REAR_Y0 - SSD_REAR_GAP - (SSD_W + 2 * (SSD_CLR + 2.0)))   # drive front edge

# Rear plate: soldering windows (the jacks are soldered from the back with the SDRs mated)
SOLDER_WIN_DX   = 7.0     # PROPOSED: window half width about the jack axis (X)
SOLDER_WIN_DZ   = 6.0     # PROPOSED: window margin above the upper jack and below the lower jack (Z)
CLK_HOLE_KEEP   = 4.0     # PROPOSED: minimum material around each board mounting hole
CLK_HOLE_D      = 3.4     # clock board mounting hole in the rear plate
# Rear plate: access holes for the rear stop screws. The screws are on the
# strip axis, under the clock board (bottom strips) and above it (top strips).
# A long hex key goes in through the rear plate along -Y. The key for an M3
# set screw is 1.5 mm hex; the hole also takes a 3 mm screwdriver shaft.
STOP_ACCESS_D   = 4.0     # PROPOSED: access hole diameter in the rear plate
STOP_KEY_D      = 3.0     # tool shaft diameter that check_fit.py proves has a clear path

def slot_x0(i):
    return MARGIN + sum(p for _, _, p in SLOTS[:i])
def tray_x(i):
    return slot_x0(i) + TRAY_OFF   # tray centre plane

# ---------------------------------------------------------------- helpers
def place(shape, origin, u, v, w):
    t = gp_Trsf()
    t.SetValues(u[0], v[0], w[0], origin[0],
                u[1], v[1], w[1], origin[1],
                u[2], v[2], w[2], origin[2])
    return shape.moved(cq.Location(t))

def box(x0, y0, z0, x1, y1, z1):
    return cq.Solid.makeBox(x1 - x0, y1 - y0, z1 - z0, cq.Vector(x0, y0, z0))

def rrect(cx, cy, w, h, r, z0=-5, z1=10):
    r = min(r, w / 2 - 0.01, h / 2 - 0.01)
    s = cq.Sketch().rect(w, h).vertices().fillet(r)
    return (cq.Workplane("XY").workplane(offset=z0).center(cx, cy)
            .placeSketch(s).extrude(z1 - z0).val())

def circ(cx, cy, d, z0=-5, z1=10):
    return cq.Solid.makeCylinder(d / 2, z1 - z0, cq.Vector(cx, cy, z0))

def slot2d(cx, cy, length, d, z0=-5, z1=10):
    """stadium slot along x"""
    s = cq.Sketch().slot(length, d)
    return (cq.Workplane("XY").workplane(offset=z0).center(cx, cy)
            .placeSketch(s).extrude(z1 - z0).val())

def plate(x0, y0, x1, y1, adds=(), cuts=(), t=T):
    s = box(x0, y0, 0, x1, y1, t)
    for a in adds:
        s = s.fuse(a)
    if cuts:
        c = cuts[0]
        for k in cuts[1:]:
            c = c.fuse(k)
        s = s.cut(c)
    return s.clean()

def tslot(cx, cy, dirn, depth=14.0):
    """T-slot for M3 screw + square nut, entering from an edge.
    (cx,cy) on the edge; dirn = +1/-1 along y (into the part)."""
    yy = cy + dirn * depth / 2
    c = box(cx - 1.6, min(cy, cy + dirn * depth) - 0.01, -5, cx + 1.6, max(cy, cy + dirn * depth) + 0.01, 10)
    ny0 = cy + dirn * 6.0
    ny1 = cy + dirn * 8.7
    c = c.fuse(box(cx - 2.85, min(ny0, ny1), -5, cx + 2.85, max(ny0, ny1), 10))
    return c

def tslot_x(cx, cy, dirn, depth=14.0):
    """same, entering along x"""
    c = box(min(cx, cx + dirn * depth) - 0.01, cy - 1.6, -5, max(cx, cx + dirn * depth) + 0.01, cy + 1.6, 10)
    nx0, nx1 = cx + dirn * 6.0, cx + dirn * 8.7
    return c.fuse(box(min(nx0, nx1), cy - 2.85, -5, max(nx0, nx1), cy + 2.85, 10))

def grid_windows(x0, y0, x1, y1, nx, ny, rib, r=4.0):
    out = []
    cw = (x1 - x0 - rib * (nx - 1)) / nx
    ch = (y1 - y0 - rib * (ny - 1)) / ny
    for i in range(nx):
        for j in range(ny):
            cx = x0 + cw / 2 + i * (cw + rib)
            cy = y0 + ch / 2 + j * (ch + rib)
            if cw > 2 * r + 1 and ch > 2 * r + 1:
                out.append(rrect(cx, cy, cw, ch, r))
    return out

# ---------------------------------------------------------------- joints
WALL_TABS_Y   = [25.0, 105.0, 185.0]     # tab centres along wall bottom/top edge
WALL_SCREWS_Y = [65.0, 145.0]
TAB_L = 20.0
REAR_TABS_X   = [W * 0.22, W * 0.78]
# The rear plate screw sits midway between the 2nd and 3rd SDR trays. Why:
# at W/2 its T-slot nut pocket cut into the rear stop access hole of slot 2.
_SDR_I = [i for i, sl in enumerate(SLOTS) if sl[1] in ("b210", "b200")]
REAR_SCREWS_X = [MARGIN + sum(p for _, _, p in SLOTS[:_SDR_I[2]]) + TRAY_OFF - SLOTS[_SDR_I[1]][2] / 2]
REAR_SIDE_TABS_Z = [30.0, 90.0]
REAR_SIDE_SCREW_Z = [60.0]

# ---------------------------------------------------------------- strips
EAR_Y = [(0.0, 14.0), (75.0, 85.0), (145.0, 155.0)]
NUC_EAR_Y = [(0.0, 14.0), (50.0, 60.0), (95.0, 105.0)]

def strip_local(length, ears, sdr=False):
    """rail strip in local coords: x across (centred), y along, z up from plate.
    sdr=True gives the rear stop block a set screw. The tip of that screw is a backup
    stop for the SDR tray (see STOP_TIP_Y). The NUC strip stays plain."""
    w2 = STRIP_W / 2
    s = box(-w2, 0, 0, w2, length, STRIP_BASE)
    s = s.fuse(box(-w2, 0, STRIP_BASE, -w2 + RIB_T, length, STRIP_BASE + RIB_H))
    s = s.fuse(box(w2 - RIB_T, 0, STRIP_BASE, w2, length, STRIP_BASE + RIB_H))
    if sdr:
        # Long, tall stop block: it must hold an M3 heat-set insert. It sits
        # behind the SDR board, so it can be taller than the ribs.
        s = s.fuse(box(-w2, length - STOP_BLOCK_L, STRIP_BASE, w2, length, STOP_BLOCK_H))
    else:
        s = s.fuse(box(-w2, length - 3.0, STRIP_BASE, w2, length, STRIP_BASE + RIB_H))   # rear stop
    holes = []
    for k, (e0, e1) in enumerate(ears):
        e1 = min(e1, length)
        s = s.fuse(box(w2, e0, 0, w2 + 6.0, e1, STRIP_BASE + RIB_H))
        hy = (e0 + e1) / 2 if k else 10.0
        holes.append(cq.Solid.makeCylinder(2.0, 5.5, cq.Vector(w2 + 3.0, hy, 0)))   # M3 heat-set from plate side
    # latch pivot insert on front face of first ear (along +y)
    holes.append(cq.Solid.makeCylinder(2.0, 6.0, cq.Vector(w2 + 3.0, 0, STRIP_BASE + RIB_H / 2 + 0.5), cq.Vector(0, 1, 0)))
    if sdr:
        # The insert goes in from the FRONT face of the block. The tray pushes
        # the screw backward, so the load presses the insert into its pocket.
        # The set screw enters from the rear through the clearance hole.
        y0 = length - STOP_BLOCK_L
        up = cq.Vector(0, 1, 0)
        holes.append(cq.Solid.makeCylinder(INSERT_D / 2, STOP_INSERT_L + 0.01, cq.Vector(0, y0 - 0.01, LATCH_Z), up))
        holes.append(cq.Solid.makeCylinder(STOP_SCREW_CLR / 2, STOP_BLOCK_L + 0.02, cq.Vector(0, y0 - 0.01, LATCH_Z), up))
    for hh in holes:
        s = s.cut(hh)
    return s.clean()

def strip_hole_xy(i, kind):
    """plate screw positions (x,y) for slot i's strip ears"""
    xs = tray_x(i) + STRIP_W / 2 + 3.0
    ears = NUC_EAR_Y if kind == "nuc" else EAR_Y
    out = []
    for k, (e0, e1) in enumerate(ears):
        out.append((xs, (e0 + e1) / 2 if k else 10.0))
    return out

def tpu_rail_local(length):
    """C-rail profile in local x (across), z (up), extruded along y. Tray edge sits at z=RAIL_BACK."""
    s = box(-RAIL_W / 2, 0, 0, RAIL_W / 2, length, RAIL_BACK)
    lip = (RAIL_W - T) / 2
    s = s.fuse(box(-RAIL_W / 2, 0, RAIL_BACK, -RAIL_W / 2 + lip, length, RAIL_BACK + RAIL_GRIP))
    s = s.fuse(box(RAIL_W / 2 - lip, 0, RAIL_BACK, RAIL_W / 2, length, RAIL_BACK + RAIL_GRIP))
    return s.clean()

# ---------------------------------------------------------------- plates
def ssd_geom():
    """SSD bay geometry in world mm. The drive stands on its connector edge:
    100 mm along Z, 70 mm along Y, 7 mm along X. The sled plate is on the -X face."""
    sx0 = SSD_XC - (SSD_T + SLED_T) / 2          # sled outer face
    sx1 = sx0 + SLED_T + SSD_T                   # drive top-cover face
    y0, y1 = SSD_Y0, SSD_Y0 + SSD_W
    zd0 = SSD_BASE + 0.5                         # drive connector edge (mated)
    return dict(sx0=sx0, sx1=sx1, y0=y0, y1=y1, zd0=zd0, zd1=zd0 + SSD_L)

def ssd_cage_holes():
    """(x, y) of the four M3 screws that hold the SSD cage to both plates."""
    g = ssd_geom()
    ym = (g["y0"] + g["y1"]) / 2
    xl, xr = g["sx0"] - SSD_CLR - 2.0 - 5.0, g["sx1"] + SSD_CLR + 2.0 + 5.0
    return [(xl, ym - 22.0), (xl, ym + 22.0), (xr, ym - 22.0), (xr, ym + 22.0)]

def ssd_cage_keep(m=3.0):
    """plate area under the cage that must stay solid (no airflow windows)."""
    g = ssd_geom()
    xs = [h[0] for h in ssd_cage_holes()]
    return box(min(xs) - 5.0 - m, g["y0"] - SSD_CLR - 2.0 - m, -5, max(xs) + 5.0 + m, g["y1"] + SSD_CLR + 2.0 + m, 10)

def ssd_latch_xy():
    g = ssd_geom()
    return (g["sx1"] + SSD_CLR + 6.0, (g["y0"] + g["y1"]) / 2)

def plate_local(top=False):
    """top / bottom plate, local z 0..T. Both have the same hole pattern; the
    top plate also has the SSD hatch and the hatch-latch pivot hole."""
    x0, x1 = -T, W + T
    cuts = []
    # side wall tab slots and screw holes
    for xw in (-T / 2, W + T / 2):
        for ty in WALL_TABS_Y:
            cuts.append(box(xw - T / 2 - KERF, ty - TAB_L / 2 - KERF, -5, xw + T / 2 + KERF, ty + TAB_L / 2 + KERF, 10))
        for sy in WALL_SCREWS_Y:
            cuts.append(circ(xw, sy, 3.4))
    # rear plate tabs and screws
    ry = REAR_Y0 + T / 2
    for tx in REAR_TABS_X:
        cuts.append(box(tx - TAB_L / 2 - KERF, ry - T / 2 - KERF, -5, tx + TAB_L / 2 + KERF, ry + T / 2 + KERF, 10))
    for sx in REAR_SCREWS_X:
        cuts.append(circ(sx, ry, 3.4))
    # strip screws
    for i, (_, kind, _) in enumerate(SLOTS):
        for (hx, hy) in strip_hole_xy(i, kind):
            cuts.append(circ(hx, hy, 3.4))
    # airflow windows between strips; their y ranges avoid the strip ears
    wins = []
    edges = [0.0]
    for i in range(len(SLOTS)):
        edges += [tray_x(i) - STRIP_W / 2, tray_x(i) + STRIP_W / 2]
    edges.append(W)
    for k in range(0, len(edges), 2):
        a, b = edges[k] + 2.0, edges[k + 1] - 2.0
        if b - a < 7:
            continue
        right_of_nuc = k == 2 and SLOTS[0][1] == "nuc"
        for (y0, y1) in ([(18, 46), (64, 92)] if right_of_nuc else [(18, 72), (88, 142)]):
            wins.append(rrect((a + b) / 2, (y0 + y1) / 2, b - a, y1 - y0, 3.0))
    # behind the short NUC blade (NUC cables + SSD bay)
    nuc_x1 = tray_x(1) - STRIP_W / 2 - 2.0
    wins += grid_windows(4.0, NUC_STRIP_Y1 + 4.0, nuc_x1, STRIP_Y1, 2, 1, 6.0)
    # full-width area behind the strips (cables + exhaust), split by ribs
    wins += grid_windows(6.0, STRIP_Y1 + 4.0, W - 6.0, REAR_Y0 - 4.0, 4, 1, 6.0)
    wu = wins[0]
    for w_ in wins[1:]:
        wu = wu.fuse(w_)
    cuts.append(wu.cut(ssd_cage_keep()))
    # SSD cage screws
    for (hx, hy) in ssd_cage_holes():
        cuts.append(circ(hx, hy, 3.4))
    if top:
        g = ssd_geom()
        cuts.append(box(g["sx0"] - 0.6, g["y0"] - 0.6, -5, g["sx1"] + 0.6, g["y1"] + 0.6, 10))   # hatch
        lx, ly = ssd_latch_xy()
        cuts.append(circ(lx, ly, 3.4))
    return plate(x0, 0, x1, D, cuts=cuts)

def side_wall_local():
    """local x = world Y (0..D), local y = world Z (0..H); tabs reach to -T and H+T."""
    adds = []
    cuts = []
    for ty in WALL_TABS_Y:
        adds.append(box(ty - TAB_L / 2, -T, 0, ty + TAB_L / 2, 0.01, T))
        adds.append(box(ty - TAB_L / 2, H - 0.01, 0, ty + TAB_L / 2, H + T, T))
    for sy in WALL_SCREWS_Y:
        cuts.append(tslot(sy, 0.0, +1))
        cuts.append(tslot(sy, H, -1))
    # rear-plate tab slots + screw hole
    ry = REAR_Y0 + T / 2
    for tz in REAR_SIDE_TABS_Z:
        cuts.append(box(ry - T / 2 - KERF, tz - 15 / 2 - KERF, -5, ry + T / 2 + KERF, tz + 15 / 2 + KERF, 10))
    for sz in REAR_SIDE_SCREW_Z:
        cuts.append(circ(ry, sz, 3.4))
    cuts += grid_windows(10.0, 18.0, REAR_Y0 - 8.0, H - 18.0, 4, 2, 7.0)
    return plate(0, 0, D, H, adds=adds, cuts=cuts)

def rear_plate_local():
    """local x = world X (0..W), local y = world Z (0..H)."""
    adds, cuts = [], []
    for tx in REAR_TABS_X:
        adds.append(box(tx - TAB_L / 2, -T, 0, tx + TAB_L / 2, 0.01, T))
        adds.append(box(tx - TAB_L / 2, H - 0.01, 0, tx + TAB_L / 2, H + T, T))
    for tz in REAR_SIDE_TABS_Z:
        adds.append(box(-T, tz - 7.5, 0, 0.01, tz + 7.5, T))
        adds.append(box(W - 0.01, tz - 7.5, 0, W + T, tz + 7.5, T))
    for sx in REAR_SCREWS_X:
        cuts.append(tslot(sx, 0.0, +1))
        cuts.append(tslot(sx, H, -1))
    for sz in REAR_SIDE_SCREW_Z:
        cuts.append(tslot_x(0.0, sz, +1))
        cuts.append(tslot_x(W, sz, -1))
    # clock board mounting holes
    for (hx, hz) in clk_holes():
        cuts.append(circ(hx, hz, CLK_HOLE_D))
    cx0, cx1, cz0, cz1 = clk_outline()
    # soldering windows: one behind each SDR jack column
    cuts += solder_windows()
    # access holes: one on the axis of each rear stop screw
    for (ax, az) in stop_axes():
        cuts.append(circ(ax, az, STOP_ACCESS_D))
    # windows: left of clock board (SSD/NUC cables), above it (SDR power), below it
    cuts += grid_windows(16.0, 18.0, cx0 - 6.0, H - 18.0, 1, 2, 8.0)
    cuts += grid_windows(cx0 + 6.0, cz1 + 6.0, W - 16.0, H - 18.0, 3, 1, 8.0)
    return plate(0, 0, W, H, adds=adds, cuts=cuts)

def stop_axes():
    """World (X, Z) of every rear stop screw axis: bottom and top strip of each SDR slot.
    The top strip is the bottom strip mirrored about Z = H/2 (see mirror_top)."""
    return [(tray_x(i), z) for i in sdr_indices() for z in (LATCH_Z, H - LATCH_Z)]

def solder_windows():
    """Windows in the rear plate (local coords: x = world X, y = world Z).
    The jacks are soldered from the back of the clock board with the SDRs
    already mated. The iron must reach both jacks of one column. A keep-out
    disc around each board mounting hole leaves enough material there."""
    out = []
    keep = [circ(hx, hz, CLK_HOLE_D + 2 * CLK_HOLE_KEEP) for hx, hz in clk_holes()]
    by_sdr = {}
    for m in mate_pairs():
        by_sdr.setdefault(m["sdr"], []).append(m)
    for name, ms in by_sdr.items():
        xc = sum(m["jack_xz"][0] for m in ms) / len(ms)
        z0 = min(m["jack_xz"][1] for m in ms) - SOLDER_WIN_DZ
        z1 = max(m["jack_xz"][1] for m in ms) + SOLDER_WIN_DZ
        w = rrect(xc, (z0 + z1) / 2, 2 * SOLDER_WIN_DX, z1 - z0, 2.0)
        for k in keep:
            w = w.cut(k)
        out.append(w)
    return out

# ---------------------------------------------------------------- clock board (Rev B envelope)
def sdr_indices():
    return [i for i, s in enumerate(SLOTS) if s[1] in ("b210", "b200")]

def sdr_sma_x(i):
    return tray_x(i) + T / 2 + STANDOFF + SMA_BZ

CLK_W, CLK_H = 118.0, 44.0          # clock board rev C (Hardware/clock_board_revC; same outline as rev B)
CLK_TOP_Z = 54.0                    # board top edge height (KiCad y=0)

def clk_outline():
    idx = sdr_indices()
    x0 = slot_x0(idx[0]) + 2.0      # KiCad x=0
    return x0, x0 + CLK_W, CLK_TOP_Z - CLK_H, CLK_TOP_Z

def clk_holes():
    x0, x1, z0, z1 = clk_outline()
    return [(x0 + 4, z1 - 4), (x1 - 4, z1 - 4), (x0 + 4, z0 + 4), (x1 - 4, z0 + 4)]

def clk_to_world(kx, ky):
    """KiCad board coords (component side, origin top-left) -> world (X, Z)."""
    x0, _, _, z1 = clk_outline()
    return x0 + kx, z1 - ky

_HERE = os.path.dirname(os.path.abspath(__file__))
CLK_PCB = next((p for p in (os.path.join(_HERE, "..", "clock_board_revC", "project", "heron_clock.kicad_pcb"),
                            os.path.join(_HERE, "..", "clockB", "project", "heron_clock.kicad_pcb")) if os.path.exists(p)),
               "")   # rev C KiCad board (D-028); the jack positions come from this file

def _clk_read():
    """{ref: (kx, ky, footprint_name)} for J1..J10 from the KiCad file (rev C)."""
    import re
    out = {}
    if not os.path.exists(CLK_PCB):
        return out
    txt = open(CLK_PCB).read()
    for blk in re.split(r"\n[ \t]+\(footprint ", txt)[1:]:   # KiCad 7 indents with spaces, KiCad 8 with tabs
        m = re.search(r'\(at ([-\d.]+) ([-\d.]+)', blk)
        r = re.search(r'\(property "Reference" "([^"]+)"', blk) or re.search(r'fp_text reference "([^"]+)"', blk)
        f = re.match(r'"([^"]*)"', blk)
        if m and r and re.fullmatch(r"J\d+", r.group(1)):
            out[r.group(1)] = (float(m.group(1)) - 100.0, float(m.group(2)) - 100.0, f.group(1) if f else "")
    return out

def clk_parts():
    """{ref: (kx, ky)} board-local positions of J1..J10 from the KiCad file (rev C)."""
    return {r: (x, y) for r, (x, y, _) in _clk_read().items()}

def clk_jack_kind(ref, fp=""):
    """'smp' or 'sma'. The footprint name decides. The KiCad file may still hold
    the old 132134 SMA footprint on J3-J10, so the reference also decides:
    J3-J10 are the SDR jacks (SMP). J2 (PPS IN) stays SMA."""
    if "SMP" in fp.upper():
        return "smp"
    return "smp" if ref in ("J%d" % n for n in range(3, 11)) else "sma"

def mate_pairs():
    """One entry for each SDR reference SMA that faces a clock board jack.
    Both the rear plate windows and the check_fit mate report use this list.
    The jack is the nearest SDR jack (J3-J10) to the SDR SMA axis. If the
    KiCad file is missing, the jack axis equals the SDR axis."""
    jacks = {}
    for ref, (kx, ky, fp) in _clk_read().items():
        if clk_jack_kind(ref, fp) == "smp":
            jacks[ref] = clk_to_world(kx, ky)
    out = []
    for i in sdr_indices():
        for k in ("ref_a", "ref_b"):
            ax = (sdr_sma_x(i), Z_BOARD + SDR_REAR_SMA[k])
            ref, jxz = None, ax
            if jacks:
                ref = min(jacks, key=lambda r: math.hypot(jacks[r][0] - ax[0], jacks[r][1] - ax[1]))
                jxz = jacks[ref]
            out.append(dict(sdr=SLOTS[i][0], key=k, axis_xz=ax, jack=ref, jack_xz=jxz))
    return out

def clock_board_ref():
    """clock board rev C: PCB in the XZ plane, jacks facing the SDRs, J1 on top.
    J3-J10: Amphenol SMP-MSSB-PCT10T envelope. J2: Amphenol 132134 SMA envelope."""
    x0, x1, z0, z1 = clk_outline()
    pcb = box(x0, CLK_Y0, z0, x1, CLK_Y0 + 1.6, z1)
    for (hx, hz) in clk_holes():
        pcb = pcb.cut(cq.Solid.makeCylinder(1.6, 5, cq.Vector(hx, CLK_Y0 - 1, hz), cq.Vector(0, 1, 0)))
    toward_sdr = cq.Vector(0, -1, 0)
    for ref, (kx, ky, fp) in _clk_read().items():
        wx, wz = clk_to_world(kx, ky)
        if ref == "J1":   # JST-GH, mouth up
            pcb = pcb.fuse(box(wx - 3.5, CLK_Y0 - 4.3, wz - 3.2, wx + 3.5, CLK_Y0, wz + 3.2))
        elif clk_jack_kind(ref, fp) == "smp":
            # SMP-MSSB-PCT10T (square body 5.99, mating face at SMP_JACK_H), Y measured from the board front face toward the SDR (Amphenol IGES model):
            # 6.0 sq body 0 - 1.22, dia 6.0 flange 1.22 - 2.31, dia 4.19 shroud 2.31 - SMP_JACK_H
            pcb = pcb.fuse(box(wx - 3.0, CLK_Y0 - 1.22, wz - 3.0, wx + 3.0, CLK_Y0, wz + 3.0))
            pcb = pcb.fuse(cq.Solid.makeCylinder(3.0, 1.09, cq.Vector(wx, CLK_Y0 - 1.22, wz), toward_sdr))
            pcb = pcb.fuse(cq.Solid.makeCylinder(4.19 / 2, SMP_JACK_H - 2.31, cq.Vector(wx, CLK_Y0 - 2.31, wz), toward_sdr))
        else:             # 132134: 6.35 sq body + SMA barrel, ~12.7 mm tall
            pcb = pcb.fuse(box(wx - 3.175, CLK_Y0 - 4.4, wz - 3.175, wx + 3.175, CLK_Y0, wz + 3.175))
            pcb = pcb.fuse(cq.Solid.makeCylinder(3.175, 8.3, cq.Vector(wx, CLK_Y0 - 4.4, wz), toward_sdr))
    return pcb

def mate_refs():
    """Reference solids for the direct mate: one adapter for each SDR jack.
    The adapter runs from the SDR SMA tip plane to the board jack mating face.
    It overlaps the jack shroud by design when mated. These are not parts to
    make. They are here to check fit."""
    out = []
    up = cq.Vector(0, 1, 0)
    for m in mate_pairs():
        ax, az = m["axis_xz"]
        tag = f"{m['sdr']}_{m['key']}"
        out.append((f"REF_adapter_{tag}", cq.Solid.makeCylinder(ADAPTER_D / 2, ADAPTER_REACH, cq.Vector(ax, SDR_SMA_TIP_Y, az), up)))
    return out

# ---------------------------------------------------------------- trays
def tray_local(kind):
    """local x = world Y, local y = world Z; thickness along world +X.
    SDR trays are solid: each one carries a grounded copper-tape shield
    between neighbouring SDRs. The NUC tray stays skeletal."""
    y1 = NUC_TRAY_Y1 if kind == "nuc" else TRAY_Y1
    adds, cuts = [], []
    # front pull tab with finger hole
    adds.append(rrect(-7.0, 60.0, 22.0, 26.0, 5.0, 0, T))
    cuts.append(circ(-9.0, 60.0, 10.0))
    if kind in ("b210", "b200"):
        holes = [(Y_BOARD - by, Z_BOARD + bx) for bx, by in SDR_HOLES]
        cuts += [circ(y, z, 3.4) for y, z in holes]
    else:  # nuc, front-aligned
        holes = [(Y_BOARD + hx, Z_BOARD + hz) for hx, hz in NUC_HOLES]
        cuts += [circ(y, z, 3.4) for y, z in holes]
        cuts += grid_windows(14.0, 20.0, y1 - 12.0, H - 26.0, 2, 2, 7.0)
    return plate(TRAY_Y0, TRAY_Z0, y1, TRAY_Z1, adds=adds, cuts=cuts)

# ---------------------------------------------------------------- SSD bay (printed)
def ssd_cage():
    """Guide cage for the drop-in SSD (one print, ASA-CF). Two U-channel guides
    hold the long edges of the drive + sled. The base holds the 22-pin
    receptacle adapter; its pocket opens toward the NUC for the cable.
    Flanges screw to the bottom plate, guide-top ears to the top plate."""
    g = ssd_geom()
    cx0, cx1 = g["sx0"] - SSD_CLR, g["sx1"] + SSD_CLR          # channel (X)
    ox0, ox1 = cx0 - 2.0, cx1 + 2.0                            # cage outer (X)
    fy0, fy1 = g["y0"] - SSD_CLR - 2.0, g["y1"] + SSD_CLR + 2.0
    s = box(ox0, fy0, 0, ox1, fy1, SSD_BASE)                   # base block
    for (yo, yi) in ((fy0, g["y0"] + 3.0), (g["y1"] - 3.0, fy1)):   # guides
        gd = box(ox0, min(yo, yi), SSD_BASE - 0.01, ox1, max(yo, yi), H - 0.4)
        gd = gd.cut(box(cx0, g["y0"] - SSD_CLR if yo == fy0 else g["y1"] - 3.1,
                        SSD_BASE - 1, cx1, g["y0"] + 3.1 if yo == fy0 else g["y1"] + SSD_CLR, H + 1))
        s = s.fuse(gd)
    for zr in (55.0, 90.0):                                    # tie ribs, outside the channel
        for (a, b) in ((ox0, cx0), (cx1, ox1)):
            s = s.fuse(box(a, g["y0"] + 2.9, zr, b, g["y1"] - 2.9, zr + 4.0))
    xs = sorted(set(h[0] for h in ssd_cage_holes()))
    ym = (g["y0"] + g["y1"]) / 2
    for x in xs:                                               # bottom flanges + top ears
        s = s.fuse(box(min(x, ox0) - 5.0 if x < ox0 else ox1 - 0.01, ym - 27.0, 0,
                       ox0 + 0.01 if x < ox0 else x + 5.0, ym + 27.0, 4.0))
        s = s.fuse(box(min(x, ox0) - 5.0 if x < ox0 else ox1 - 0.01, ym - 27.0, H - 6.4,
                       ox0 + 0.01 if x < ox0 else x + 5.0, ym + 27.0, H - 0.4))
    for (hx, hy) in ssd_cage_holes():                          # M3 heat-set inserts
        s = s.cut(cq.Solid.makeCylinder(2.0, 5.0, cq.Vector(hx, hy, -0.01)))
        s = s.cut(cq.Solid.makeCylinder(2.0, 5.0, cq.Vector(hx, hy, H - 5.4)))
    # receptacle adapter pocket, centred on the drive connector, open toward -Y (NUC)
    ccy = g["y0"] + SSD_CONN_C
    pc = (cx0 + cx1) / 2
    s = s.cut(box(pc - SSD_CONN_H / 2, fy0 - 1, 3.0, pc + SSD_CONN_H / 2, ccy + SSD_CONN_W / 2, SSD_BASE + 1))
    # lighten the base behind the pocket
    s = s.cut(box(ox0 - 1, ccy + SSD_CONN_W / 2 + 3, 3.0, ox1 + 1, fy1 - 4, SSD_BASE - 2.5))
    return s.clean()

def ssd_sled():
    """Printed sled (ASA-CF) on the drive's bottom (hole) face, with a handle
    above the drive. Four M3 flat-head screws through countersunk slots."""
    g = ssd_geom()
    s = box(g["sx0"], g["y0"], g["zd0"], g["sx0"] + SLED_T, g["y1"], g["zd1"] + 0.3)
    # handle block above the drive (0.3 mm gap to the drive), fills the channel width
    s = s.fuse(box(g["sx0"], g["y0"] + 12.0, g["zd1"] + 0.3, g["sx1"], g["y1"] - 12.0, H + T - 0.3))
    # cord hole through the handle (tie a pull loop here)
    s = s.cut(box(g["sx0"] - 1, g["y0"] + 22.0, g["zd1"] + 2.5, g["sx1"] + 1, g["y1"] - 22.0, g["zd1"] + 6.5))
    # screw slots (cover both SFF-8201 hole positions)
    for (a, b) in SSD_HOLE_Y:
        zc = g["zd0"] + (a + b) / 2
        for yy in (g["y0"] + SSD_HOLE_IN, g["y1"] - SSD_HOLE_IN):
            sl = slot2d(0, 0, (b - a) + 3.4, 3.4, -1, SLED_T + 1)
            sl = place(sl, (g["sx0"] + SLED_T, yy, zc), (0, 0, 1), (0, 1, 0), (-1, 0, 0))   # right-handed: slot runs along Z
            s = s.cut(sl)
    # lightening windows between the screw rows
    s = s.cut(box(g["sx0"] - 1, g["y0"] + 10.0, g["zd0"] + 20.0, g["sx0"] + SLED_T + 1, g["y1"] - 10.0, g["zd1"] - 20.0))
    return s.clean()

# ---------------------------------------------------------------- latch
def latch_local(preload=False):
    """Swing latch. Local z runs toward the rack front (world -Y).
    preload=True (SDR latches) adds a boss with an M3 heat-set insert. An M3
    thumbscrew goes through the boss along +Y. Its tip pushes on the tray front
    edge. The thumbscrew seats both connectors. The rear stop screw is a backup. A screw gives
    the force: up to 15 N to engage each SMP interface (30 N for each SDR).
    The pivot hole and the swing-aside movement do not change."""
    n = LATCH_L_PRE if preload else LATCH_L
    s = plate(0, -4, n, 4)
    s = s.cut(circ(3.0, 0, 3.4))
    if preload:
        bx = STRIP_W / 2 + 6.0                 # local x of the tray centre plane (pivot is 3 mm from the arm start)
        s = s.fuse(cq.Solid.makeCylinder(PRE_BOSS_D / 2, PRE_BOSS_T, cq.Vector(bx, 0, 0)))
        # The insert goes in from the REAR face (local z = 0). The tray pushes
        # the screw toward the front, so the load presses the insert into its pocket.
        s = s.cut(cq.Solid.makeCylinder(INSERT_D / 2, STOP_INSERT_L, cq.Vector(bx, 0, -0.01)))
        s = s.cut(cq.Solid.makeCylinder(STOP_SCREW_CLR / 2, PRE_BOSS_T + 0.02, cq.Vector(bx, 0, -0.01)))
    s = s.fuse(cq.Solid.makeCylinder(1.5, 2.0, cq.Vector(12.5, 0, T)))   # grip nub
    return s.clean()

# ---------------------------------------------------------------- reference stand-ins
def sdr_ref(kind):
    path = os.path.join(REF, "b210.stp" if kind == "b210" else "cu_ettus_b200_cca.stp")
    if os.path.exists(path):
        return cq.importers.importStep(path).val()
    return box(0, -SDR_L, 0, SDR_W, 0, SDR_TOP)

def sdr_place(shape, i):
    """board coords (bx right, by negative toward rear, bz up) -> rack."""
    o = (tray_x(i) + T / 2 + STANDOFF, Y_BOARD, Z_BOARD)
    return place(shape, o, (0, 0, 1), (0, -1, 0), (1, 0, 0))

def nuc_ref(i):
    x = tray_x(i) + T / 2 + STANDOFF
    y0 = Y_BOARD
    pcb = box(x, y0, Z_BOARD, x + PCB_T, y0 + NUC_S, Z_BOARD + NUC_S)
    env = box(x + PCB_T, y0 + 10, Z_BOARD + 10, x + NUC_TOP, y0 + NUC_S - 10, Z_BOARD + NUC_S - 10)
    return pcb.fuse(env)

def ssd_ref():
    g = ssd_geom()
    return box(g["sx0"] + SLED_T, g["y0"], g["zd0"], g["sx1"], g["y1"], g["zd1"])

# ---------------------------------------------------------------- assembly
def mirror_top(shp):
    return shp.mirror("XY", (0, 0, H / 2))

def build(with_refs=True):
    """parts: (name, world_shape, material, local_shape_or_None, export_name)"""
    parts = []
    bp, tp = plate_local(False), plate_local(True)
    parts.append(("bottom_plate", bp.moved(cq.Location(cq.Vector(0, 0, -T))), "laser", bp, "bottom_plate_x1"))
    parts.append(("top_plate", tp.moved(cq.Location(cq.Vector(0, 0, H))), "laser", tp, "top_plate_x1"))
    sw = side_wall_local()
    parts.append(("side_wall_L", place(sw, (-T, 0, 0), (0, 1, 0), (0, 0, 1), (1, 0, 0)), "laser", sw, "side_wall_x2"))
    parts.append(("side_wall_R", place(sw, (W, 0, 0), (0, 1, 0), (0, 0, 1), (1, 0, 0)), "laser", None, None))
    rp = rear_plate_local()
    parts.append(("rear_plate", place(rp, (0, REAR_Y0 + T, 0), (1, 0, 0), (0, 0, 1), (0, -1, 0)), "laser", rp, "rear_plate"))
    nsdr = len(sdr_indices())
    exported = set()
    for i, (name, kind, _) in enumerate(SLOTS):
        fam = "sdr" if kind in ("b210", "b200") else kind
        first = fam not in exported
        exported.add(fam)
        n = nsdr if fam == "sdr" else 1
        nuc = fam == "nuc"
        tl = tray_local(kind)
        tw = place(tl, (tray_x(i) - T / 2, 0, 0), (0, 1, 0), (0, 0, 1), (1, 0, 0))
        parts.append((f"tray_{name}", tw, "laser", tl if first else None, f"tray_{fam}{'_solid' if fam == 'sdr' else ''}_x{n}"))
        sl = strip_local(NUC_STRIP_Y1 if nuc else STRIP_Y1, NUC_EAR_Y if nuc else EAR_Y, sdr=not nuc)
        sb = sl.moved(cq.Location(cq.Vector(tray_x(i), 0, 0)))
        parts.append((f"strip_bot_{name}", sb, "asa", sl if first else None,
                      f"rail_strip_{'nuc' if nuc else 'sdr'}_x{n} (+ same number MIRRORED for top)"))
        parts.append((f"strip_top_{name}", mirror_top(sb), "asa", None, None))
        rlen = (NUC_TRAY_Y1 if nuc else TRAY_Y1) - TRAY_Y0
        rl = tpu_rail_local(rlen)
        rb = rl.moved(cq.Location(cq.Vector(tray_x(i), TRAY_Y0, TRAY_Z0 - RAIL_BACK)))
        parts.append((f"rail_bot_{name}", rb, "tpu", rl if first else None,
                      f"tpu_rail_{'nuc' if nuc else 'sdr'}_x{2 * n}"))
        parts.append((f"rail_top_{name}", mirror_top(rb), "tpu", None, None))
        lt = latch_local(preload=not nuc)
        px = tray_x(i) + STRIP_W / 2 + 3.0
        pz = LATCH_Z
        lw = place(lt, (px + 3.0, 0, pz), (-1, 0, 0), (0, 0, -1), (0, -1, 0))
        parts.append((f"latch_bot_{name}", lw, "asa", lt if first else None,
                      "latch_sdr_preload_x%d" % (2 * nsdr) if not nuc else "latch_x3 (NUC x2 + SSD hatch x1)"))
        parts.append((f"latch_top_{name}", mirror_top(lw), "asa", None, None))
    # SSD bay
    cg, sd = ssd_cage(), ssd_sled()
    parts.append(("ssd_cage", cg, "asa", cg, "ssd_cage_x1"))
    parts.append(("ssd_sled", sd, "asa", sd, "ssd_sled_x1"))
    lx, ly = ssd_latch_xy()
    lh = place(latch_local(), (lx + 3.0, ly, H + T), (-1, 0, 0), (0, -1, 0), (0, 0, 1))   # pivot on the hole; closed: arm over the hatch
    parts.append(("latch_ssd_hatch", lh, "asa", None, None))
    refs = []
    if with_refs:
        for i, (name, kind, _) in enumerate(SLOTS):
            if kind in ("b210", "b200"):
                refs.append((f"REF_{name}", sdr_place(sdr_ref(kind), i)))
            elif kind == "nuc":
                refs.append(("REF_NUC7i3DNB_envelope", nuc_ref(i)))
        refs += mate_refs()
        refs.append(("REF_SSD_2.5in", ssd_ref()))
        refs.append(("REF_clock_board_revC", clock_board_ref()))
    return parts, refs

DENS = {"laser_ply": 0.68, "laser_acrylic": 1.19, "laser_delrin": 1.41, "asa": 1.07, "tpu": 1.21}
FILL = {"asa": 0.6, "tpu": 1.0}     # effective printed density factor (walls + infill)

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    with_refs = "--norefs" not in sys.argv
    parts, refs = build(with_refs)
    print(f"Overall: {W + 2*T:.1f} W x {D:.1f} D x {H + 2*T:.1f} H mm (interior W {W:.1f})")
    # per-part exports
    for sub in ("laser_dxf", "print_stl", "step_parts"):
        os.makedirs(os.path.join(OUT, sub), exist_ok=True)
    rows = []
    for name, shp, mat, loc, ename in parts:
        vol = shp.Volume() / 1000.0
        if mat == "laser":
            m = {k: vol * DENS["laser_" + k] for k in ("ply", "acrylic", "delrin")}
            rows.append((name, mat, round(vol, 2), round(m["ply"], 1), round(m["acrylic"], 1), round(m["delrin"], 1)))
        else:
            g = vol * DENS[mat] * FILL[mat]
            rows.append((name, mat, round(vol, 2), round(g, 1), round(g, 1), round(g, 1)))
        if loc is not None:
            base = ename.split(" ")[0]
            cq.exporters.export(cq.Workplane().add(loc), os.path.join(OUT, "step_parts", base + ".step"))
            if mat == "laser":
                sec = cq.Workplane("XY").add(loc).section(T / 2)
                cq.exporters.export(sec, os.path.join(OUT, "laser_dxf", base + ".dxf"))
            else:
                cq.exporters.export(cq.Workplane().add(loc), os.path.join(OUT, "print_stl", base + ".stl"), tolerance=0.05, angularTolerance=0.2)
    with open(os.path.join(OUT, "mass.csv"), "w", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["part", "material", "volume_cm3", "g_if_ply", "g_if_acrylic", "g_if_delrin"])
        wtr.writerows(rows)
    # assembly
    asm = cq.Assembly(name="HERON_payload_rack_revA")
    colors = {"laser": cq.Color(0.85, 0.72, 0.5), "asa": cq.Color(0.2, 0.2, 0.22), "tpu": cq.Color(0.9, 0.35, 0.1)}
    for name, shp, mat, _, _ in parts:
        asm.add(shp, name=name, color=colors[mat])
    for name, shp in refs:
        asm.add(shp, name=name, color=cq.Color(0.2, 0.55, 0.3, 0.6))
    fn = "HERON_rack_revA_assembly.step" if with_refs else "HERON_rack_revA_assembly_noboards.step"
    asm.save(os.path.join(OUT, fn))
    print("wrote", fn)
