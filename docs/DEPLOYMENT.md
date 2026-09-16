# DEPLOYMENT — Install the Onboard Software on a Payload Computer

Status: Draft 1, 2026-09-16. Follow these steps to prepare a fresh
payload computer. Steps marked TBD depend on open questions in
`DECISIONS.md`.

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

## 2. Install system dependencies

1. Install UHD and tools. **Caution (Q-011):** the apt UHD on
   modern Ubuntu is 4.x, which dropped B100 support. Verify the UHD
   version that runs all four SDRs (likely UHD 3.15 LTS, built from
   source) before you standardize this step. Also install:
   `sudo apt install -y build-essential cmake git`
2. Download the B210 FPGA images:
   `sudo uhd_images_downloader`
3. Add the udev rules so the `heron` user can open the SDRs without
   root (copy the rules from the UHD install, then
   `sudo udevadm control --reload-rules`).
4. Increase USB memory limits if UHD reports buffer errors
   (`usbcore.usbfs_memory_mb=1024` on the kernel command line).

## 3. Install the HERON software

1. Clone the repository with submodules (see `GETTING_STARTED.md`).
2. Install `uv`, then run `uv sync` in `HERON_DEPLOY_SOFTWARE/`.
3. Build the C++ recorder (adapted from the legacy
   `rx_multi_to_file`): follow the build README in
   `HERON_DEPLOY_SOFTWARE/Onboard_Software/` (TBD when the code
   exists).
4. Copy the machine config template to the machine config location
   and edit it: the four SDR serial numbers, payload link settings,
   data paths, disk thresholds, fallback trigger behavior.

## 4. Prepare storage

1. Format the data SSD as ext4 with label `DataStore`.
2. Add an fstab entry to mount it at `/media/DataStore` at boot.
3. Confirm sustained write speed with `dd` or `fio` at 2× the
   configured capture rate.
4. Offload/rotation procedure: TBD (Q-003).

## 5. Enable autostart

1. Install the provided systemd unit for the onboard supervisor
   (TBD when the code exists). The unit must:
   restart on failure, start after the data disk mounts, and log to
   the journal and to `/media/DataStore/logs/`.
2. Reboot. Confirm the supervisor is active:
   `systemctl status heron-onboard`.

## 6. Verify the installation

Run the full bench routine in `TEST_BENCH_ROUTINE.md` before any
flight. Do not skip it after a reinstall, OS update, or hardware swap.

## 7. Ground station (Base_Software)

1. Install `uv` and run `uv sync` in `HERON_DEPLOY_SOFTWARE/` on the
   ground laptop (any OS the team uses; Linux is simplest).
2. Connect the GNSS receiver (model TBD, Q-002) and the payload radio
   link hardware (TBD, Q-006).
3. Configure and test the link against the bench payload
   (Section 4 of `TEST_BENCH_ROUTINE.md`).
Record the full procedure here when the hardware is chosen.
