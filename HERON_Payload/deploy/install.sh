#!/usr/bin/env bash
# Install or update the HERON payload service on the NUC (Ubuntu Server 24.04).
# Run from the copied HERON_Payload folder:   sudo bash deploy/install.sh
# Safe to run again after copying a newer version; your saved setups in /var/lib/heron are kept.
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="$(pwd)"
USER_NAME="${HERON_USER:-${SUDO_USER:-heron}}"   # the account that ran sudo, e.g. gpsadmin

if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo bash deploy/install.sh"; exit 1; fi
id "$USER_NAME" >/dev/null 2>&1 || { echo "User $USER_NAME does not exist (set HERON_USER=...)"; exit 1; }

echo "== Packages"
apt-get update -q
apt-get install -y -q uhd-host libuhd-dev build-essential cmake libboost-all-dev python3-venv rsync
uhd_images_downloader >/dev/null || echo "   (FPGA image download failed; run 'sudo uhd_images_downloader' when online)"
usermod -aG dialout,plugdev "$USER_NAME"

echo "== Code to /opt/heron"
mkdir -p /opt/heron /etc/heron
rsync -a --delete --exclude '__pycache__' --exclude '.venv' "$SRC/heron_payload" "$SRC/recorder" "$SRC/deploy" "$SRC/requirements.txt" /opt/heron/
[[ -x /opt/heron/.venv/bin/python ]] || python3 -m venv /opt/heron/.venv
/opt/heron/.venv/bin/pip install -q -r /opt/heron/requirements.txt

echo "== Build the recorder"
BUILD="$(mktemp -d)"
cmake -S /opt/heron/recorder -B "$BUILD" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/heron >/dev/null
cmake --build "$BUILD" -j"$(nproc)"
cmake --install "$BUILD" >/dev/null
rm -rf "$BUILD"
/opt/heron/bin/heron-recorder --discover || true

echo "== Service"
install -m 0644 /opt/heron/deploy/heron-payload.service /etc/systemd/system/heron-payload.service
if [[ ! -f /etc/heron/payload.env ]]; then
  install -m 0644 /opt/heron/deploy/payload.env.example /etc/heron/payload.env
  PORT="$(ls /dev/serial/by-id/ 2>/dev/null | head -n1 || true)"
  [[ -n "$PORT" ]] && sed -i "s|/dev/serial/by-id/REPLACE_ME|/dev/serial/by-id/$PORT|" /etc/heron/payload.env
fi
if [[ "$USER_NAME" != heron ]]; then
  sed -i "s/^User=heron/User=$USER_NAME/; s/^Group=heron/Group=$USER_NAME/" /etc/systemd/system/heron-payload.service
fi
systemctl daemon-reload
systemctl enable heron-payload >/dev/null
systemctl restart heron-payload

echo
echo "Installed. Cube link: $(grep ^HERON_CUBE_PORT /etc/heron/payload.env)"
echo "If that path says REPLACE_ME, plug in the TELEM2 adapter, run 'ls /dev/serial/by-id/', put it in"
echo "/etc/heron/payload.env and run: sudo systemctl restart heron-payload"
echo "Logs:          journalctl -u heron-payload -f"
echo "Payload Setup: http://$(hostname).local:8080"
