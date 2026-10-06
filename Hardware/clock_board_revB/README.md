# HERON clock board (rev B): 10 MHz + PPS distribution, rack version

Rev B has the same circuit as rev A (`../clock_board/`, D-025, D-026). It has a new
board shape and new connectors so that it fits the payload rack
(`../payload_rack/`). Rev A stays as it is.

**Status: proposed. Not ordered. Not built. Not yet in `docs/DECISIONS.md`.**

## What changed from rev A

| Item | Rev A | Rev B |
| --- | --- | --- |
| Board | 84 × 56 mm | 118 × 44 mm, 4 layers, 1.6 mm, four M3 holes |
| SMA connectors J2–J10 | Amphenol 132289 edge-launch | Amphenol 132134 vertical THT jack |
| Connector layout | 10 MHz on one edge, PPS on the other edge | One jack pair for each SDR, on a 30 mm pitch |
| J1 (5 V, JST-GH) | Left edge | Top edge, opening up |
| Routing | All by hand (`route.py`) | Clusters by hand (copied from rev A), the rest by Freerouting |
| Schematic | — | Same parts, values and nets. Only the 9 SMA footprints changed (checked against the rev A netlist). |

## Fit in the rack

- The board mounts on the rack rear plate with four M3 × 8 mm standoffs. The
  component side faces forward, toward the SDRs.
- Each SDR has two jacks. The jacks are coaxial with the rear reference SMAs of that SDR. The rack
  model reads the jack positions from this KiCad file. The error is 0.000 mm on all 8 jacks.
- The gap between the jack tips and the SDR SMA tips is about 21 mm. Use flexible male–male
  RG316 jumpers, 50–75 mm long.
- Rack coordinates: world X = 46 + x, world Z = 54 − y. The board face is at world Y = 203.2.

| SDR | Upper jack (PPS) | Lower jack (10 MHz) |
| --- | --- | --- |
| B210_1 | J7 (13.16, 10.84) | J3 (13.16, 30.78) |
| B210_2 | J8 (43.16, 10.84) | J4 (43.16, 30.78) |
| B200_1 | J9 (73.16, 10.84) | J5 (73.16, 30.78) |
| B200_2 | J10 (103.16, 10.84) | J6 (103.16, 30.78) |

Board coordinates are in mm, with (0, 0) at the top-left corner, seen from the component side.

**Verify before you order:** the rack model assumes that the two adjacent rear SMAs
of each B2x0 (14.2 mm and 34.2 mm from the board corner) are 10 MHz and PPS. Read the silkscreen on
one SDR. If the order is different, the board still works. Cross the jumpers or use longer jumpers.

## Layout

```
 J1 (+5 V, top edge)
 H1 ┌───────────────────────────────────────────────────────────────────────────┐ H2
    │ B210_1        B210_2           B200_1             B200_2                   │
    │ [J7 PPS]      [J8 PPS]         [J9 PPS]           [J10 PPS]                │
    │   PPS chain |   PPS chain |  10 MHz  |  PPS chain |   PPS chain |          │
    │  10 MHz     │ LDOs and    │ filter   │ TCXO U3,   │ PPS input   │ 10 MHz   │
    │  filter     │ protection  │ column   │ LMK U4,    │ U5, LED     │ filter   │
    │  column     │ (bay 1)     │          │ PPS buf U6 │ (bay 3)     │ column   │
    │ [J3 10M]    [J4 10M]        [J5 10M]   (bay 2)   [J6 10M]                  │
 H3 └──────────────────────────────────────────────── [J2 PPS IN] ───────────────┘ H4
```

- Each 10 MHz filter column runs up, 6 mm to the right of its jack. The PPS output chain is
  directly under its PPS jack.
- The TCXO, buffer and LDO clusters keep the rev A placement and the rev A hand routes. The
  script moves them as rigid groups. The TCXO cluster turns 90°.
- U3 rules (SiT5155 layout guide): C8 is 1.69 mm from pin 9. A track keepout on In2 and B.Cu
  stops other nets under U3. The script checked that no track of another net is under U3.
- Stackup: the same as rev A. F.Cu has the signals and a GND pour. In1 is solid GND. In2 has
  +3V3_OSC and +3V3_CLK. B.Cu has a GND pour and a short PPS_BUF trunk.

## Verification status

| Check | Result |
| --- | --- |
| KiCad DRC (KiCad 7.0.11) | 0 errors, 0 unconnected pads. 3 warnings: silkscreen clipped by the solder mask (cosmetic, the same type as the 24 warnings of rev A). |
| Netlist (`check_net.py`) | All nets have 2 or more nodes. Exceptions: the intentional NC pins U1.4, U2.4 and U5.1. |
| Netlist compared to rev A | All 51 nets are the same. Only the footprints of J2–J10 are different. |
| Fit in the rack (`../payload_rack/check_fit.py`) | No interference. |
| ERC | Not run. **Run ERC in the KiCad GUI before you order.** |
| Hardware test | Not done. Use the rev A bring-up checklist. |

## Assembly

1. **U3 SiT5155: use water-soluble flux only. Do not use no-clean flux. Do not use
   ultrasonic or megasonic cleaning.** (SiTime manufacturing guidelines.)
2. Reflow all SMD parts on the top side. `fab/heron_clock_cpl_top.csv` has the placement data.
3. Solder the nine 132134 jacks (J2–J10) by hand after reflow.
4. R23 (49.9 Ω PPS termination) is DNP. Fit it only if the GNSS PPS source needs a 50 Ω load.
5. J1 pin 1 = +5 V, pin 2 = GND.

## Rebuild

The generators make all the KiCad files and fab outputs. You need:

- KiCad 7 with its Python module (`pcbnew`) and `kicad-cli`.
- Java 17 or later, Freerouting 1.9.0 (`freerouting-1.9.0.jar`, from GitHub), and Xvfb.
- `FREEROUTING_JAR` set to the path of the jar. Do not put the jar in git.

```
export KICAD7_FOOTPRINT_DIR=/usr/share/kicad/footprints KICAD7_SYMBOL_DIR=/usr/share/kicad/symbols
export FREEROUTING_JAR=/path/to/freerouting-1.9.0.jar
bash make_fab.sh
```

**Caution:** Freerouting can give different routes on each run. If you edit the KiCad files
by hand, the KiCad files become the source of truth. Do not run `make_fab.sh` after that,
because it overwrites the schematic and the PCB.

## Files

| Path | Content |
| --- | --- |
| `project/heron_clock.kicad_pro/.kicad_sch/.kicad_pcb` | KiCad project (rev B) |
| `project/fab/` | Gerber zip with drill files, BOM, CPL, schematic PDF, assembly PDF, DRC report, top view PNG |
| `design.py`, `gen_lib.py`, `gen_sch.py` | Schematic and library generators (the same as rev A, except the SMA footprint) |
| `gen_pcb.py` | Rev B placement, hand routes, autorouting, pours and DRC |
| `check_net.py`, `make_bom.py`, `make_fab.sh` | Netlist check, BOM, full rebuild |
| `sim/lpf.py` | Filter and output level model (unchanged) |

## Open items

- Which rear SMA of the B2x0 is 10 MHz and which is PPS (see above).
- The PPS source (GNSS receiver model) is still open. J2 (PPS IN) faces forward, between the B200_1 and B200_2
  jack columns. Route its cable inside the rack.
- The 10 MHz is free-running, the same as rev A.
