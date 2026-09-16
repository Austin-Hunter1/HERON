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

## Open questions

| ID | Question | Default until decided | Blocks |
| --- | --- | --- | --- |
| Q-001 | Signal plan: which GNSS bands on which SDR channels (L1, L5, both)? | Legacy L5 config as placeholder in config files | Capture config, antenna order |
| Q-002 | Ground station GNSS receiver model? | — | Base_Software design |
| Q-003 | Data offload method from the SATA SSD after flight? | Remove or SSH copy — TBD | DEPLOYMENT, USAGE |
| Q-004 | Time alignment across the four SDRs: shared PPS, common start marker, or post-hoc correlation? A shared oscillator fixes frequency, not sample-start time. | — | Capture code, DATA_FORMATS |
| Q-005 | Final payload computer (keep NUC7i3DNB, or smaller/cheaper later)? | NUC7i3DNB | HARDWARE |
| Q-006 | Ground-to-payload radio link hardware for control/telemetry? Dedicated payload radio, or piggyback on the existing flight-control telemetry link (preferred by the team if bandwidth and the drone team allow — note this partly re-couples payload and flight control, against D-012). | — | HARDWARE, Base_Software, Onboard_Software |
| Q-007 | Containerize ground tools with Podman (onboard stays bare metal)? | Podman for ground tools/CI | DEPLOYMENT |
| Q-008 | (Merged into Q-006.) | — | — |
| Q-009 | Autonomous fallback trigger when the ground link is absent: record from power-on, timer, or arm-signal? | Record from power-on | Onboard_Software |
| Q-010 | RTK correction data path and consumer: does the correction stream go to the Cube GNSS (via flight telemetry) or to a payload receiver via the payload link? | — | Base_Software, HARDWARE |
| Q-011 | UHD version: verify that one UHD version supports both B210 and B100 on the chosen OS. B100 support was removed from modern UHD 4.x; UHD 3.15 LTS may be required. | Verify on the bench before design freeze | DEPLOYMENT, capture code |
| Q-012 | B100 sample rate plan: USB 2.0 limits each B100 to roughly 16 Msps complex at sc8. Confirm the required rates fit. | — | Signal plan (Q-001), HARDWARE |

## How to add an entry

1. Ask the team. Get a clear answer.
2. Move the question from the open table to the confirmed table with a
   new D-number and the date.
3. Update the documents the question blocked.
