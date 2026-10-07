"""Render the SSD bay with the drive + sled lifted 60 mm out of the top-plate hatch."""
import cadquery as cq
import render as Rn, heron_rack as R
parts, refs = R.build(with_refs=True)
lift = cq.Location(cq.Vector(0, 0, 60))
items = []
for n, s, m, *_ in parts:
    if n in ("side_wall_L",):
        continue
    if n == "ssd_sled":
        s = s.moved(lift)
    if n == "latch_ssd_hatch":
        s = s.rotate(cq.Vector(*R.ssd_latch_xy(), 0), cq.Vector(*R.ssd_latch_xy(), 1), 90)   # latch swung open
    items.append((s, Rn.COL[m], 1.0))
for n, s in refs:
    if n == "REF_SSD_2.5in":
        s = s.moved(lift)
        items.append((s, (0.1, 0.25, 0.6), 1.0))
    else:
        items.append((s, (0.15, 0.5, 0.25), 0.95))
Rn.scene(items, 28, -145, "out/preview_ssd_bay.png", "SSD (blue) lifts out through the top-plate hatch behind the NUC (left wall hidden)")
