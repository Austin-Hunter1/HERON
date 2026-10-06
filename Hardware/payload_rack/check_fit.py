"""Interference check: every pair of parts (and reference boards) whose boxes overlap."""
import itertools, time
import cadquery as cq
import heron_rack as R
parts, refs = R.build(with_refs=True)
items = [(n, s) for n, s, *_ in parts] + list(refs)
bbs = [(n, s, s.BoundingBox()) for n, s in items]
def ov(a, b, tol=0.01):
    return (a.xmin < b.xmax - tol and b.xmin < a.xmax - tol and a.ymin < b.ymax - tol and
            b.ymin < a.ymax - tol and a.zmin < b.zmax - tol and b.zmin < a.zmax - tol)
bad = []
t = time.time()
for (n1, s1, b1), (n2, s2, b2) in itertools.combinations(bbs, 2):
    if not ov(b1, b2):
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
