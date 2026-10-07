import sys, numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import cadquery as cq
import heron_rack as R

COL = {"laser": (0.86, 0.72, 0.50), "asa": (0.25, 0.25, 0.28), "tpu": (0.95, 0.42, 0.12)}
def tris(shape, tol=0.4):
    v, f = shape.tessellate(tol, 0.5)
    v = np.array([(p.x, p.y, p.z) for p in v])
    return v[np.array(f)] if len(f) else np.zeros((0, 3, 3))

def shade(t, base, light=np.array([0.4, -0.6, 0.7])):
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    n /= (np.linalg.norm(n, axis=1)[:, None] + 1e-9)
    k = 0.45 + 0.55 * np.abs(n @ (light / np.linalg.norm(light)))
    return np.clip(np.array(base)[None, :] * k[:, None], 0, 1)

def scene(items, elev, azim, fn, title, explode=None, alpha_ref=0.9):
    fig = plt.figure(figsize=(11, 8.5), dpi=130)
    ax = fig.add_subplot(111, projection="3d")
    allv = []
    for shp, base, a in items:
        t = tris(shp)
        if not len(t): continue
        pc = Poly3DCollection(t, facecolors=shade(t, base), edgecolors="none", alpha=a)
        ax.add_collection3d(pc); allv.append(t.reshape(-1, 3))
    allv = np.vstack(allv)
    mn, mx = allv.min(0), allv.max(0); c = (mn + mx) / 2; r = (mx - mn).max() / 2
    ax.set_xlim(c[0] - r, c[0] + r); ax.set_ylim(c[1] - r, c[1] + r); ax.set_zlim(c[2] - r, c[2] + r)
    ax.set_box_aspect((1, 1, 1)); ax.view_init(elev, azim); ax.set_axis_off()
    ax.set_title(title, fontsize=13)
    plt.tight_layout(); plt.savefig(fn, bbox_inches="tight"); plt.close()

if __name__ != "__main__":
    pass
parts, refs = (R.build(with_refs=("--boards" in sys.argv)) if __name__ == "__main__" else ([], []))
if __name__ == '__main__':
    mk = lambda skip=(): [(s, COL[m], 1.0) for n, s, m, *_ in parts if not any(k in n for k in skip)]
    boards = [(s, (0.15, 0.5, 0.25), 0.95) for n, s in refs]
    scene(mk() + boards, 22, -125, "out/preview_front.png", "HERON payload rack Rev A - front (RF side), boards installed")
    scene(mk() + boards, 25, 55, "out/preview_rear.png", "Rear: clock board strip, cable exits")
    scene(mk(("top_plate", "side_wall_R", "strip_top", "rail_top", "latch_top")) + boards, 35, -60, "out/preview_open.png",
          "Top, right wall removed: blades in rail strips")
    # one SDR blade alone
    i = 2
    blade = [(s, COL[m], 1.0) for n, s, m, *_ in parts if n.endswith("B210_1") and ("tray" in n or "rail" in n)]
    blade += [(s, (0.15, 0.5, 0.25), 1.0) for n, s in refs if n.endswith("B210_1")]
    scene(blade, 15, -150, "out/preview_sdr_blade.png", "SDR blade: skeletal tray + TPU C-rails + B210")
