"""Add the mounting holes H5 and H6 to the rev D schematic (D-043).

Why: the direct-mate SMP jacks push on the board when the SDR trays slide in.
Two more M3 standoffs, at the middle of the top and bottom long edges (in line
with the corner standoffs), support the board there. The holes are parts in
the schematic (like H1-H4), so the PCB keeps 0 schematic parity issues.

What it does: copies the H4 symbol and its GND power symbol (the GND symbol
sits on the hole pin, there is no wire) to a second row 12.7 mm under H1 and
H2: H5 at (25.4, 269.24), H6 at (38.1, 269.24). design.py places them at the
same points. The UUIDs come from uuid5 of the reference, so a re-run gives
the same file. A second run finds H5 and stops.

Run with any Python 3 from this folder:

    python3 add_center_holes.py
"""

import os
import re
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
SCH = os.path.join(HERE, "project", "heron_clock.kicad_sch")
NS = uuid.UUID("6f1c2c4e-5d0b-4f0e-9a51-0d7d1e2c0d43")      # fixed namespace for the new UUIDs
NEW = {"H5": (25.4, 269.24), "H6": (38.1, 269.24)}
SRC_REF, SRC_AT = "H4", (63.5, 256.54)                       # the block that is copied
PIN_DY = 2.54                                                # GND symbol: on the hole pin, 2.54 mm below


def block_at(text, lib_id, at):
    """Return (start, end) of the top-level symbol block with this lib_id and position."""
    m = re.search(r'\n\t\(symbol\n\t\t\(lib_id "%s"\)\n\t\t\(at %s %s ' % (re.escape(lib_id), at[0], at[1]), text)
    if not m:
        raise SystemExit(f"{lib_id} at {at} not found")
    start = m.start() + 1
    end = text.find("\n\t)\n", start) + 4
    return start, end


def moved(block, src, dst, ref_old, ref_new, tag):
    """Copy of a symbol block moved from src to dst, with new references and UUIDs."""
    dx, dy = round(dst[0] - src[0], 4), round(dst[1] - src[1], 4)

    def shift(m):
        x, y = float(m.group(1)) + dx, float(m.group(2)) + dy
        return f"(at {round(x, 4):g} {round(y, 4):g}"
    out = re.sub(r"\(at ([\d.]+) ([\d.]+)", shift, block)
    out = out.replace(f'"{ref_old}"', f'"{ref_new}"')
    n = [0]

    def new_uuid(_m):
        n[0] += 1
        return f'(uuid "{uuid.uuid5(NS, f"{tag}-{n[0]}")}")'
    return re.sub(r'\(uuid "[0-9a-f-]+"\)', new_uuid, out)


def main():
    text = open(SCH, encoding="utf-8").read()
    if '"Reference" "H5"' in text:
        print("H5 is already in the schematic, nothing to do")
        return
    h_start, h_end = block_at(text, "Mechanical:MountingHole_Pad", SRC_AT)
    g_start, g_end = block_at(text, "power:GND", (SRC_AT[0], round(SRC_AT[1] + PIN_DY, 2)))
    hole, gnd = text[h_start:h_end], text[g_start:g_end]
    pwr = max(int(n) for n in re.findall(r'"#PWR(\d+)"', text))
    gnd_ref = re.search(r'"(#PWR\d+)"', gnd).group(1)
    add = ""
    for ref, at in NEW.items():
        pwr += 1
        add += moved(hole, SRC_AT, at, SRC_REF, ref, ref)
        add += moved(gnd, (SRC_AT[0], SRC_AT[1] + PIN_DY), (at[0], round(at[1] + PIN_DY, 2)),
                     gnd_ref, f"#PWR{pwr:03d}", f"{ref}-gnd")
    text = text[:h_end] + add + text[h_end:]
    open(SCH, "w", encoding="utf-8", newline="\n").write(text)
    print("added", ", ".join(NEW))


if __name__ == "__main__":
    main()
