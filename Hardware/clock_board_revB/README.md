# HERON clock board (rev B): 10 MHz + PPS distribution, rack version

Rev B has the same circuit as rev A (`../clock_board/`, D-025, D-026). It has a new
board shape and new connectors so that it fits the payload rack
(`../payload_rack/`). Rev A stays as it is.

**Status: proposed. Not ordered. Not built. Not yet in `docs/DECISIONS.md`.**

## What changed from rev A

| Item | Rev A | Rev B |
| --- | --- | --- |
| Board | 84 × 56 mm | 118 × 44 mm, 4 layers, 1.6 mm, four M3 holes |
| Connectors J2–J10 | Amphenol 132289 edge-launch SMA | J3–J10: Amphenol RF SMP-MSLD-PCT, SMP limited-detent THT jack in oversize holes, blind-mate to the SDRs (D-027). J2: Amphenol 132134 vertical SMA |
| Connector layout | 10 MHz on one edge, PPS on the other edge | One jack pair for each SDR, on a 30 mm pitch |
| J1 (5 V, JST-GH) | Left edge | Top edge, opening up |
| Routing | All by hand (`route.py`) | Clusters by hand (copied from rev A), the rest by Freerouting |
| Schematic | — | Same parts, values and nets. Only the 9 jack footprints changed (checked against the rev A netlist). |
| Tool | KiCad 7 | KiCad 10 (files saved in the KiCad 10 format) |

## Fit in the rack

- The board mounts on the rack rear plate with four M3 × 8 mm standoffs. The
  component side faces forward, toward the SDRs.
- Each SDR has two jacks. The jacks are coaxial with the rear reference SMAs of that SDR. The rack
  model reads the jack positions from this KiCad file. The error is 0.000 mm on all 8 jacks.
- **Blind-mate (D-027).** Each SDR rear SMA gets an SMA-male to SMP-male smooth-bore adapter. An SMP
  female–female bullet sits in each board jack. When the SDR tray slides in, the adapter mates with
  the bullet. There are no jumpers.
- The board-to-SDR distance comes from the rack parameters `ADAPTER_REACH`, `BULLET_L` and
  `SMP_JACK_H` (`../payload_rack/heron_rack.py`). `ADAPTER_REACH` is a placeholder. Measure it (Q-015).
- Rack coordinates: world X = 46 + x, world Z = 54 − y. The rack model gives the world Y of the board face.

| SDR | Upper jack (PPS) | Lower jack (10 MHz) |
| --- | --- | --- |
| B210_1 | J7 (13.16, 10.84) | J3 (13.16, 30.78) |
| B210_2 | J8 (43.16, 10.84) | J4 (43.16, 30.78) |
| B200_1 | J9 (73.16, 10.84) | J5 (73.16, 30.78) |
| B200_2 | J10 (103.16, 10.84) | J6 (103.16, 30.78) |

Board coordinates are in mm, with (0, 0) at the top-left corner, seen from the component side.

**Verify before you order:** the rack model assumes that the two adjacent rear SMAs
of each B2x0 (14.2 mm and 34.2 mm from the board corner) are 10 MHz (lower) and PPS (upper). Read the
silkscreen on one SDR. A direct mate cannot cross cables. If the order is different, swap `Y_PPS` and
`Y_REF` in `gen_pcb.py` (or the nets of J3–J6 and J7–J10) before you order.

## SMP jack footprint (J3–J10)

- Footprint `HERON_Clock:SMP_Amphenol_SMP-MSLD-PCT_Vertical_Float`, made by `gen_lib.py`. The origin
  and pad 1 are on the jack axis.
- Lead sizes come from the Amphenol 3D model (IGES): centre pin ø0.71 mm, four corner ground legs
  0.95 mm square at (±2.52, ±2.52) mm, 6.0 mm square body, mating face 4.09 mm above the board.
  The pin and the legs come out 0.9 mm and 1.4 mm through the back of the 1.6 mm board.
