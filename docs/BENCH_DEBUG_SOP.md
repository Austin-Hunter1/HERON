# BENCH_DEBUG_SOP — Debug Problems on the Bench

Status: Draft 2, 2026-09-17. Use this procedure when a bench test
fails. Work from the wall to the software: power, USB, driver,
config, code. Change one thing at a time. Log every change.

## 1. Computer does not boot or is unreachable

1. Confirm bench power voltage and polarity at the payload connector.
2. Connect a monitor and keyboard (bench only). Watch the boot.
3. If the OS boots but SSH fails: check the network link, then
   `systemctl status ssh` on the console.

## 2. An SDR does not enumerate

1. Run `lsusb`. A B210 appears as Ettus Research. If absent, swap the
   USB cable, then the USB port. Use short, known-good USB 3.0 cables.
2. If present in `lsusb` but not `uhd_find_devices`: re-run
   `sudo uhd_images_downloader`; check udev rules; try as root once to
   isolate a permissions problem.
3. Check `dmesg -w` while replugging for USB resets or power faults.
4. Test the unit alone on a lab PC to separate unit fault from NUC
   fault.

## 3. Overflows (dropped samples) during capture

1. Confirm the SSD write speed with `fio` at the target rate.
2. Confirm each B210 is on its own USB controller
   (`lsusb -t`).
3. Lower the sample rate in a test config to find the stable ceiling.
4. Check CPU load and thermal throttling during capture
   (`htop`, `sensors`).
5. Check the kernel USB memory setting from `DEPLOYMENT.md`.

## 4. No reference or PPS lock

1. Check cables from the sync source (GPSDO) to each unit's REF input,
   and to both B210 PPS inputs. The B200mini has one reference input
   only (10 MHz REF); it has no PPS to check (D-020).
2. Confirm the config selects `external` clock and time sources per
   SDR (`sdr.clock_source`, `sdr.time_source`). The recorder refuses
   to start without a reference lock, and logs "no PPS edge seen" if a
   B210's PPS cable is missing or the GPSDO is not outputting PPS.
3. Probe the reference with a scope if available (bench only).

## 5. Ground link problems

1. Test the command/telemetry protocol over `udp` or `loopback`
   transport first (`onboard.bench-loopback.toml` /
   `base.bench-udp.toml`), to separate protocol bugs from MAVLink or
   radio bugs.
2. Then test over the real flight-controller link (D-016) at short
   range: `heron-base send ping` and confirm an ack. No ack after a
   few tries means the Cube is not forwarding `TUNNEL` frames — check
   the NUC's heartbeat is reaching the Cube (`SERIALn_PROTOCOL = 2`)
   and that the ground side is reading the same MAVLink stream
   (Q-014).
3. Confirm the link config (device path, addresses, rates) matches on
   both the payload and the ground laptop. Prefer stable
   `/dev/serial/by-id/` paths for serial devices.
4. Check antennas and cables before you suspect our code.

## 6. Software faults

1. Read the supervisor log and the journal first:
   `journalctl -u heron-onboard -e`.
2. Reproduce with `uv run` in the foreground with debug logging on.
3. Write a failing unit or regression test that captures the bug
   before you fix it. See `TESTING.md`.

## 7. When you are stuck

1. Record what you observed, what you changed, and the logs.
2. Post in the team channel with the log excerpt.
3. Do not leave the payload in a modified state without a note in the
   bench log.
