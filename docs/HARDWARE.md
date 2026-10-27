# HARDWARE — HERON Hardware Sheet

Status: Draft 4, 2026-09-24. Update this sheet when hardware changes.
The software must never depend on values here that are not also in a
config file.

## 1. Payload computer

| Item | Value | Notes |
| --- | --- | --- |
| Model | Intel NUC7i3DNB (board) | Core i3-7100U, 2 cores / 4 threads (D-013) |
| RAM | 8 GB | |
| OS drive | NVMe (M.2) | Ubuntu Server LTS, headless |
| Data drive | SATA SSD | IQ data only. SATA III ceiling ≈ 550 MB/s — size the signal plan below this with 2× margin |
| USB | USB 3.0 ports for all four SDRs (B210 and B200 are both USB 3.0) | Spread devices across controllers; check `lsusb -t` |
| Power | From drone power system, **TBD** — measure NUC + 4 SDR draw on the bench | Estimate from datasheets: section 6 |

The computer can change at any time (Q-005). A replacement must
support: four USB 3.0 ports on at least two controllers, NVMe + SATA
(or equal), and UHD 4.x on its OS.

### Data-rate sizing rule

Aggregate disk rate = sum over channels of (sample rate × 2 bytes at
sc8). Example: 2× B210 at 22 Msps × 2 ch = 176 MB/s, plus 2× B200
at 22 Msps × 1 ch = 88 MB/s, total ≈ 264 MB/s. This fits SATA III but
verify with the endurance test (`TESTING.md`). The exact plan is
Q-001. `heron-onboard check-config` prints this sum for a config.

## 2. Software-defined radios (D-008, D-020, D-024)

| Item | Value | Notes |
| --- | --- | --- |
| Units | 2× Ettus USRP B210, 2× Ettus USRP B200 (D-024) | Serial numbers: **TBD — record all four here and in `onboard.toml`** |
| B210 interface | USB 3.0, 2 RX channels each, separate REF IN and PPS IN | `clock_source = external`, `time_source = external` |
| B200 interface | USB 3.0, 1 RX channel, separate REF IN and PPS IN (the same sync inputs as the B210) | `clock_source = external`, `time_source = external` (D-024) |
| B200 size and mass | The B200 board (about 97 × 155 mm) is larger and heavier than the B200mini it replaces | Mounting, payload mass, and USB power draw: **TBD — check on the bench** |
| UHD support | UHD 4.x (apt on Ubuntu 22.04/24.04) runs all four | Q-011 closed |
| Sample format | sc8 (8-bit complex) files (D-009); sc16 on the USB wire by default | `capture.cpu_format`, `capture.wire_format` |
| Sync | HERON clock board (D-025, D-026): SiT5155 Super-TCXO 10 MHz (fixed frequency, factory-set, no I2C) and buffered GNSS PPS to all four units (D-014, D-024, D-025) | Design files: `Hardware/clock_board/` (rev A); current revision `Hardware/clock_board_revD/` (D-042). 4× 10 MHz SMA (≈ +7 dBm sine) and 4× PPS SMA (3.3 V). PPS source (GNSS receiver model): **TBD**. 5 V in on JST XH (D-040). |
| Time alignment | All four units: PPS-aligned to the same UTC second (D-024) | See `DATA_FORMATS.md` |
| Legacy settings | 22 Msps, sc8, L5 1176.45 MHz, gain 45, BW 20.322 MHz | `SURGE/NUC_scripts/SDR_backup_files/b210_split_settings-balloon.xml` |

## 3. Antennas and RF chain

| Item | Value | Notes |
| --- | --- | --- |
| Direct (up-looking) antenna(s) | **TBD** | RHCP typical for direct |
| Reflected (down-looking) antenna(s) | **TBD** | LHCP typical for reflection; the 1.02 deck plans L and R HCP |
| Antenna inventory | Four antennas on hand: two spirals, one patch, one helical | Photos, specifications, and open items: `ANTENNAS.md` |
| RF front end (draft) | Antenna → Mini-Circuits ZFBT-4R2G-FT+ bias tee (one per antenna) → B210 RX2 | Block diagram and gain/noise budget: `RF_FRONT_END.md` |
| LNAs / filters / splitters | **TBD** | Record gains and bias-tee use |
| Antenna-to-SDR-channel map | **TBD** | Must live in config (`antenna_label` per channel) and on physical labels |
| Signal plan | **Open (Q-001)**: bands per SDR | Keep in config only. The template puts L5 on one B210 + one B200 and L1 on the others as a placeholder |

