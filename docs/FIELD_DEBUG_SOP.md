# FIELD_DEBUG_SOP — Debug Problems in the Field

Status: Draft 1, 2026-09-16. The payload is headless in the field: no
monitor, no keyboard. All access is over SSH from the field laptop.
Goal: decide fast whether you can fly, fix, or stand down. Do not
start bench-style deep debugging in the field.

## 1. Connect to the payload

1. Power the payload. Wait 2 minutes for boot.
2. Join the payload network for SSH (method TBD; record the SSID and
   address here when decided). Note: SSH access and the payload
   control link (Q-006) can be different paths.
3. SSH in: `ssh heron@heron-nuc.local` (or the fixed IP).
4. If SSH fails after 5 minutes: power cycle once. If it fails again,
   see Section 5.

## 2. Standard health check (run before every flight)

From the laptop: `uv run heron-base send status --config
Base_Software/config/base.toml` prints the ack and refreshes the
display. Over SSH on the payload, check by hand:

1. Service active: `systemctl status heron-onboard`.
2. Disk mounted and free space OK: `df -h /media/DataStore`.
3. All four SDRs present: `uhd_find_devices` (only while IDLE, never
   while recording).
4. Ground link up: the `Base_Software` display shows CONNECTED and
   fresh telemetry.

## 3. Recording did not start on command

1. Check the `Base_Software` display: is the link CONNECTED and the
   telemetry fresh? If not, this is a link problem — check the radio
   hardware and antennas on both ends first.
2. In the payload log (`journalctl -u heron-onboard -e`), confirm the
   START command arrived.
3. If the command arrived but the recorder faulted: read the last log
   lines. A disk-space stop or SDR fault is shown there.
4. One retry rule: fix the one identified cause, then repeat the
   health check. If it fails again, stand down and take the payload
   back to the bench.

## 4. In-flight fault messages

1. FAULT status on the ground station display: continue the flight
   only if the team lead agrees; the payload data for this flight
   may be lost.
1a. Link LOST during recording: the payload keeps recording (D-011).
   Note the time; try to recover the link; do not power cycle the
   payload in flight.
2. After landing, do not power off. SSH in and copy the last 200 log
   lines to the field laptop before any restart.

## 5. Cannot reach the payload at all

1. Check payload power LEDs and connectors.
2. Check the field network is up (can the laptop see the router/AP?).
3. Power cycle the network device, then the payload, one time each.
4. If still unreachable: stand down the payload. Do not open the case
   in the field unless the lead approves.

## 6. Field kit for debugging

SSH-ready ground laptop with the repository cloned; payload radio
link spares; GNSS receiver cables; spare USB 3.0 and USB 2.0 cables;
spare SSD (if removable, Q-003); printed copy of this SOP and
`USAGE.md`.

## 7. Report

After any field problem, file an issue the same day: symptoms, log
excerpts, actions taken, and whether data was collected.
