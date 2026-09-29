# HERON clock board (rev A): 10 MHz + PPS distribution

This board gives a common 10 MHz reference and a common PPS to the four
SDRs (2× Ettus B210, 2× Ettus B200). See D-025 in `docs/DECISIONS.md`.

- 10 MHz source: SiTime SiT5155 Super-TCXO, ±0.5 ppm (free-running, not GPS-locked).
  It is the fixed-frequency TCXO version (SiT5155AI-FK-33E0-10.000000). SiTime sets it at the
  factory. It has no I2C, and the board has no I2C connection (D-026).
- PPS source: an external GNSS receiver. The board buffers its PPS to four outputs.
- 4-layer, 84 × 56 mm, 1.6 mm, four M3 holes. KiCad 7 files (open in KiCad 7, 8 or 9).

## Block diagram

```
5 V (JST-GH J1) -> PTC F1 -> TVS D1 -> Schottky D2 -> ferrite FB1 -> VIN_5V
VIN_5V -> TPS7A2033 U1 -> +3V3_OSC (SiT5155 only)
VIN_5V -> TPS7A2033 U2 -> +3V3_CLK (buffers, LEDs)

SiT5155 U3 (10 MHz LVCMOS) -> 22R -> LMK1C1104 U4 (1:4, 50R out)
  each output: 0R -> 100 nF DC block -> 5th-order LPF -> 3 dB pad -> ESD -> SMA
  J3 B210_1   J4 B210_2   J5 B200_1   J6 B200_2        (top edge)

PPS in SMA J2 (left edge) -> [49.9R DNP] -> ESD -> 100R -> SN74LVC1G17 U5
  -> SN74LVC125A U6 -> 22R -> ESD -> SMA
  J7 B210_1   J8 B210_2   J9 B200_1   J10 B200_2       (bottom edge)
```

## Key numbers

| Item | Value | Source |
| --- | --- | --- |
| 10 MHz level at a 50 Ω load | ≈ +7 dBm (1.4 Vpp), sine | `sim/lpf.py` |
| 10 MHz level into high impedance | ≈ 2.9 Vpp | `sim/lpf.py` |
| B200/B210 REF IN limit | +15 dBm max (3.5 Vpp into 50 Ω) | Ettus KB |
| LPF | 0.1 dB Chebyshev, n = 5, fc ≈ 13 MHz, 50 Ω: 270 pF / 820 nH / 470 pF / 820 nH / 270 pF | `sim/lpf.py` |
| LPF rejection (model, ideal parts) | 20 MHz −22 dB, 30 MHz −43 dB, 100 MHz −98 dB re. 10 MHz | `sim/lpf.py` |
| 10 MHz loss, ±5 % part tolerance | −3.34 to −3.52 dB (includes the 3 dB pad) | Monte Carlo in `sim/lpf.py` |
| PPS out | 3.3 V CMOS, DC coupled, 22 Ω series | B2x0 PPS IN accepts 1.8–5 V |
| PPS in | 5 V tolerant (SN74LVC1G17), 100 Ω series, 10 k pull-down | |
| Supply | 5 V, ≈ 100 mA estimate | |

Why a low-pass filter: harmonics of a 10 MHz square wave fall at 1170/1180 MHz
and 1570/1580 MHz. These are inside the recorded L5 and L1 bands. The filter
makes each output a near-sine and removes most harmonic energy from the cables.

## Stackup

| Layer | Use |
| --- | --- |
| F.Cu | Components, clock traces (0.3–0.35 mm, ≈ 50 Ω over In1), GND pour |
| In1.Cu | Solid GND |
| In2.Cu | +3V3_OSC and +3V3_CLK traces, GND fill |
| B.Cu | PPS_BUF trunk, GND pour |

Use a standard 4-layer 1.6 mm stackup with ≈ 0.2 mm prepreg between F.Cu and In1
(for example JLC04161H-7628). At 10 MHz the exact impedance is not critical.

## Assembly (in-house pick-and-place and reflow)

1. **U3 SiT5155: use water-soluble flux only. Do not use no-clean flux. Do not
   use ultrasonic or megasonic cleaning.** (SiTime datasheet, manufacturing guidelines.)
2. Use an IPC/JEDEC J-STD-020 reflow profile.
3. All parts are on the top side. `fab/heron_clock_cpl_top.csv` has the
   placement data. The origin is the bottom-left board corner.
4. Fiducials (board coordinates from the top-left corner, mm): FID1 (10.0, 3.0),
   FID2 (80.0, 10.0), FID3 (80.0, 46.0).
