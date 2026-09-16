# USAGE — Operate the System for a Flight

Status: Draft 2, 2026-09-16. This is the operator procedure for a data
collection flight. It assumes a deployed, bench-tested payload
(`DEPLOYMENT.md`, `TEST_BENCH_ROUTINE.md`).

## 1. Before you leave the lab

1. Charge all batteries (drone, payload if separate, ground laptop,
   payload radio if separate).
2. Confirm the SATA data SSD has enough free space for the planned
   flight time. Use the data-rate rule in `HARDWARE.md`.
3. Run the short bench check (Section 6 of `TEST_BENCH_ROUTINE.md`).
4. Copy the current config hash and git hash to the flight log sheet.
5. Pack: payload, antennas, cables, ground laptop, GNSS receiver and
   antenna for the laptop, radio link hardware, tripod, spare cables,
   multitool.

## 2. Field setup

1. Set up the ground station: laptop, radio link, and the GNSS
   receiver with a clear sky view. Start `Base_Software`. Confirm the
   GNSS receiver has a fix and RTK data is flowing (path per Q-010).
2. Mount the payload and antennas on the drone. Confirm each antenna
   connects to the correct SDR port (labels and `HARDWARE.md`).
3. Power the payload. Wait for boot.
4. On the ground station display, confirm: link CONNECTED, payload
   IDLE, all four SDRs present, reference lock, disk free space OK.
5. If the display shows no link after 5 minutes, go to
   `FIELD_DEBUG_SOP.md`.

## 3. Flight

1. The drone operator arms and flies per the flight plan. The payload
   does not depend on the flight controller.
2. Start recording from the ground station at the agreed point (for
   example, after takeoff, before the survey lines). Confirm the
   state changes to RECORDING and the data rate is normal.
3. During flight, watch the health display: overflows, disk space,
   SDR faults, link state.
4. If the link drops, do not panic: the payload keeps recording
   (D-011). Log the time. Recover the link if possible.
5. Stop recording from the ground station after the survey lines.
   Confirm the state returns to IDLE.

## 4. After landing

1. Confirm the state is IDLE on the display (or over SSH).
2. Shut the payload down cleanly over SSH: `sudo poweroff`. Do not
   pull power during a write.
3. Stop and save the ground station logs (telemetry, commands, GNSS).
4. Fill the flight log: date, site, times, config hash, anomalies.

## 5. Back in the lab

1. Offload the IQ data (procedure TBD, Q-003) and the ground station
   logs.
2. Verify file counts and sizes against the recording duration.
3. Back up raw data to the lab archive before any processing.
4. File any anomalies as issues in the repository.
