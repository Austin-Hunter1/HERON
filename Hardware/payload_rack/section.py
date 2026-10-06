import numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon
import cadquery as cq
import heron_rack as R
from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
COL = {"laser": "#d9b47c", "asa": "#3a3a40", "tpu": "#f06a1e", "ref": "#2f8a4a"}
def sect(shape, y):
    # cut a thin slab and project faces lying in plane y
    slab = cq.Solid.makeBox(1000, 0.02, 1000, cq.Vector(-500, y - 0.01, -500))
    try:
        s = shape.intersect(slab)
    except Exception:
        return []
    polys = []
    for f in s.Faces():
        if f.geomType() == "PLANE" and abs(f.normalAt().y) > 0.99 and f.Center().y < y:
            pts = []
            ed = f.outerWire().discretize(60) if hasattr(f.outerWire(), "discretize") else None
            try:
                pts = [(p.x, p.z) for p in [f.outerWire().positionAt(t) for t in np.linspace(0, 1, 120)]]
            except Exception:
                pass
            polys.append(pts)
    return polys
parts, refs = R.build(True)
for y in (80.0, 170.0):
    fig, ax = plt.subplots(figsize=(14, 9), dpi=120)
    for n, s, m, *_ in parts:
        for p in sect(s, y):
            ax.add_patch(Polygon(p, closed=True, fc=COL[m], ec="k", lw=0.3))
    for n, s in refs:
        for p in sect(s, y):
            ax.add_patch(Polygon(p, closed=True, fc=COL["ref"], ec="k", lw=0.2, alpha=0.8))
    ax.set_xlim(-8, R.W + 8); ax.set_ylim(-8, R.H + 8); ax.set_aspect("equal"); ax.grid(alpha=0.25)
    ax.set_title(f"Section at y = {y} mm, looking from the front (X across, Z up)  - tan laser / dark ASA / orange TPU / green boards")
    plt.savefig(f"out/section_y{int(y)}.png", bbox_inches="tight"); plt.close()
print("ok")
