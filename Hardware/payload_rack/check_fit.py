"""Fit check for the HERON payload rack.

Part 1: interference. Report every pair of parts (and reference boards)
whose solids overlap. The mated adapter and board overlap by design (the
adapter plugs onto the jack), so the script skips that pair on purpose
(see MATED).
Part 3 (below): a clear tool path to each rear stop screw.
Part 2: mate report. For each SDR and each of its two reference jacks,
print the radial offset between the adapter axis and the board jack axis,
and the axial error: the adapter mated face must equal the board jack face.
The script fails when a value is out of limit.
"""
import itertools, sys, time
import cadquery as cq
import heron_rack as R

RADIAL_MAX = 0.05    # mm: largest radial offset that still mates
AXIAL_TOL = 0.01     # mm: largest difference between the adapter mated face and the jack face

parts, refs = R.build(with_refs=True)
items = [(n, s) for n, s, *_ in parts] + list(refs)
bbs = [(n, s, s.BoundingBox()) for n, s in items]

# The mated pair overlaps by design: adapter + board (the adapter covers the
# jack shroud). Skip this pair only. Every other pair stays in the clash test.
BOARD = "REF_clock_board_revC"
MATED = set()
for m in R.mate_pairs():
    tag = "%s_%s" % (m["sdr"], m["key"])
    MATED.add(frozenset(("REF_adapter_" + tag, BOARD)))

def ov(a, b, tol=0.01):
    return (a.xmin < b.xmax - tol and b.xmin < a.xmax - tol and a.ymin < b.ymax - tol and
            b.ymin < a.ymax - tol and a.zmin < b.zmax - tol and b.zmin < a.zmax - tol)
bad = []
t = time.time()
for (n1, s1, b1), (n2, s2, b2) in itertools.combinations(bbs, 2):
    if frozenset((n1, n2)) in MATED or not ov(b1, b2):
        continue
    try:
        v = s1.intersect(s2).Volume()
    except Exception as e:
        v = -1
    if v > 0.5 or v < 0:
        bad.append((round(v, 1), n1, n2))
for b in sorted(bad, reverse=True):
    print("CLASH %8.1f mm3  %s  <->  %s" % b)
print("pairs checked; clashes:", len(bad), "time %.0fs" % (time.time() - t))

# ---------------------------------------------------------------- mate report
# The axial error is read from the solids, not from the formula. The jack face is the
# lowest Y of the board model in a thin column around the jack axis.
byname = dict(items)
board = byname[BOARD]
fails = 0
print("\nMate report (limit: radial <= %.2f mm, adapter face = jack face +/- %.2f mm)" % (RADIAL_MAX, AXIAL_TOL))
print("%-8s %-6s %-5s %9s %9s %s" % ("SDR", "jack", "ref", "radial", "axial", "result"))
for m in R.mate_pairs():
    tag = "%s_%s" % (m["sdr"], m["key"])
    ad = byname["REF_adapter_" + tag].BoundingBox()
    ax, az = (ad.xmin + ad.xmax) / 2, (ad.zmin + ad.zmax) / 2
    jx, jz = m["jack_xz"]
    radial = ((ax - jx) ** 2 + (az - jz) ** 2) ** 0.5
    col = cq.Solid.makeBox(2.0, 20.0, 2.0, cq.Vector(jx - 1.0, R.CLK_Y0 - 15.0, jz - 1.0))
    jack_face = board.intersect(col).BoundingBox().ymin
    axial = ad.ymax - jack_face    # 0 when the adapter is fully mated
    ok = radial <= RADIAL_MAX and abs(axial) <= AXIAL_TOL
    fails += 0 if ok else 1
    print("%-8s %-6s %-5s %9.3f %9.3f %s" % (m["sdr"], m["jack"], m["key"], radial, axial, "ok" if ok else "FAIL"))
print("mate report:", "PASS" if not fails else "FAIL (%d)" % fails)

# ---------------------------------------------------------------- stop screw access
# A tool shaft (STOP_KEY_D) must reach each rear stop screw from behind the
# rack, through the rear-plate access hole, without touching any part. The
# path runs from the rear plate outer face to the rear face of the stop block.
print("\nStop screw access (tool shaft d = %.1f mm, hole d = %.1f mm)" % (R.STOP_KEY_D, R.STOP_ACCESS_D))
blocked = 0
for (x, z) in R.stop_axes():
    y0 = R.STRIP_Y1 + 0.05                   # stop block rear face, with a small gap
    y1 = R.REAR_Y0 + R.T + 1.0               # just behind the rear plate
    key = cq.Solid.makeCylinder(R.STOP_KEY_D / 2, y1 - y0, cq.Vector(x, y0, z), cq.Vector(0, 1, 0))
    kb = key.BoundingBox()
    hits = []
    for n, s, b in bbs:
        if ov(kb, b) and s.intersect(key).Volume() > 0.01:
            hits.append(n)
    blocked += 1 if hits else 0
    print("  X %6.1f  Z %5.1f  %s" % (x, z, "clear" if not hits else "BLOCKED by " + ", ".join(hits)))
# Each access hole must keep MIN_WEB of rear plate material all around it
# (plate edge, T-slots, board holes, windows). The ring around the hole must
# be fully inside the plate.
MIN_WEB = 2.0
rp = R.rear_plate_local()
for (x, z) in R.stop_axes():
    r0 = R.STOP_ACCESS_D / 2
    ring = cq.Solid.makeCylinder(r0 + MIN_WEB, R.T, cq.Vector(x, z, 0)).cut(cq.Solid.makeCylinder(r0, R.T, cq.Vector(x, z, 0)))
    lost = ring.Volume() - rp.intersect(ring).Volume()
    if lost > 0.01:
        blocked += 1
        print("  X %6.1f  Z %5.1f  web < %.1f mm (%.2f mm3 missing)" % (x, z, MIN_WEB, lost))
print("stop screw access:", "PASS" if not blocked else "FAIL (%d)" % blocked)
fails += blocked
sys.exit(1 if fails else 0)
