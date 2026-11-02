#!/usr/bin/env bash
# Update an already-installed NUC to the HERON_Payload folder this script sits in.
# No internet needed. Keeps /etc/heron/payload.env, your setups in /var/lib/heron, and all recordings.
#
#   1. Copy the new HERON_Payload folder to the NUC (WinSCP, or: scp -r HERON_Payload <user>@192.168.137.10:~)
#   2. ssh in and run:   sudo bash ~/HERON_Payload/deploy/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="$(pwd)"

if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo bash deploy/update.sh"; exit 1; fi
if [[ ! -x /opt/heron/.venv/bin/python || ! -f /etc/systemd/system/heron-payload.service ]]; then
  echo "HERON is not installed on this computer yet. Run the full installer first: sudo bash deploy/install.sh"
  exit 1
fi

old_version="$(/opt/heron/.venv/bin/python -c 'import sys; sys.path.insert(0, "/opt/heron"); import heron_payload; print(heron_payload.__version__)' 2>/dev/null || echo unknown)"
new_version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC/heron_payload/__init__.py")"
echo "== HERON payload $old_version -> $new_version"

fingerprint() { (cd "$1" 2>/dev/null && find . -type f ! -name '.DS_Store' -print0 | sort -z | xargs -0 -r sha256sum | sha256sum | cut -d' ' -f1) || echo none; }
old_recorder="$(fingerprint /opt/heron/recorder)"
old_reqs="$(sha256sum /opt/heron/requirements.txt 2>/dev/null | cut -d' ' -f1 || echo none)"
old_unit="$(sha256sum /opt/heron/deploy/heron-payload.service 2>/dev/null | cut -d' ' -f1 || echo none)"

if systemctl is-active --quiet heron-payload; then
  state="$(/opt/heron/.venv/bin/python - <<'PY' 2>/dev/null || true
import json, urllib.request
try:
    print(json.load(urllib.request.urlopen("http://127.0.0.1:8080/api/state", timeout=2))["status"]["state"])
except Exception:
    pass
PY
)"
  if [[ "$state" == "RECORDING" || "$state" == "AUTO_ARMED" || "$state" == "FINALIZING" ]]; then
    echo "The payload is $state. Stop it from the ground station first, then run this again."
    exit 1
  fi
fi

echo "== Copying code to /opt/heron"
rsync -a --delete --exclude '__pycache__' --exclude '.venv' --exclude '.DS_Store' \
  "$SRC/heron_payload" "$SRC/recorder" "$SRC/deploy" "$SRC/requirements.txt" /opt/heron/

if [[ "$(sha256sum /opt/heron/requirements.txt | cut -d' ' -f1)" != "$old_reqs" ]]; then
  echo "== Python packages changed; installing"
  /opt/heron/.venv/bin/pip install -q -r /opt/heron/requirements.txt || {
    echo "   Could not install the new Python packages (no internet?). Share your laptop's internet to the NUC and run this again."
    exit 1; }
fi

if [[ "$(fingerprint /opt/heron/recorder)" != "$old_recorder" || ! -x /opt/heron/bin/heron-recorder ]]; then
  echo "== Recorder source changed; rebuilding (about a minute)"
  BUILD="$(mktemp -d)"
  cmake -S /opt/heron/recorder -B "$BUILD" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/heron >/dev/null
  cmake --build "$BUILD" -j"$(nproc)"
  cmake --install "$BUILD" >/dev/null
  rm -rf "$BUILD"
else
  echo "== Recorder unchanged; not rebuilding"
fi

if [[ "$(sha256sum /opt/heron/deploy/heron-payload.service | cut -d' ' -f1)" != "$old_unit" ]]; then
  echo "== Service file changed; installing it"
  run_as="$(systemctl show -p User --value heron-payload)"; run_as="${run_as:-heron}"
  install -m 0644 /opt/heron/deploy/heron-payload.service /etc/systemd/system/heron-payload.service
  sed -i "s/^User=heron/User=$run_as/; s/^Group=heron/Group=$run_as/" /etc/systemd/system/heron-payload.service
  systemctl daemon-reload
fi

echo "== Restarting the payload service"
systemctl restart heron-payload
sleep 3
if systemctl is-active --quiet heron-payload; then
  echo "Updated to $new_version and running. Reload http://$(hostname).local:8080 (or http://192.168.137.10:8080)."
else
  echo "The service did not start. Recent log:"
  journalctl -u heron-payload -n 30 --no-pager
  exit 1
fi
