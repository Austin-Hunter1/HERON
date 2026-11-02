#!/usr/bin/env python3
"""Run real ArduCopter firmware (SITL) for the Simulate and Fly decks.

This is what Mission Planner's simulator runs: the ArduCopter flight code built
for the computer, flying ArduPilot's multicopter physics model. Mission logic,
speeds, S-curves, yaw, RTL and failsafes are the firmware's own.

Build once:
    git clone --branch Copter-4.6.3 --recurse-submodules https://github.com/ArduPilot/ardupilot ~/ardupilot
    cd ~/ardupilot && ./waf configure --board sitl && ./waf copter

Run, then in HERON connect "TCP · ArduPilot SITL" (127.0.0.1:5760):
    .venv/bin/python scripts/sitl.py --params ~/Downloads/AURELIADRONE.param
    .venv/bin/python scripts/sitl.py --mission output/HERON_20261006_1730.waypoints
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "output" / "sitl"
DEFAULT_HOME = (40.086045, -105.233634, 1582.0)  # SURGE pad, north shore

# Mission, speed and failsafe settings from the real aircraft.
# Leave attitude, rate, motor and loiter gains at SITL's: the Aurelia X6 numbers
# are for a heavy 6S airframe. SITL is a light 3S hexa, so those gains make it
# overshoot and look like it is flying drunk.
COPY_PREFIXES = (
    "WPNAV_", "WP_YAW_BEHAVIOR", "RTL_", "LAND_",
    "AUTO_OPTIONS", "GUID_OPTIONS", "MIS_", "DISARM_DELAY",
    "FENCE_", "FS_GCS_", "FS_OPTIONS", "FS_THR_", "FS_EKF_", "FS_CRASH_CHECK",
    "BATT_FS_", "BATT_LOW_", "BATT_CRT_", "BATT_ARM_", "BATT_CAPACITY",
    "ARMING_CHECK", "SYSID_MYGCS",
)


def read_params(path: Path) -> dict[str, str]:
    params = {}
    for line in path.read_text().splitlines():
        parts = line.replace(",", " ").split()
        if len(parts) >= 2 and not line.startswith("#"):
            params[parts[0]] = parts[1]
    return params


def mission_pad(path: Path) -> tuple[float, float]:
    rows = [line.split("\t") for line in path.read_text().splitlines()[1:] if line.strip()]
    takeoff = next(r for r in rows if r[3] == "22")
    return float(takeoff[8]), float(takeoff[9])


def overlay(params: dict[str, str]) -> dict[str, str]:
    out = {k: v for k, v in params.items() if k.startswith(COPY_PREFIXES)}
    capacity = float(params.get("BATT_CAPACITY", 0))
    if capacity > 0:
        out["SIM_BATT_CAP_AH"] = f"{capacity / 1000:g}"
    # SITL thrust scales with pack voltage. The X6 is 6S (~25 V); the model is 3S.
    # Forcing 12.6 V keeps hover around 40% throttle. Scale the failsafe volts to match.
    out["SIM_BATT_VOLTAGE"] = "12.6"
    reference = float(params.get("BATT_ARM_VOLT", 0) or params.get("BATT_LOW_VOLT", 0))
    cells = round(reference / 3.75) if reference > 0 else 3
    for key in ("BATT_LOW_VOLT", "BATT_CRT_VOLT", "BATT_ARM_VOLT"):
        if key in out and cells != 3:
            out[key] = f"{float(out[key]) * 3 / cells:.2f}"
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ardupilot", type=Path, default=Path.home() / "ardupilot")
    ap.add_argument("--params", type=Path, help="Parameter file saved from the real aircraft")
    ap.add_argument("--mission", type=Path, help="Start on this mission's launch pad")
    ap.add_argument("--home", help="lat,lon[,alt_msl] to start at instead")
    ap.add_argument("--heading", type=float, default=0, help="Nose heading at start, degrees")
    ap.add_argument("--speedup", type=float, default=1, help="Keep 1: live reflections use the wall clock")
    ap.add_argument("--keep", action="store_true", help="Keep parameters changed in a previous run")
    args = ap.parse_args()

    binary = args.ardupilot / "build" / "sitl" / "bin" / "arducopter"
    if not binary.exists():
        raise SystemExit(f"{binary} not found. Build it first (see the top of this script).")

    lat, lon, alt = DEFAULT_HOME
    if args.mission:
        lat, lon = mission_pad(args.mission)
    if args.home:
        parts = [float(v) for v in args.home.split(",")]
        lat, lon = parts[:2]
        alt = parts[2] if len(parts) > 2 else alt

    RUN.mkdir(parents=True, exist_ok=True)
    defaults = [args.ardupilot / "Tools" / "autotest" / "default_params" / name
                for name in ("copter.parm", "copter-hexa.parm", "copter-X.parm")]
    if args.params:
        copied = overlay(read_params(args.params.expanduser()))
        if args.speedup > 1 and "FS_GCS_TIMEOUT" in copied:
            # HERON's heartbeat is 1 Hz wall time, so sim time between heartbeats grows with speedup.
            copied["FS_GCS_TIMEOUT"] = f"{float(copied['FS_GCS_TIMEOUT']) * args.speedup:g}"
        aircraft = RUN / "aircraft.parm"
        aircraft.write_text("".join(f"{k} {v}\n" for k, v in sorted(copied.items())))
        defaults.append(aircraft)
        print(f"Copied {len(copied)} settings from {args.params.name}")

    cmd = [str(binary), "--model", "hexax", "--speedup", f"{args.speedup:g}",
           "--home", f"{lat:.7f},{lon:.7f},{alt:g},{args.heading:g}",
           "--defaults", ",".join(str(p) for p in defaults), "-I0"]
    if not args.keep:
        cmd.append("--wipe")
    print(f"ArduCopter SITL at {lat:.6f}, {lon:.6f}. Connect HERON to TCP 127.0.0.1:5760.")
    os.chdir(RUN)
    os.execv(cmd[0], cmd)


if __name__ == "__main__":
    main()