## 4. Flight segment

| Item | Value | Notes |
| --- | --- | --- |
| Autopilot | Cube Orange, ArduPilot (D-004) | The payload does not use flight modes (D-012) |
| Payload control link | **Flight-controller telemetry link (D-016).** NUC ↔ Cube telemetry serial port (MAVLink 2, component 191); Cube ↔ ground via the existing telemetry radio / Herelink | Cube port and baud: Q-014. Default TELEM2 at 921600 |
| Drone airframe | Aurelia X6 (from SURGE; drone team confirms) | |

## 5. Payload mass budget (estimate, D-046)

Status: **estimate only**. The values come from vendor datasheets and
from the rack CAD. Nobody has weighed an item yet. When you weigh an
item, write the measured mass in its row and change the source to
"measured, date".

| Item | Qty | Each (g) | Total (g) | Source |
| --- | --- | --- | --- | --- |
| Ettus USRP B210, board only | 2 | 350 | 700 | Ettus KB B200/B210 page; Digilent and Jameco listings (97 × 155 × 15 mm, 350 g) |
| Ettus USRP B200, board only | 2 | 350 | 700 | Ettus KB B200/B210 page (one value for both boards) |
| Payload rack, no boards (3 mm plywood + ASA/TPU prints) | 1 | 460 | 460 | `Hardware/payload_rack/README.md`, `out/mass.csv` (CAD volume × density) |
| Rack screws, inserts, standoffs | 1 | 40 | 40 | `Hardware/payload_rack/README.md` (estimate) |
| Rack copper tape | 1 | 25 | 25 | `Hardware/payload_rack/README.md` (estimate) |
| Bias tee Mini-Circuits ZFBT-4R2G-FT+ | 2 | 38 | 76 | `Hardware/datasheets/MiniCircuits-ZFBT-4R2G-FT-datasheet.pdf` (outline table, "wt. grams") |
| Flight antenna ArduSimple helical (ANT-04) | 1 | 18 | 18 | `ANTENNAS.md` section 5 (datasheet) |
| **Subtotal, items with a value** | | | **2019** | |
| NUC7i3DNB board + RAM + NVMe + cooler | 1 | **TBD** | — | Intel TPS has a weights section (2.5.2) for this board family; the value was not available. Weigh it |
| SATA data SSD | 1 | **TBD** | — | Model not recorded |
| Patch antenna Abracon APKG5012GD (ANT-03) | 1 | **TBD** | — | The datasheet gives no mass (50 × 50 × 12 mm, ceramic, 105 mm RG316 cable) |
| Clock board rev D, populated | 1 | **TBD** | — | No mass in the design files |
| PPS GNSS receiver and antenna | 1 | **TBD** | — | Model TBD (section 2) |
| Other antennas carried in flight | — | **TBD** | — | Antenna roles TBD (section 3) |
| USB 3.0 cables, RF cables, SMP adapters | — | **TBD** | — | Lengths not recorded |
| Power wiring and regulators | — | **TBD** | — | Power design TBD (section 1) |

Read the subtotal as a lower limit. The payload mass is more than
about 2.0 kg, because the TBD rows are not included. The NUC, the SSD,
and the cables are the largest open items.

The Ettus 350 g value is one value for "B200/B210". It is not clear
if it is the bare board. A bare board can be lighter.

## 6. Payload power budget (estimate, D-047)

Status: **estimate only**. The values come from vendor datasheets and
manuals. Nobody has measured a draw yet. Ettus does not publish a
typical draw for the B200 or B210, so the SDR rows are upper limits,
not typical values. When you measure an item, write the measured draw
in its row and change the basis to "measured, date, mode".

The four SDRs get power from the NUC USB 3.0 ports (bus power). Thus
the SDR power is part of the NUC input power. Do not add it a second
time at the NUC input.

