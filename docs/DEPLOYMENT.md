# DEPLOYMENT — Install the Onboard Software on a Payload Computer

Status: Draft 2, 2026-09-17. Follow these steps to prepare a fresh
payload computer. Steps marked TBD depend on open questions in
`DECISIONS.md`. The full dependency list is in
`HERON_DEPLOY_SOFTWARE/README.md`.

## 1. Install the operating system

1. Install Ubuntu Server LTS (24.04 recommended; see
   `RECOMMENDATIONS.md`). Choose the minimal server install. Do not
   install a desktop environment.
2. Set the hostname to `heron-nuc` and create the user `heron`.
3. Enable the OpenSSH server during install.
4. After first boot, update the system:
   `sudo apt update && sudo apt upgrade -y`
5. Disable automatic unattended upgrades of kernel and UHD packages
   before the field season, so behavior does not change mid-campaign.
6. Add `heron` to the `dialout` group so it can open the Cube serial
   port: `sudo usermod -aG dialout heron`.

## 2. Install system dependencies

1. Install UHD 4.x, the build tools, and Boost:
   `sudo apt install -y uhd-host libuhd-dev build-essential cmake git libboost-program-options-dev`
   UHD 4.x supports the B210 and the B200mini (D-020). Check with
   `uhd_config_info --version`.
2. Download the FPGA images: `sudo uhd_images_downloader`
3. Add the udev rules so the `heron` user can open the SDRs without
   root (the `uhd-host` package installs them; else copy
   `uhd-usrp.rules` to `/etc/udev/rules.d/` and run
   `sudo udevadm control --reload-rules`).
4. Increase USB memory limits if UHD reports buffer errors
   (`usbcore.usbfs_memory_mb=1024` on the kernel command line).
5. Allow real-time thread priority for the recorder: add
   `@heron - rtprio 99` to `/etc/security/limits.conf` (the systemd
   unit also sets `LimitRTPRIO`).

## 3. Install the HERON software

1. Clone the repository with submodules (see `GETTING_STARTED.md`)
   into `/home/heron/HERON`.
2. Install `uv`: `curl -LsSf https://astral.sh/uv/install.sh | sh`
3. In `HERON_DEPLOY_SOFTWARE/` run `uv sync --all-packages`. This
   installs the three Python packages and the dev tools.
4. Build and install the C++ recorder:
   ```bash
   cd HERON_DEPLOY_SOFTWARE/Onboard_Software/recorder
   cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
   cmake --build build -j
   sudo cmake --install build
   ```
   This puts `heron_recorder` in `/usr/local/bin`. See
   `recorder/README.md`.
5. Copy the config template and edit the machine values:
   `cp Onboard_Software/config/onboard.example.toml Onboard_Software/config/onboard.toml`
   Set: the four SDR serial numbers (from `uhd_find_devices`), the
   per-SDR `clock_source`/`time_source`, the Cube serial port under
   `[link.mavlink]` (use a `/dev/serial/by-id/` path), data paths,
   disk thresholds, and the `[bands]` table plus each channel's `band`
   (used to build `metadata.yml`, D-022). Then validate:
   `uv run heron-onboard check-config --config Onboard_Software/config/onboard.toml`

## 4. Prepare storage

1. Format the data SSD as ext4 with label `DataStore`.
2. Add an fstab entry to mount it at `/media/DataStore` at boot
   (`LABEL=DataStore /media/DataStore ext4 defaults,noatime 0 2`).
3. Create the data and log directories, owned by `heron`:
   `sudo mkdir -p /media/DataStore/iq /media/DataStore/logs && sudo chown -R heron:heron /media/DataStore`
4. Confirm sustained write speed with `dd` or `fio` at 2× the
   configured capture rate.
5. Offload/rotation procedure: TBD (Q-003).

## 5. Configure the flight controller link (D-016, Q-014)

1. Connect the NUC to a Cube telemetry port (default plan: TELEM2)
   with a USB-serial adapter or the Cube's serial cable.
2. In Mission Planner set for that port: `SERIALn_PROTOCOL = 2`
   (MAVLink 2) and `SERIALn_BAUD` to match `link.mavlink.baud`
   (default 921600 → `SERIALn_BAUD = 921`).
3. The NUC sends a heartbeat as system 1, component 191. ArduPilot
   learns the route and forwards `TUNNEL` messages between the NUC
   port and the ground link. Confirm this on the bench with
   `heron-base send ping` (TEST_BENCH_ROUTINE section 4).
4. Record the final port, baud, and any routing parameters here.

## 6. Enable autostart

1. Install the systemd unit:
   ```bash
   sudo cp HERON_DEPLOY_SOFTWARE/Onboard_Software/systemd/heron-onboard.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now heron-onboard
   ```
   The unit restarts on failure, starts after the data disk mounts,
   and logs to the journal and to `/media/DataStore/logs/`.
2. Reboot. Confirm the supervisor is active:
   `systemctl status heron-onboard` and
   `journalctl -u heron-onboard -e`.

## 7. Verify the installation

Run the full bench routine in `TEST_BENCH_ROUTINE.md` before any
flight. Do not skip it after a reinstall, OS update, or hardware swap.

## 8. Ground station (Base_Software)

1. Install `uv` and run `uv sync --all-packages` in
   `HERON_DEPLOY_SOFTWARE/` on the ground laptop (Linux, macOS, or
   Windows; Linux is simplest for serial ports).
2. Copy `Base_Software/config/base.example.toml` to `base.toml` and
   edit: the MAVLink ground source under `[link.mavlink]` (Herelink
   hotspot UDP port or Mission Planner mirror, Q-014), the GNSS
   receiver port under `[gnss]`, and the correction sink (Q-010).
3. Validate: `uv run heron-base check-config --config Base_Software/config/base.toml`
4. Try the display with no hardware: `uv run heron-base demo`.
5. Test the link against the bench payload
   (Section 4 of `TEST_BENCH_ROUTINE.md`).
