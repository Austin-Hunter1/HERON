# REQUIREMENTS — HERON Deploy Software

Status: Draft 3, 2026-09-17. Owner: HERON team. This document is the
source of truth for what the software must do. When a requirement here
conflicts with code, the requirement wins until the team changes it in
`DECISIONS.md`.

Words: **shall** = mandatory. **should** = strong preference.
**may** = optional.

## 1. Mission requirements

- M1. The system shall record raw GNSS IQ samples in flight from
  direct (up-looking) and reflected (down-looking) antennas.
- M2. The system shall record enough data quality to detect surface
  water in ground post-processing.
- M3. The system shall provide RTK-grade positioning data: a ground
  GNSS receiver at the base station supplies correction data
  (D-015; correction path is Q-010).

## 2. Onboard software (Onboard_Software/)

### Capture

- O1. The onboard software shall record raw IQ samples from all four
  SDRs (2× B210, 2× B200mini — D-008, D-020) to the SATA data SSD. It
  shall not process science data in flight. (D-001.)
- O2. All SDRs shall record from the shared 10 MHz reference (D-014).
  The software shall verify reference lock where the hardware reports
  it, and shall align the two B210s to a shared PPS edge; the
  B200mini's offset is found after the flight (D-020, Q-013).
- O3. Sample file format shall be sc8 (8-bit complex) (D-009). Center
  frequencies, sample rates, gains, bandwidths, antenna ports, and
  channel counts shall come from a config file. The signal plan is
  open (Q-001); the code shall not fix it.
- O4. The capture path shall sustain the configured aggregate data
  rate without sample loss. The software shall detect, count, and log
  overflows and dropped samples per SDR.
- O5. Each recording shall include metadata: config used, start time,
  SDR serial numbers, and software version (git hash). It shall also
  include a `metadata.yml` in the data-processing team's own schema,
  so their tools read a HERON flight with no conversion (D-022).

### Control and monitoring

- O6. The payload shall not depend on the flight controller's state:
  it shall not read flight mode or arm state, and no behavior of the
  onboard software shall change because of them (D-012). The payload
  link transport rides on the Cube's telemetry link as a bit-pipe
  (MAVLink `TUNNEL` frames the Cube only routes, never interprets) —
  this is a transport choice, not a flight-controller dependency
  (D-016).
- O7. The onboard software shall accept start and stop commands from
  the ground station over the payload link (D-010). The command
  protocol shall be its own module, independent of the transport
  (D-016), so the transport can change without code rewrites.
- O8. The onboard software shall have an autonomous fallback so a
  flight without a working ground link still collects data (D-010).
  Fallback behavior shall be configurable: a grace period after boot
  is the default (D-017).
- O9. If the ground link drops during recording, the payload shall
  keep recording until disk limits or a stop command (D-011).
- O10. The onboard software shall stream live health telemetry to the
  ground station: recording state, per-SDR status and reference lock,
  overflow counts, data rate, disk free space, CPU load, and
  temperatures. The telemetry set and rate shall be configurable and
  shall fit the link bandwidth (D-016, D-019).
- O11. The onboard software shall monitor free disk space and shall
  stop recording, with a logged event and a telemetry alert, before
  the disk is full. The threshold shall be configurable.
- O12. The onboard software shall start automatically at boot and
  shall recover automatically after a crash (systemd restart).
- O13. All events shall go to a persistent, timestamped log file on
  the payload, independent of the ground link. Verbosity shall be
  configurable.

### Robustness

- O14. Loss of the ground link shall not crash the software. The
  software shall keep operating, retry the link, and log the events.
- O15. Disconnection of one SDR shall produce a clear fault state,
  a telemetry alert, and a log entry — not a silent hang. The other
  SDRs shall keep recording if possible.
- O16. Power loss during recording shall corrupt at most the file
  segment being written. Recordings shall be split into fixed-length
  segments (configurable; legacy used 30 s).

## 3. Ground station software (Base_Software/) (D-005)

- B1. The base software shall run on the ground station laptop and
  shall display the full live health data from O10 during flight.
- B2. The base software shall provide start and stop recording
  controls that send commands over the payload link.
- B3. The base software shall show link state clearly (connected,
  degraded, lost) and the age of the last telemetry.
- B4. The base software shall read the connected GNSS receiver
  (Q-002) and shall handle the RTK correction data per the path the
  team selects (Q-010). It shall log the raw GNSS data for
  post-processing backup.
- B5. The base software shall log all telemetry and all commands with
  timestamps, so a flight can be reconstructed.
- B6. The display technology (terminal UI, web page, or GUI) is a
  team choice; the display shall be a thin layer over a documented
  telemetry interface, so it can change independently.

## 4. Software quality requirements

- S1. All tunable values shall live in config files (no hard-coded
  values). Configs shall be validated at load with clear errors.
- S2. The code shall be modular: capture control, command/telemetry
  link, disk monitoring, logging, config loading, and display shall
  be separate modules with narrow interfaces.
- S3. All code shall be commented for human readability in ASD-STE100
  Simplified Technical English.
- S4. New logic shall have unit tests. The regression suite shall
  pass before merge. See `TESTING.md`.
- S5. The software shall run on the NUC7i3DNB (D-013) and shall not
  depend on that exact hardware (Q-005). See `HARDWARE.md`.
- S6. The software shall be operable and debuggable in the field over
  SSH without a monitor. See `FIELD_DEBUG_SOP.md`.

## 5. Out of scope (for now)

- In-flight science processing.
- Real-time downlink of IQ samples.
- Flight control and flight planning (the drone team owns the Cube).

## 6. Open requirements

See `DECISIONS.md` for the open-question list: the signal plan
(Q-001), the ground GNSS receiver model (Q-002), data offload (Q-003),
the final payload computer (Q-005), the RTK correction path (Q-010),
the B200mini post-flight time offset (Q-013), and the exact MAVLink
port and baud on both ends (Q-014).
