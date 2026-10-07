# HERON clock board (rev B): 10 MHz + PPS distribution, rack version

Rev B has the same circuit as rev A (`../clock_board/`, D-025, D-026). It has a new
board shape and new connectors so that it fits the payload rack
(`../payload_rack/`). Rev A stays as it is.

**Status: proposed. Not ordered. Not built. Not yet in `docs/DECISIONS.md`.**

## What changed from rev A

| Item | Rev A | Rev B |
| --- | --- | --- |
| Board | 84 × 56 mm | 118 × 44 mm, 4 layers, 1.6 mm, four M3 holes |
| Connectors J2–J10 | Amphenol 132289 edge-launch SMA | J3–J10: Amphenol RF SMP-MSSB-PCT10T, SMP smooth-bore jack (THT legs in oversize holes, SMD signal tab), direct blind-mate to the SDRs (D-027). J2: Amphenol 132134 vertical SMA |
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
- **Blind-mate (D-027).** Each SDR rear SMA gets one adapter: Cinch/Johnson 134-1019-451, SMA plug to
  SMP female. When the SDR tray slides in, the adapter plugs directly onto the board jack. There is no
  bullet and there are no jumpers.
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

- Part: Amphenol RF **SMP-MSSB-PCT10T** (2026-10-07; was SMP-MSSB-PCT, about $29, now about $7).
  The four ground legs are through-hole. The signal contact is a surface-mount tab that leaves the
  body on one side. The part ships with a pick-and-place cap: remove it before you mate a jack.
- Footprint `HERON_Clock:SMP_Amphenol_SMP-MSSB-PCT10T_Vertical_Float`, made by `gen_lib.py`. The origin
  is on the jack axis. Pad 1 is the tab pad on +x; pads 2 are the legs.
- Dimensions come from the Amphenol customer outline drawing SMP-MSSB-PCT10T rev A: four round
  ground legs ø0.99 mm on a 5.08 mm square pitch, 2.49 mm long (0.9 mm through the 1.6 mm board),
  5.99 mm square body, tab 0.38 mm wide and 0.55 mm outside the body, mating face 4.09 mm above the
  board. Push-on force 9 N max, release force 2.2 N min (smooth bore). Rated -40 to +85 °C.
- Holes: leg + 0.2 mm + 2 × float. Float is ±0.20 mm (D-027): leg holes 1.59 mm, 0.30 mm annular ring.
  These holes are larger than IPC recommends. The joint relies on the fillet and the ring, not on
  full barrel fill. Make a sample joint first.
- Tab pad: 2.23 × 0.83 mm, from 2.4 to 4.63 mm off the axis (Amphenol's pad, plus 0.2 mm inward and
  enough width for ±0.2 mm float of the 0.38 mm tab). No paste: the tab is soldered by hand.
- A copper keep-out (ø4.24 mm circle and a 2.73 mm slot along the tab, from the drawing) is in the
  footprint. The signal contact runs along the bottom of the body there.
- All eight jacks have the tab toward +x. The 10 MHz traces leave the tab straight to the filter
  column. The PPS jacks have a dog-leg around the lower-right leg to the ESD diode (J7, J8, J10 on F.Cu;
  J9 through two vias and B.Cu, because the PPS4_Y trace passes next to J9).
- DRC warnings that remain by design: 8 courtyard overlaps between the 10 MHz jack tab side and the
  filter column (the real body and tab are at least 1 mm from those parts, and the jacks are fitted by
  hand after reflow), and starved thermal reliefs on some leg pads on F.Cu (the legs also connect to
  the solid GND plane on In1).
- The parameters are at the top of the SMP section in `gen_lib.py` (`SMP_FLOAT` and others).
- **Check before you order (Q-015):** the 2.49 mm legs on the 1.6 mm board leave 0.9 mm for the back
  fillet. Make a sample joint.

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
   1. Remove the pick-and-place cap from each jack. Mount the board on the rear plate. Put each SMP
      jack loose in its holes.
   2. Fit the adapters (134-1019-451) to the SDRs. Install all four SDR trays, so each adapter plugs
      onto its jack. Tighten each preload screw (see `../payload_rack/README.md`).
   3. Solder the ground legs of each jack from the back, through the rear-plate windows.
   4. Remove the SDRs. Solder the signal tab of each jack on the top side. Check the fillets.
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
| `bom_sources.csv` | Purchase data for each MPN: Digi-Key stock and price on the check date, notes. Also the off-board parts (8 Cinch 134-1019-451 SMA-to-SMP adapters). `make_bom.py` merges it into `fab/heron_clock_bom.csv` and fails when a fitted part has no MPN or no row. |
| `project/fab/heron_clock_bom_digikey.csv` | Digi-Key list upload: one product per line, `quantity,Digi-Key PN,references` (falls back to the MPN when `bom_sources.csv` has no Digi-Key PN), comma-delimited, no header. DNP parts and parts marked "In hand" in `bom_sources.csv` (the SiT5155) are left out. The 8 off-board adapters are included, and 5 spare R23 resistors (`Spare qty`). For more boards: `python make_bom.py heron_clock.net fab/heron_clock_bom.csv <boards>` in `project/`. |
| `sim/lpf.py` | Filter and output level model (unchanged) |

## Open items

- **U3 SiT5155:** the team has the parts in hand (2026-10-07). Digi-Key had no stock (estimate 2027-05-17).
- **U1/U2 LDOs (2026-10-07):** TPS7A2033PDBVR had no stock. U1 (+3V3_OSC, TCXO) is now an LP5907MFX-3.3
  (6.5-10 uVrms, output capacitance 0.7-10 uF). U2 (+3V3_CLK) is now a TLV75533PDBVR (71.5 uVrms,
  1-200 uF), because +3V3_CLK has about 13 uF, above the LP5907 limit. Same SOT-23-5 pinout, no layout
  change. Both have a 5.5 V maximum input: keep the payload 5 V rail below about 5.8 V. Check their stock.
- Five parts changed on 2026-10-05 (approved 2026-10-07) because of obsolete or out-of-stock parts (see `design.py` and
  `bom_sources.csv`). D2 is now a Panjit SS1030HEWS (SOD-323HE). Check its land pattern against the
  SOD-323F footprint.

- Which rear SMA of the B2x0 is 10 MHz and which is PPS (see above, Q-015).
- The adapter reach (`ADAPTER_REACH`, measure it with the adapter fitted and mated) and the board thickness check (Q-015).
- The PPS source (GNSS receiver model) is still open. J2 (PPS IN) faces forward, between the B200_1 and B200_2
  jack columns. Route its cable inside the rack.
- The 10 MHz is free-running, the same as rev A.