| Item | Qty | Each (W) | Total (W) | Basis | Source |
| --- | --- | --- | --- | --- | --- |
| Ettus USRP B210, USB bus power | 2 | ≤ 4.5 | ≤ 9.0 | Upper limit: one USB 3.0 port (900 mA at 5 V). UHD: bus power is sufficient "in most cases" | UHD manual, B200/B210 page |
| Ettus USRP B200, USB bus power | 2 | ≤ 4.5 | ≤ 9.0 | Same as B210 | UHD manual, B200/B210 page |
| NUC7i3DNB CPU (Core i3-7100U) | 1 | 15 | 15 | CPU TDP only. This is not the board draw | Intel ARK, i3-7100U |
| Clock board rev D: SiT5155 TCXO | 1 | 0.22 | 0.22 | 44 mA typ (53 mA max), taken as 5 V input current through the LDO | `Hardware/datasheets/SiT5155-datasheet.pdf` |
| Patch antenna LNA (ANT-03) | 1 | 0.12 | 0.12 | 37 mA max at the 3.3 V minimum supply | `RF_FRONT_END.md`, Abracon datasheet |
| Flight antenna LNA (ANT-04) | 1 | 0.11 | 0.11 | 35 mA at 3 V | `ANTENNAS.md` section 5 |
| **Sum of the values above** | | | **about 33.5** | Mixed: upper limits and a CPU rating | |
| NUC board: RAM, NVMe, chipset, fan, USB controller | 1 | **TBD** | — | Not in a datasheet. Measure the NUC input with the SDRs streaming | — |
| SATA data SSD | 1 | **TBD** | — | Model not recorded | — |
| Clock board rev D: buffer, PPS buffer, LEDs, 10 MHz into 4 × 50 Ω | 1 | **TBD** | — | Upper limit 2.5 W (500 mA polyfuse F1 at 5 V) | `Hardware/clock_board_revD/design.py` |
| PPS GNSS receiver | 1 | **TBD** | — | Model TBD (section 2) | — |
| Power converter losses | — | **TBD** | — | Power design TBD (section 1) | — |

How to read the sum:
- About 33.5 W is a planning value. It is not a measured draw.
- The SDR rows are upper limits. The real SDR draw can be lower.
- The CPU row is the TDP only. The full NUC board draws more than this
  under load, and much less when the CPU is idle.
- The TBD rows and the converter losses are not included.
- The antenna supply voltage is TBD. At a higher bias voltage, the
  antenna LNA power increases.

When the SDRs use the external DC jack in place of USB power, the jack
rating is 6 V, 3 A (18 W). This is the rating of the supply, not the
SDR draw.

## 7. Ground segment (base station)

| Item | Value | Notes |
| --- | --- | --- |
| Ground station | Laptop running `Base_Software` (D-005) | Live health display + start/stop control; any OS with Python 3.11+ |
| GNSS receiver | u-blox ZED-F9P (simpleRTK2B) on a serial port, assumed (D-021, Q-002) | Base logs raw UBX/NMEA/RTCM; correction path Q-010 |
| Ground radio | The flight-controller ground link (Herelink hotspot or telemetry radio) | MAVLink stream shared with Mission Planner (Q-014) |

## 8. Change log

| Date | Change | By |
| --- | --- | --- |
| 2026-09-16 | Initial sheet from legacy SURGE data and team answers | Claude + Nathan |
| 2026-09-16 | Draft 2: NUC7i3DNB, +2 B100s, sc8, SATA data SSD, ground station laptop, payload/flight-control separation, shared oscillator | Claude + Nathan |
| 2026-09-17 | Draft 3: B100 → B200mini (USB 3.0, UHD 4.x, one reference input); GPSDO 10 MHz + PPS; payload link on the flight-controller link; ZED-F9P assumed on the ground | Claude + Nathan |
| 2026-09-24 | Draft 4: B200mini → B200 (separate REF IN and PPS IN); PPS to all four units (D-024) | Claude + Nathan |
| 2026-10-26 | Section 5: payload mass budget, an estimate from datasheet values (D-046) | Claude + Nathan |
| 2026-10-26 | Section 6: payload power budget, an estimate from datasheet values (D-047) | Claude + Nathan |
