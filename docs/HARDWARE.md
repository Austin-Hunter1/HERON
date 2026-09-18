# HARDWARE — HERON Hardware Sheet

Status: Draft 3, 2026-09-17. Update this sheet when hardware changes.
The software must never depend on values here that are not also in a
config file.

## 1. Payload computer

| Item | Value | Notes |
| --- | --- | --- |
| Model | Intel NUC7i3DNB (board) | Core i3-7100U, 2 cores / 4 threads (D-013) |
| RAM | 8 GB | |
| OS drive | NVMe (M.2) | Ubuntu Server LTS, headless |
| Data drive | SATA SSD | IQ data only. SATA III ceiling ≈ 550 MB/s — size the signal plan below this with 2× margin |
| USB | USB 3.0 ports for all four SDRs (B210 and B200mini are both USB 3.0) | Spread devices across controllers; check `lsusb -t` |
| Power | From drone power system, **TBD** — measure NUC + 4 SDR draw on the bench | |

The computer can change at any time (Q-005). A replacement must
support: four USB 3.0 ports on at least two controllers, NVMe + SATA
(or equal), and UHD 4.x on its OS.

### Data-rate sizing rule

Aggregate disk rate = sum over channels of (sample rate × 2 bytes at
sc8). Example: 2× B210 at 22 Msps × 2 ch = 176 MB/s, plus 2× B200mini
at 22 Msps × 1 ch = 88 MB/s, total ≈ 264 MB/s. This fits SATA III but
verify with the endurance test (`TESTING.md`). The exact plan is
Q-001. `heron-onboard check-config` prints this sum for a config.

## 2. Software-defined radios (D-008, D-020)

| Item | Value | Notes |
| --- | --- | --- |
| Units | 2× Ettus USRP B210, 2× Ettus USRP B200mini (B206mini possible) | Serial numbers: **TBD — record all four here and in `onboard.toml`** |
| B210 interface | USB 3.0, 2 RX channels each, separate REF IN and PPS IN | `clock_source = external`, `time_source = external` |
| B200mini interface | USB 3.0, 1 RX channel, **one reference input** (10 MHz REF only, no separate PPS) | `clock_source = external`, `time_source = none` (D-020) |
| UHD support | UHD 4.x (apt on Ubuntu 22.04/24.04) runs all four | Q-011 closed |
| Sample format | sc8 (8-bit complex) files (D-009); sc16 on the USB wire by default | `capture.cpu_format`, `capture.wire_format` |
| Sync | GPSDO: 10 MHz to all four units; PPS to the two B210s (D-014, D-020) | GPSDO model: **TBD**. Splitter/cabling for 10 MHz to four units: **TBD** |
| Time alignment | B210s: PPS-aligned to the same UTC second. B200minis: host-clock start (ms), exact offset found after the flight (Q-013) | See `DATA_FORMATS.md` |
| Legacy settings | 22 Msps, sc8, L5 1176.45 MHz, gain 45, BW 20.322 MHz | `SURGE/NUC_scripts/SDR_backup_files/b210_split_settings-balloon.xml` |

## 3. Antennas and RF chain

| Item | Value | Notes |
| --- | --- | --- |
| Direct (up-looking) antenna(s) | **TBD** | RHCP typical for direct |
| Reflected (down-looking) antenna(s) | **TBD** | LHCP typical for reflection; the 1.02 deck plans L and R HCP |
| LNAs / filters / splitters | **TBD** | Record gains and bias-tee use |
| Antenna-to-SDR-channel map | **TBD** | Must live in config (`antenna_label` per channel) and on physical labels |
| Signal plan | **Open (Q-001)**: bands per SDR | Keep in config only. The template puts L5 on one B210 + one B200mini and L1 on the others as a placeholder |

## 4. Flight segment

| Item | Value | Notes |
| --- | --- | --- |
| Autopilot | Cube Orange, ArduPilot (D-004) | The payload does not use flight modes (D-012) |
| Payload control link | **Flight-controller telemetry link (D-016).** NUC ↔ Cube telemetry serial port (MAVLink 2, component 191); Cube ↔ ground via the existing telemetry radio / Herelink | Cube port and baud: Q-014. Default TELEM2 at 921600 |
| Drone airframe | Aurelia X6 (from SURGE; drone team confirms) | |

## 5. Ground segment (base station)

| Item | Value | Notes |
| --- | --- | --- |
| Ground station | Laptop running `Base_Software` (D-005) | Live health display + start/stop control; any OS with Python 3.11+ |
| GNSS receiver | u-blox ZED-F9P (simpleRTK2B) on a serial port, assumed (D-021, Q-002) | Base logs raw UBX/NMEA/RTCM; correction path Q-010 |
| Ground radio | The flight-controller ground link (Herelink hotspot or telemetry radio) | MAVLink stream shared with Mission Planner (Q-014) |

## 6. Change log

| Date | Change | By |
| --- | --- | --- |
| 2026-09-16 | Initial sheet from legacy SURGE data and team answers | Claude + Nathan |
| 2026-09-16 | Draft 2: NUC7i3DNB, +2 B100s, sc8, SATA data SSD, ground station laptop, payload/flight-control separation, shared oscillator | Claude + Nathan |
| 2026-09-17 | Draft 3: B100 → B200mini (USB 3.0, UHD 4.x, one reference input); GPSDO 10 MHz + PPS; payload link on the flight-controller link; ZED-F9P assumed on the ground | Claude + Nathan |
