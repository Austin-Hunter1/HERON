"""Grouped BOM (CSV) from the KiCad netlist. DNP parts and test points/holes are listed apart."""
import csv, sys, re
from sexp import parse, find, find1
net = parse(open(sys.argv[1]).read())
sch = parse(open(sys.argv[1].replace('.net', '.kicad_sch')).read())
dnp = set()
for s in find(sch, "symbol"):
    d = find1(s, "dnp")
    if d and str(d[1]) == "yes":
        dnp.add([p[2] for p in find(s, "property") if p[1] == "Reference"][0])
groups = {}
for c in find(find1(net, "components"), "comp"):
    ref = find1(c, "ref")[1]
    if ref.startswith(("TP", "H", "FID")):
        continue
    val = find1(c, "value")[1]; fp = find1(c, "footprint")[1]
    fields = {f[1][1]: f[2] for f in find(find1(c, "fields") or [], "field") if len(f) > 2}
    key = (ref in dnp, val, fp, fields.get("Manufacturer", ""), fields.get("MPN", ""), fields.get("Dielectric", ""), fields.get("Tolerance", ""))
    groups.setdefault(key, []).append(ref)
nat = lambda r: [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', r)]
rows = sorted(groups.items(), key=lambda kv: (kv[0][0], nat(kv[1][0][:1]), nat(sorted(kv[1], key=nat)[0])))
with open(sys.argv[2], "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Qty", "References", "Value", "Footprint", "Manufacturer", "MPN", "Dielectric", "Tolerance", "Fit"])
    for (isdnp, val, fp, mfr, mpn, diel, tol), refs in rows:
        refs = sorted(refs, key=nat)
        w.writerow([len(refs), " ".join(refs), val, fp.split(":")[1], mfr, mpn, diel, tol, "DNP" if isdnp else "Fit"])
print("BOM lines:", len(rows))