- Holes: lead + 0.2 mm + 2 × float. Float is ±0.20 mm (D-027): centre hole 1.31 mm, leg holes 1.90 mm,
  0.30 mm annular ring. These holes are larger than IPC recommends. The joint relies on the fillet
  and the ring, not on full barrel fill. Make a sample joint first.
- The parameters are at the top of the SMP section in `gen_lib.py` (`SMP_FLOAT` and others).
- **Verify before you order (Q-015):** compare the lead sizes with the Amphenol customer drawing.
  A distributor listing gives a 1.57 mm maximum board thickness. The board is 1.6 mm.

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
| KiCad DRC (KiCad 10.0.6, with schematic parity) | 0 errors, 0 unconnected pads, 0 parity issues. 3 warnings: silkscreen clipped by the solder mask (cosmetic, the same as before the SMP change). 75 `lib_footprint_mismatch` notes: the board keeps the KiCad 7 copies of the stock footprints. These notes are not errors. |
| SMP change (2026-10-05) | J3–J10 were swapped in place in the PCB. All routes stayed. The ground pours were refilled. The board title text moved 0.5 mm, clear of the larger pads. |
| Netlist (`check_net.py`) | All nets have 2 or more nodes. Exceptions: the intentional NC pins U1.4, U2.4 and U5.1. |
| Netlist compared to rev A | All 51 nets are the same. Only the footprints of J2–J10 are different. |
| Fit in the rack (`../payload_rack/check_fit.py`) | No interference. |
| ERC | Not run. **Run ERC in the KiCad GUI before you order.** |
| Hardware test | Not done. Use the rev A bring-up checklist. |

## Assembly

1. **U3 SiT5155: use water-soluble flux only. Do not use no-clean flux. Do not use
   ultrasonic or megasonic cleaning.** (SiTime manufacturing guidelines.)
2. Reflow all SMD parts on the top side. `fab/heron_clock_cpl_top.csv` has the placement data.
3. Solder J2 (132134 SMA) by hand after reflow.
4. **J3–J10 (SMP): solder in place in the rack.** This aligns each jack to its own SDR.
   1. Mount the board on the rear plate. Put each SMP jack loose in its holes, with a bullet in it.
   2. Fit the adapters to the SDRs. Install all four SDR trays. Set each rear stop and tighten each
      preload screw (see `../payload_rack/README.md`).
   3. Solder one ground leg of each jack from the back, through the rear-plate windows.
   4. Remove the SDRs. Solder the other legs and the centre pin. Check the fillets.
   5. Do not move an SDR to a different slot after this. Mark the slots.
5. R23 (49.9 Ω PPS termination) is DNP. Fit it only if the GNSS PPS source needs a 50 Ω load.
6. J1 pin 1 = +5 V, pin 2 = GND.

## Rebuild

The generators make all the KiCad files and fab outputs.

**Export only (the normal case now).** The board was edited after the last full run (the SMP swap).
Make the fab outputs from the KiCad files as they are:

```
EXPORT_ONLY=1 KICAD_CLI=kicad-cli PYTHON=python3 bash make_fab.sh
```

On Windows, set `KICAD_CLI` and `PYTHON` to `kicad-cli.exe` and `python.exe` in the KiCad 10 `bin`
folder, and run the script in Git Bash.

**Full rebuild.** You need:

- KiCad 7 with its Python module (`pcbnew`) and `kicad-cli`. The full rebuild was not tested with KiCad 10.
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

- Which rear SMA of the B2x0 is 10 MHz and which is PPS (see above, Q-015).
- The adapter reach, the adapter part, the bullet length and the Amphenol drawing check (Q-015).
- The PPS source (GNSS receiver model) is still open. J2 (PPS IN) faces forward, between the B200_1 and B200_2
  jack columns. Route its cable inside the rack.
- The 10 MHz is free-running, the same as rev A.
