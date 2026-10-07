# HERON clock board (rev C): 10 MHz + PPS distribution, rack version, larger parts

Rev C is rev B (`../clock_board_revB/`) with larger small parts, so that the board is easier to
assemble in-house (D-028). The circuit, the values, the nets, the board outline, the holes and the
jack positions do not change. Rev B stays as it is.

**Status: proposed. Not ordered. Not built.**

## What changed from rev B

| Item | Rev B | Rev C |
| --- | --- | --- |
| Resistors and capacitors | 49 parts in 0402 | All 0603, same values. Same makers, except the 22 R: Stackpole RMCF0603FT22R0 (the YAGEO 0603 was out of stock on 2026-10-07). The MPNs are in `design.py` and `bom_sources.csv`. |
| ESD diodes D4–D13 (9 parts) | TPD1E05U06**DPYR**, X1SON 0.6 × 1 mm | TPD1E05U06**DYAR**, SOD-523 1.6 × 0.8 mm. The same TI part in a larger package: 0.5 pF, 5.5 V. Same pinout: pin 1 = I/O, pin 2 = GND (TI datasheet Table 4-1, `../datasheets/tpd1e05u06.pdf`). |
| ESD footprint | `Package_SON:Texas_DPY0002A_0.6x1mm_P0.65mm` | KiCad stock `Diode_SMD:D_SOD-523` (0.6 × 0.7 mm pads, 2.0 mm span). It fully covers the TI example land (0.4 × 0.67 mm pads, 1.48 mm span). The larger pads are easier to solder. |
| 10 MHz filter columns | Shunt parts across the signal line | Shunt parts upright beside the line (see Layout). The column keeps the rev B length. |
| PPS output chains | 22 R upright under the ESD diode | 22 R flat. J7, J8, J10: in the row at y 17.53. J9: left of its ESD diode. |
| Smallest parts | 0402, X1SON | 0603, SOD-523. The finest-pitch parts that remain have no larger package: U3 SiT5155 (5 × 3.2 mm, 10 pads), U4 and U6 (TSSOP, 0.65 mm pitch), SOT-23-5. |
| Source of the KiCad files | Generators, then the SMP swap script | `make_revc.py`. It reads the rev B KiCad files and writes the rev C files. |

## Fit in the rack

- The board mounts on the rack rear plate with four M3 × 8 mm standoffs. The
  component side faces forward, toward the SDRs.
- Each SDR has two jacks. The jacks are coaxial with the rear reference SMAs of that SDR. Rev C does
  not move the jacks. The rack model still reads the rev B KiCad file (see Open items).
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
silkscreen on one SDR. A direct mate cannot cross cables. If the order is different, swap the nets of
J3–J6 and J7–J10 before you order.

## SMP jack footprint (J3–J10)

The jacks and their footprint are the same as rev B.

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
- DRC warning that remains by design: starved thermal reliefs on one J10 leg pad on F.Cu (the legs
  also connect to the solid GND plane on In1). The 8 rev B courtyard overlaps between the 10 MHz jack
  tab side and the filter column are gone in rev C, because no shunt part is on the tab side now.
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
- **Filter column (rev C).** The series parts sit on the signal line, all with pin 1 at the top:
  0 R, 100 nF, 820 nH, 820 nH, 17.4 R, and the last 294 R (pin 2 to GND). Their courtyards have
  0.05 mm space between them. The 10 MHz jack tab track joins the line between the 17.4 R and the last
  294 R, at y 30.78, as in rev B. The shunt parts stand upright beside the line. Each has a short stub
  from its node: 270 pF (left, up), 470 pF (left, down), 270 pF (right, up), 294 R (right, down) and the
  ESD diode (right, down). Each GND pad has its own via. The numbers are at the top of the filter
  section in `make_revc.py` (`SERIES_DY`, `SHUNTS`).
- Four tracks pass under the first inductor of a column: REF1_Y and PPS2_Y under L3, REF4_Y and
  PPS_BUF under L5. Two tracks share each inductor gap (0.25 mm tracks, 0.2 mm to the pads).
- **PPS outputs (rev C).** Each ESD diode keeps pin 1 on the rev B line, so the jack dog-leg stays. Its
  GND via moved 0.8 mm to the right, out of the larger pad. The 22 R lies flat: J7, J8 and J10 in the
  row at y 17.53 (PPS1_Y gets a new via right of R25), J9 to the left of D12 (PPS4_Y and REF3_Y run
  just below that spot).
- The TCXO, buffer and LDO clusters keep the rev B placement. Small moves made room for the larger
  parts: C2, R2 (with its +3V3_OSC via), D3, C28, R4 (with its +3V3_CLK via, and the B.Cu PPS_BUF trunk
  0.34 mm to the right), and R21–R23 and D8 at the PPS input. R3 (22 R, TCXO to buffer) lies flat
  between U4 and U3, because the gap is only 2.76 mm.
- U3 rules (SiT5155 layout guide): the C8 pad centre (now 0603) is 1.79 mm from pin 9, with a 0.63 mm pad
  gap. C9 is 5.5 mm from U3. A track keepout on In2 and B.Cu stops other nets under U3. No track or
  via of another net is under U3 (checked 2026-10-07).