5. The nine edge-launch SMAs (Amphenol 132289) straddle the board edge. Fit them
   last. Solder the ground legs on both sides.
6. R23 (49.9 Ω PPS termination) is DNP. Fit it only if the GNSS PPS source needs a 50 Ω load.
7. J1 pin 1 = +5 V, pin 2 = GND (JST GH, SM02B-GHS-TB). Check the harness polarity.

## Bring-up checklist

1. Power from a current-limited 5 V supply (limit 250 mA). Expect ≈ 60–110 mA.
2. Measure TP1 (VIN_5V ≈ 4.6–4.8 V after the Schottky), TP2 (+3V3_OSC), TP3 (+3V3_CLK). The green PWR LED is on.
3. Measure TP4 (CLK10_IN): 10 MHz square, 3.3 V. TP5 is OSC_OE (high = on).
4. Terminate each J3–J6 in 50 Ω. Measure ≈ 1.4 Vpp sine at 10 MHz. Look at harmonics on a spectrum analyzer.
5. Feed PPS into J2. The yellow PPS LED blinks (visible if the pulse is ≥ ≈ 10 ms). Check 3.3 V pulses on J7–J10.
6. Connect to the SDRs. Run with `clock_source = external`, `time_source = external`. Check that UHD reports the reference locked on all four units.

## Files

| Path | Content |
| --- | --- |
| `project/heron_clock.kicad_pro/.kicad_sch/.kicad_pcb` | KiCad project |
| `project/HERON_Clock.kicad_sym`, `project/HERON_Clock.pretty/` | Project library: SiT5155, LMK1C1104, TPS7A2033, 74LVC125 (single unit) symbols; SiT5155 footprint (datasheet Rev 1.05 p.34 land pattern) |
| `project/fp-lib-table`, `project/sym-lib-table` | Project library tables (use `${KIPRJMOD}`) |
| `project/fab/` | Gerber zip, drill, BOM, CPL, schematic PDF, assembly PDF, DRC report |
| `design.py` | Schematic content (parts, values, nets) |
| `gen_lib.py`, `gen_sch.py`, `gen_pcb.py`, `route.py` | Generators for the library, schematic and PCB |
| `check_net.py`, `make_bom.py`, `make_fab.sh` | Netlist check, BOM, full rebuild |
| `sim/lpf.py` | Filter and output-level model |

The generators made rev A. **If you edit the KiCad files by hand, the KiCad
files become the source of truth. Do not run `make_fab.sh` after that**, because it
overwrites the schematic and the PCB.

## Verification status

| Check | Result |
| --- | --- |
| KiCad DRC (KiCad 7.0.11) | 0 errors, 0 unconnected pads. 24 warnings: silkscreen lines of stock footprints over pads (the fab clips them). Re-run 2026-09-28 after the U3 layout change: same result. |
| U3 layout vs. SiT5155 datasheet (p.35) | 2026-09-28: C8 (100 nF) pad centre is 1.7 mm from pin 9 (VDD), pad gap 0.8 mm. C9 (10 µF) is within 6 mm. Six GND vias at U3: one under the body, one at C8, four at the GND/NC pads (pins 2, 3, 4, 7). No trace of another net runs under U3. |
| Netlist connectivity (`check_net.py`) | All nets have ≥ 2 nodes, except the intentional NC pins (U1.4, U2.4, U5.1). |
| ERC | Not run: the KiCad 7 CLI has no ERC. **Run ERC in KiCad before you order.** |
| IC pinouts | Checked against datasheets: SiT5155 (Table 13 + p.34), LMK1C1104 (PW), TPS7A2033 (DBV), SN74LVC1G17 (DBV), SN74LVC125A (PW), TPD1E05U06 (DPY). |
| Hardware test | Not done. |

## Layout rules for U3 (SiT5155)

Keep these when you move parts near U3 (datasheet Rev 1.05, p.9 note 9 and p.35):

- C8 (100 nF) stays within 1–2 mm of pin 9 (VDD). The supply goes into C8 first, then into pin 9.
- C9 (10 µF) stays within 50 mm (2 in) of U3.
- NC pins connect to GND. Keep several GND vias at the GND/NC pads. Do not put vias inside the pads.
- Do not route traces of other nets under U3.

## Open items

- The 10 MHz is free-running (±0.5 ppm over temperature, ±1 ppm initial). All four SDRs share it, so they stay coherent with each other. It is not GPS-disciplined.
- The PPS source (GNSS receiver model and PPS connector) is still open. PPS edges and the 10 MHz are not phase-locked; UHD latches time on the first sample clock after the PPS edge.
- Mass and mounting on the payload: check on the bench.
