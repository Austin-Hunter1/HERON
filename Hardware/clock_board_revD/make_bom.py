"""Grouped BOM (CSV) from the KiCad netlist. DNP parts and test points/holes are listed apart.

The purchase data (stock and price on the check date, notes)
comes from bom_sources.csv, keyed by MPN. Why a separate file: stock
and prices change often, but the schematic must not. The same file lists the
off-board parts (bullets, adapters) that the board needs in the rack.
The script fails when a fitted part has no MPN or no purchase data, so a
stale BOM cannot pass silently.

It also writes a Digi-Key list-upload file next to the BOM
(<bom name>_digikey.csv): one product per line, comma-delimited, no header:
"quantity,part number,customer reference". The part number is the Digi-Key
part number from bom_sources.csv. Why: an MPN can match more than one product
(for example 132134 also matches a Brady part), and then Digi-Key drops the line
or picks another maker. When a part has no Digi-Key number, the MPN is used
and the script prints a warning. DNP parts are
left out, and so are parts marked "yes" in the "In hand" column of
bom_sources.csv (the team already has them). A "Spare qty" in bom_sources.csv adds
that fixed number of spares (also for DNP parts, for example R23). The off-board adapters are included.

Usage: make_bom.py <netlist> <bom.csv> [boards]
  boards: number of boards to buy for (default 1). The quantities scale with it.
"""
import csv, os, sys, re
from sexp import parse, find, find1

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = os.path.join(HERE, "bom_sources.csv")

net = parse(open(sys.argv[1], encoding="utf-8").read())
sch = parse(open(sys.argv[1].replace('.net', '.kicad_sch'), encoding="utf-8").read())
src = {r["MPN"]: r for r in csv.DictReader(open(SOURCES, encoding="utf-8", newline=""))}
BOARDS = int(sys.argv[3]) if len(sys.argv) > 3 else 1
DK_REF_MAX = 48     # Digi-Key customer reference length limit (VERIFY on the upload page)
upload = []         # (quantity, MPN, customer reference) for the Digi-Key file

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
    w.writerow(["Qty", "References", "Value", "Footprint", "Manufacturer", "MPN", "Digi-Key PN", "Dielectric", "Tolerance", "Fit",
                "Stock 2026-10-05", "Unit price qty 1 (USD)", "Note"])
    for (isdnp, _, fp, mfr, mpn, diel, tol), items in rows:
        items = sorted(items, key=lambda x: nat(x[0]))
        refs = [r for r, _ in items]
        vals = sorted({v for _, v in items}, key=nat)
        s = src.get(mpn, {})
        if not isdnp and (not mpn or not s):
            problems.append(f"{' '.join(refs)}: {'no MPN' if not mpn else 'no row in bom_sources.csv for ' + mpn}")
        if not isdnp and mpn and s.get("In hand", "").lower() != "yes":
            upload.append((len(refs) * BOARDS, mpn, " ".join(refs)))
        # Spares: a fixed count from bom_sources.csv, also for DNP parts. Why:
        # R23 is DNP until the GNSS receiver is chosen, but the part must be on hand.
        spare = int(s.get("Spare qty") or 0)
        if mpn and spare:
            upload.append((spare, mpn, " ".join(refs) + (" DNP" if isdnp else "") + " spare"))
        w.writerow([len(refs), " ".join(refs), " / ".join(vals), fp.split(":")[1], mfr, mpn, s.get("Digi-Key PN", ""), diel, tol,
                    "DNP" if isdnp else "Fit", s.get("Stock 2026-10-05", ""),
                    s.get("Unit price qty 1 (USD)", ""), s.get("Note", "")])
    # Off-board parts: not on the PCB, but part of the clock board to SDR connection.
    for s in src.values():
        if s.get("Off-board use"):
            if (s["Off-board qty"] or "0") != "0":
                upload.append((int(s["Off-board qty"]) * BOARDS, s["MPN"], "off-board SDR adapters"))
            w.writerow([s["Off-board qty"] or 0, "(off-board)", s["Off-board use"], "", s["Manufacturer"], s["MPN"], s.get("Digi-Key PN", ""), "", "",
                        "Off-board" if (s["Off-board qty"] or "0") != "0" else "Alternative",
                        s["Stock 2026-10-05"], s["Unit price qty 1 (USD)"], s["Note"]])
# Digi-Key upload. Why no header and no quotes: the upload expects plain
# "qty,part,reference" lines. Commas are not allowed inside a field, so the
# script fails instead of writing a line that the upload would split.
dk = os.path.splitext(sys.argv[2])[0] + "_digikey.csv"
no_dk = []
with open(dk, "w", newline="", encoding="utf-8") as f:
    for qty, mpn, cref in upload:
        part = src.get(mpn, {}).get("Digi-Key PN", "").strip()
        if not part:
            no_dk.append(mpn); part = mpn
        if len(cref) > DK_REF_MAX:   # keep the first and last reference so the line stays readable
            cref = cref.split()[0] + " .. " + cref.split()[-1] + " (%d)" % len(cref.split())
        if "," in part or "," in cref:
            problems.append(f"{mpn}: a comma in the part number or reference breaks the Digi-Key upload")
        f.write(f"{qty},{part},{cref}\r\n")
if no_dk:
    print("WARNING: no Digi-Key PN in bom_sources.csv, MPN used for:", ", ".join(no_dk))
held = sorted(m for m, r in src.items() if r.get("In hand", "").lower() == "yes")
print("BOM lines:", len(rows), "| Digi-Key upload lines:", len(upload), "for", BOARDS, "board(s):", os.path.basename(dk),
      "| in hand, not uploaded:", ", ".join(held) or "none")
if problems:
    print("BOM PROBLEMS:\n  " + "\n  ".join(problems))
    sys.exit(1)
