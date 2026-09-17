# DECISIONS — Decision Log and Open Questions

This document records confirmed decisions (D-xxx) and open questions
(Q-xxx). Claude Code and all engineers: when you need an answer that is
not here, ask the team. Then record the answer here in the same change.

## Confirmed decisions

| ID | Date | Decision | Notes |
| --- | --- | --- | --- |
| D-001 | 2026-09-16 | The onboard software records raw IQ only. No in-flight science processing. | Same concept as legacy SURGE. |
| D-002 | 2026-09-16 | All SDRs record with synchronized references. | See D-009 for the method. |
| D-003 | 2026-09-16 | ~~Recording trigger: AUTO flight mode via MAVLink.~~ **Superseded by D-010.** | |
| D-004 | 2026-09-16 | The drone flight controller is a Cube Orange with ArduPilot. | The payload no longer connects to it (D-012). |
| D-005 | 2026-09-16 | **Revised:** `Base_Software/` is the ground station laptop software: live health display, record start/stop control, and RTK GNSS data handling. `Onboard_Software/` is the NUC flight code. | Was "GNSS base station" only. |
| D-006 | 2026-09-16 | Language: Python (uv-managed) for orchestration; keep the C++ UHD recorder for the capture path. | See `RECOMMENDATIONS.md`. |
| D-007 | 2026-09-16 | Docs live in `docs/`; `CLAUDE.md` at the repo root. | |
| D-008 | 2026-09-16 | SDR inventory: 2× Ettus B210 + 2× Ettus B100. The software records all four. | See B100 cautions in `HARDWARE.md`. |
| D-009 | 2026-09-16 | Recording sample format: 8-bit complex (sc8). | |
| D-010 | 2026-09-16 | Recording control: ground station commands are primary. The payload also has an autonomous fallback so a flight without the link still collects data. Fallback details are Q-009. | Replaces D-003. |
| D-011 | 2026-09-16 | If the ground link drops during recording, the payload keeps recording until disk limits or a stop command. | |
| D-012 | 2026-09-16 | The payload (NUC + SDRs) is separated from the flight control schema (Cube Orange). No MAVLink trigger link between NUC and Cube. | See Q-008 for a possible shared radio. |
| D-013 | 2026-09-16 | Payload computer: Intel NUC7i3DNB. OS on NVMe; IQ data on a SATA SSD. | |
| D-014 | 2026-09-16 | The SDRs share a common oscillator (frequency reference). | Time alignment method is Q-004. |
| D-015 | 2026-09-16 | A GNSS receiver connects to the ground station laptop and provides RTK correction data. | Receiver model and correction path are Q-002 / Q-010. |
| D-016 | 2026-09-17 | **Payload link (closes Q-006):** all ground-to-payload and payload-to-ground traffic rides on the flight-controller telemetry link. The NUC connects to a Cube telemetry serial port as MAVLink component 191 (onboard computer). HERON frames travel inside MAVLink `TUNNEL` messages; the Cube only routes them. The base laptop reads the ground side of the same MAVLink stream. | Serial, UDP, and loopback transports exist for the bench. The Cube port and the ground port are Q-014. This re-couples payload and flight control for transport only; the payload still does not use flight modes (D-012). |
| D-017 | 2026-09-17 | **Fallback (closes Q-009):** grace period. After boot the payload waits `control.link_grace_s` (default 120 s) for a valid ground frame. When none arrives it starts recording. A ground STOP disarms the fallback. Modes `record_at_boot` and `none` also exist in config. | `rearm_fallback_on_link_loss` (default off) restarts the rule when a link is lost while IDLE. |
| D-018 | 2026-09-17 | **Capture architecture:** one C++ recorder process (`heron_recorder`, new code against the UHD 4.x API) per SDR, launched by the Python supervisor with all settings on the command line. | The legacy `rx_multi_to_file` needs headers that are not in the repository, so it was rewritten, not adapted. One process per SDR isolates faults (O15). |
| D-019 | 2026-09-17 | **Protocol and commands:** newline-delimited JSON frames with a sequence number and CRC-16; telemetry at 1 Hz. Commands: `start`, `stop`, `status`, `ping`. No shutdown over the link. | Defined in `Common_Software/src/heron_common/protocol/`. |
| D-020 | 2026-09-17 | **SDRs and sync (revises D-008; closes Q-004, Q-011, Q-012):** units 3 and 4 are B200mini (maybe B206mini), USB 3.0, one RX channel, supported by UHD 4.x with the B210. A GPSDO gives 10 MHz and PPS. The B210s take both (`clock_source = external`, `time_source = external`). The B200mini has one reference input, so it takes 10 MHz only (`time_source = none`). Clock and time sources are per SDR in config. | All four share one frequency reference, so the B200mini sample offset is constant and is found after the flight (Q-013). |
| D-021 | 2026-09-17 | **Ground GNSS, display, config, layout (Q-002 partial, Q-010 partial):** assume a u-blox ZED-F9P on a serial port; the base logs the raw stream and shows the NMEA fix; RTCM3 goes to a pluggable correction sink (`none` or `serial`). Display: terminal UI (Textual) plus headless CLI. Config: TOML validated with pydantic. Data layout: `<flight_id>/<sdr_id>/<channel_id>/<utc_start>_<seq>.sc8` with one `metadata.json` per flight. | The receiver model is still to confirm (Q-002). |