- Stackup: the same as rev A. F.Cu has the signals and a GND pour. In1 is solid GND. In2 has
  +3V3_OSC and +3V3_CLK. B.Cu has a GND pour and a short PPS_BUF trunk.

## Verification status

| Check | Result |
| --- | --- |
| KiCad DRC (KiCad 10.0.6, with schematic parity, 2026-10-07) | 0 errors, 0 unconnected pads, 0 parity issues. Warnings: 3 starved thermal reliefs (one J10 leg, the same as rev B) and 2 silkscreen texts clipped by the solder mask (cosmetic, the same as rev B). 26 `lib_footprint_mismatch` notes: the board keeps the KiCad 7 copies of the stock footprints that did not change. These notes are not errors. |
| ERC (KiCad 10.0.6 `kicad-cli sch erc`, 2026-10-07) | 0 errors, 0 warnings. (Rev B had no ERC run.) |
| Netlist (`check_net.py`) | All nets have 2 or more nodes. Exceptions: the intentional NC pins U1.4, U2.4 and U5.1. |
| Netlist compared to rev B | All 51 nets have the same names and the same pins. |
| `design.py` compared to the schematic | All 110 parts have the same value, footprint, manufacturer and MPN. |
| U3 layout rules | Pass. See Layout. |
| Fit in the rack (`../payload_rack/check_fit.py`) | Not run for rev C. The rack model still reads the rev B board. The jacks did not move. |
| Hardware test | Not done. Use the rev A bring-up checklist. |

## Assembly

1. **U3 SiT5155: use water-soluble flux only. Do not use no-clean flux. Do not use
   ultrasonic or megasonic cleaning.** (SiTime manufacturing guidelines.)
2. Reflow all SMD parts on the top side. `fab/heron_clock_cpl_top.csv` has the placement data.
   The smallest passive parts are 0603. The ESD diodes are SOD-523. Pin 1 (I/O) connects to the
   signal and pin 2 to GND. Compare the pin-1 mark on the part (TI DYA drawing) with the assembly
   drawing before you place them.
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

`make_revc.py` makes the rev C schematic and PCB from the rev B KiCad files. `make_fab.sh` runs it.
Then it exports the netlist, runs DRC and ERC, and makes the fab outputs. You need KiCad 10: `kicad-cli`,
and the Python that has the `pcbnew` module.

```
KICAD_CLI=kicad-cli PYTHON=python3 bash make_fab.sh
```

On Windows, set `KICAD_CLI` and `PYTHON` to `kicad-cli.exe` and `python.exe` in the KiCad 10 `bin`
folder, and run the script in Git Bash.

- `make_revc.py` stops with a message when the rev B file does not have a track or via that it
  expects. Then rev B changed: check the change before you run the script again.
- **If you edit the rev C KiCad files by hand, they become the source of truth.** Then use
  `EXPORT_ONLY=1`, which skips `make_revc.py`. Without it, `make_revc.py` overwrites the rev C
  schematic and PCB.

## Files

| Path | Content |
| --- | --- |
| `project/heron_clock.kicad_pro/.kicad_sch/.kicad_pcb` | KiCad project (rev C) |
| `project/fab/` | Gerber zip with drill files, BOM, CPL, schematic PDF, assembly PDF, DRC and ERC reports, top view PNG |
| `make_revc.py` | Rev B to rev C: footprint and MPN change, filter column and PPS output placement and routes, small moves, clean-up of loose GND stubs and blocked stitching vias |
| `design.py` | The parts and nets as Python (rev C parts). It matches the schematic (checked). It uses `gen_sch.py`; `gen_lib.py` makes the project library. Both are the rev B generators (KiCad 7 libraries). |
| `check_net.py`, `make_bom.py`, `make_fab.sh` | Netlist check, BOM, rebuild and fab outputs |
| `bom_sources.csv` | Purchase data for each MPN: Digi-Key stock and price on the check date, notes. Also the off-board parts (8 Cinch 134-1019-451 SMA-to-SMP adapters). `make_bom.py` merges it into `fab/heron_clock_bom.csv` and fails when a fitted part has no MPN or no row. |
| `project/fab/heron_clock_bom_digikey.csv` | Digi-Key list upload: one product per line, `quantity,Digi-Key PN,references` (falls back to the MPN when `bom_sources.csv` has no Digi-Key PN), comma-delimited, no header. DNP parts and parts marked "In hand" in `bom_sources.csv` (the SiT5155) are left out. The 8 off-board adapters are included, and 5 spare R23 resistors (`Spare qty`). For more boards: `python make_bom.py heron_clock.net fab/heron_clock_bom.csv <boards>` in `project/`. |
| `sim/lpf.py` | Filter and output level model (unchanged: the values did not change) |

## Open items

- **Rev C part numbers (2026-10-07):** the 12 new 0603 and SOD-523 MPNs have no Digi-Key part number,
  stock or price in `bom_sources.csv` yet. The Digi-Key upload uses the MPN for them. Check them before
  you order.
- **Rack model:** `../payload_rack/heron_rack.py` and `check_fit.py` still read the rev B board. Point them
  to rev C when the team selects rev C to order, and run `check_fit.py` again.
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
