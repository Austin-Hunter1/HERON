# HARDWARE — HERON Hardware Sheet

Status: Draft 2, 2026-09-16. Update this sheet when hardware changes.
The software must never depend on values here that are not also in a
config file.

## 1. Payload computer

| Item | Value | Notes |
| --- | --- | --- |
| Model | Intel NUC7i3DNB (board) | Core i3-7100U, 2 cores / 4 threads (D-013) |
| RAM | 8 GB | |
| OS drive | NVMe (M.2) | Ubuntu Server LTS, headless |
| Data drive | SATA SSD | IQ data only. SATA III ceiling ≈ 550 MB/s — size the signal plan below this with 2× margin |
| USB | USB 3.0 ports for the B210s; USB 2.0 acceptable for the B100s | Spread devices across controllers; check `lsusb -t` |
| Power | From drone power system, **TBD** — measure NUC + 4 SDR draw on the bench | |

The computer can change at any time (Q-005). A replacement must
support: two USB 3.0 buses for the B210s, two more USB ports for the
B100s, NVMe + SATA (or equal), and UHD on its OS.

### Data-rate sizing rule

Aggregate disk rate = sum over channels of (sample rate × 2 bytes at
sc8). Example: 2× B210 at 22 Msps × 2 ch = 176 MB/s, plus 2× B100 at
8 Msps × 1 ch = 32 MB/s, total ≈ 208 MB/s. This fits SATA III but
verify with the endurance test (`TESTING.md`). The exact plan is
Q-001 / Q-012.

## 2. Software-defined radios (D-008)

| Item | Value | Notes |
| --- | --- | --- |
| Units | 2× Ettus USRP B210, 2× Ettus USRP B100 | Serial numbers: **TBD — record all four here and in config** |
| B210 interface | USB 3.0, 2 RX channels each | |
| B100 interface | **USB 2.0 only** — practical limit ≈ 16 Msps complex at sc8 per unit (Q-012) | Daughterboard model: **TBD** |
| UHD support | **Caution (Q-011):** modern UHD 4.x dropped B100 support. Verify a UHD version that runs all four units, likely UHD 3.15 LTS. | Blocks OS/UHD install choice |
| Sample format | sc8 (8-bit complex) (D-009) | |
| Sync | Shared common oscillator to all four units (D-014). Time alignment method open (Q-004). | Oscillator source device: **TBD** |
| Legacy settings | 22 Msps, sc8, L5 1176.45 MHz, gain 45, BW 20.322 MHz | `SURGE/NUC_scripts/SDR_backup_files/b210_split_settings-balloon.xml` |

## 3. Antennas and RF chain

| Item | Value | Notes |
| --- | --- | --- |
| Direct (up-looking) antenna(s) | **TBD** | RHCP typical for direct |
| Reflected (down-looking) antenna(s) | **TBD** | LHCP typical for reflection |
| LNAs / filters / splitters | **TBD** | Record gains and bias-tee use |
| Antenna-to-SDR-channel map | **TBD** | Must live in config and on physical labels |
| Signal plan | **Open (Q-001)**: bands per SDR | Keep in config only |

## 4. Flight segment

| Item | Value | Notes |
| --- | --- | --- |
| Autopilot | Cube Orange, ArduPilot (D-004) | **Not connected to the payload** (D-012) |
| Payload control link | **TBD (Q-006)**: dedicated radio, or piggyback on flight telemetry | Carries commands, health data, maybe RTK (Q-010) |
| Drone airframe | **TBD** (drone team) | |

## 5. Ground segment (base station)

| Item | Value | Notes |
| --- | --- | --- |
| Ground station | Laptop running `Base_Software` (D-005) | Live health display + start/stop control |
| GNSS receiver | Connected to the laptop, provides RTK correction data (D-015) | Model TBD (Q-002); correction path TBD (Q-010) |
| Ground radio | Matches the payload link choice (Q-006) | |

## 6. Change log

| Date | Change | By |
| --- | --- | --- |
| 2026-09-16 | Initial sheet from legacy SURGE data and team answers | Claude + Nathan |
| 2026-09-16 | Draft 2: NUC7i3DNB, +2 B100s, sc8, SATA data SSD, ground station laptop, payload/flight-control separation, shared oscillator | Claude + Nathan |