## Open questions

| ID | Question | Default until decided | Blocks |
| --- | --- | --- | --- |
| Q-001 | Signal plan: which GNSS bands on which SDR channels (L1, L5, both)? | Legacy L5 config as placeholder in config files | Capture config, antenna order |
| Q-002 | Ground station GNSS receiver model? | u-blox ZED-F9P (simpleRTK2B, as SURGE) — D-021 | Base_Software GNSS config |
| Q-003 | Data offload method from the SATA SSD after flight? | Remove or SSH copy — TBD | DEPLOYMENT, USAGE |
| Q-004 | ~~Time alignment across the four SDRs.~~ **Closed by D-020.** See Q-013 for the B200mini offset. | — | — |
| Q-005 | Final payload computer (keep NUC7i3DNB, or smaller/cheaper later)? | NUC7i3DNB | HARDWARE |
| Q-006 | ~~Ground-to-payload radio link hardware.~~ **Closed by D-016** (flight-controller link, MAVLink TUNNEL). | — | — |
| Q-007 | Containerize ground tools with Podman (onboard stays bare metal)? | Podman for ground tools/CI | DEPLOYMENT |
| Q-008 | (Merged into Q-006.) | — | — |
| Q-009 | ~~Autonomous fallback trigger.~~ **Closed by D-017** (grace period). | — | — |
| Q-010 | RTK correction data path and consumer: does the correction stream go to the Cube GNSS (via flight telemetry) or to a payload receiver via the payload link? | Base logs RTCM3; sink `none` (D-021) | Base_Software `gnss.correction`, HARDWARE |
| Q-011 | ~~UHD version for B100.~~ **Closed by D-020:** UHD 4.x for B210 and B200mini. | — | — |
| Q-012 | ~~B100 USB 2.0 sample rate limit.~~ **Closed by D-020:** the B200mini is USB 3.0. | — | — |
| Q-013 | B200mini time offset: the B200minis have no PPS (D-020), so their sample start is known to host-clock accuracy (milliseconds) only. Which method finds the exact offset to the B210 streams after the flight: cross-correlation of a direct-signal channel, or a hardware event injected into all units? | Post-flight cross-correlation; the shared 10 MHz keeps the offset constant | Post-processing, DATA_FORMATS |
| Q-014 | MAVLink link details: which Cube serial port and baud for the NUC (`SERIALn_PROTOCOL = 2`)? Which ground-side port carries MAVLink to the laptop (Herelink hotspot UDP port, or Mission Planner MAVLink mirror), so `heron-base` and Mission Planner can share the stream? Confirm ArduPilot forwards `TUNNEL` (id 385) between the NUC port and the ground link. | NUC: TELEM2 at 921600. Ground: `udpin:0.0.0.0:14551` | DEPLOYMENT, both link configs |

## How to add an entry

1. Ask the team. Get a clear answer.
2. Move the question from the open table to the confirmed table with a
   new D-number and the date.
3. Update the documents the question blocked.
