"""Grouped BOM (CSV) from the KiCad netlist. DNP parts and test points/holes are listed apart.

The purchase data (stock and price on the check date, notes)
comes from bom_sources.csv, keyed by MPN. Why a separate file: stock
and prices change often, but the schematic must not. The same file lists the
off-board parts (bullets, adapters) that the board needs in the rack.
The script fails when a fitted part has no MPN or no purchase data, so a
stale BOM cannot pass silently.
"""
import csv, os, sys, re
from sexp import parse, find, find1

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = os.path.join(HERE, "bom_sources.csv")

net = parse(open(sys.argv[1], encoding="utf-8").read())
sch = parse(open(sys.argv[1].replace('.net', '.kicad_sch'), encoding="utf-8").read())
src = {r["MPN"]: r for r in csv.DictReader(open(SOURCES, encoding="utf-8", newline=""))}

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
    # Group by part number, not by value: J3-J10 have different values but one part.
    key = (ref in dnp, fields.get("MPN", "") or val, fp, fields.get("Manufacturer", ""), fields.get("MPN", ""),
           fields.get("Dielectric", ""), fields.get("Tolerance", ""))
    groups.setdefault(key, []).append((ref, val))

nat = lambda r: [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', r)]
rows = sorted(groups.items(), key=lambda kv: (kv[0][0], nat(sorted(r for r, _ in kv[1])[0][:1]),
                                              nat(sorted((r for r, _ in kv[1]), key=nat)[0])))
problems = []
with open(sys.argv[2], "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["Qty", "References", "Value", "Footprint", "Manufacturer", "MPN", "Dielectric", "Tolerance", "Fit",
                "Stock 2026-10-05", "Unit price qty 1 (USD)", "Note"])
    for (isdnp, _, fp, mfr, mpn, diel, tol), items in rows:
        items = sorted(items, key=lambda x: nat(x[0]))
        refs = [r for r, _ in items]
        vals = sorted({v for _, v in items}, key=nat)
        s = src.get(mpn, {})
        if not isdnp and (not mpn or not s):
            problems.append(f"{' '.join(refs)}: {'no MPN' if not mpn else 'no row in bom_sources.csv for ' + mpn}")
        w.writerow([len(refs), " ".join(refs), " / ".join(vals), fp.split(":")[1], mfr, mpn, diel, tol,
                    "DNP" if isdnp else "Fit", s.get("Stock 2026-10-05", ""),
                    s.get("Unit price qty 1 (USD)", ""), s.get("Note", "")])
    # Off-board parts: not on the PCB, but part of the clock board to SDR connection.
    for s in src.values():
        if s.get("Off-board use"):
            w.writerow([s["Off-board qty"] or 0, "(off-board)", s["Off-board use"], "", s["Manufacturer"], s["MPN"], "", "",
                        "Off-board" if (s["Off-board qty"] or "0") != "0" else "Alternative",
                        s["Stock 2026-10-05"], s["Unit price qty 1 (USD)"], s["Note"]])
print("BOM lines:", len(rows))
if problems:
    print("BOM PROBLEMS:\n  " + "\n  ".join(problems))
    sys.exit(1)
