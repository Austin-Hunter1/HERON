# TEST_BENCH_ROUTINE — Standard Bench Test Sequence

Status: Draft 2, 2026-09-17. Run the full routine (Sections 1-5) after
any software, config, OS, or hardware change. Run the short check
(Section 6) before each field day. Record results in the bench log.

## 1. Power and boot

1. Connect the payload to bench power. Note the supply voltage and
   current at idle.
2. Power on. Confirm the computer boots and you can SSH in within the
   expected time.
3. Confirm the data disk mounted at the configured path.
4. Confirm the onboard supervisor service started: `systemctl status`.

## 2. SDR enumeration and sync

1. Run `uhd_find_devices`. Confirm all four SDR serial numbers
   (2× B210, 2× B200mini) appear and match `HARDWARE.md` and the
   config.
2. Run `uhd_usrp_probe` on each unit. Confirm no USB errors. Confirm
   the four units sit on at least two different USB 3.0 controllers
   (`lsusb -t`).
3. Confirm each unit locks to the shared 10 MHz (the recorder refuses
   to start without `ref_locked`; the display shows `ref` per SDR).
   Confirm the two B210s see PPS (the recorder reports "no PPS edge
   seen" otherwise). If sync hardware is not yet fitted, set
   `clock_source = "internal"` and `time_source = "none"` in a bench
   config and record that this step is waived.

## 3. Capture test

1. Start a manual 60-second recording with the flight config:
   `uv run heron-onboard record --config Onboard_Software/config/onboard.toml --seconds 60`
   (stop the service first: `sudo systemctl stop heron-onboard`).
2. Watch the console/log for overflow indications. Zero overflows is
   the pass condition.
3. Confirm files appear in the data path with the expected segment
   interval and sizes (rate × time).
4. Confirm the metadata file records config, serials, and start time.
5. With a GNSS antenna or simulator connected, process a sample chunk
   and confirm satellite signals are visible (acquisition peak). Use
   the quick-look tool (TBD when the code exists; legacy reference:
   `SURGE/Data_Processing/sample_L5Aquisition.py`).

## 4. Ground link and control test

1. Start `uv run heron-base tui` on the bench laptop with the
   flight-controller link connected (Cube + ground radio, D-016), or
   with the `udp` transport over the bench network when the Cube is
   not available (`base.bench-udp.toml` / `onboard.bench-loopback.toml`).
2. Confirm the display shows link CONNECTED and live health data:
   per-SDR state, reference lock, disk space, data rate.
3. Send START from the ground station. Confirm recording starts and
   the state and data rate update on the display.
4. Send STOP. Confirm recording stops cleanly.
5. Break the link during a test recording. Confirm the payload keeps
   recording (D-011), the display shows link LOST with telemetry age,
   and the link recovers when restored.
6. Autonomous fallback test: boot the payload with no ground link.
   Confirm the configured fallback behavior happens after the grace
   period (D-017).

## 5. Fault and endurance tests

1. Fill-disk test: set the free-space threshold high in a test config.
   Confirm recording stops before the threshold and logs the event.
2. SDR-pull test: unplug one B210 during a test recording. Confirm a
   clear fault state, not a hang. Replug and confirm recovery path.
3. Endurance: record for the full planned flight duration plus 50%.
   Confirm zero overflows and stable temperatures.
4. Power-cycle test: hard power cycle in IDLE. Confirm clean reboot to
   ready state with no operator input.

## 6. Short pre-field check (15 minutes)

1. Boot, SSH in, service active, disk mounted, free space OK.
2. All four SDRs enumerate; reference locked.
3. 60-second capture, zero overflows, files and metadata present.
4. Ground link check: `Base_Software` connects, shows live health
   data, and a START/STOP cycle works.

## 7. Pass criteria and records

The routine passes when every step passes or has a recorded waiver.
Record: date, operator, git hash, config hash, results, anomalies.
Store bench logs in the repository or the team drive (location TBD).
