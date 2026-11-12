"""NUC temperatures from Linux's hardware sensors (/sys/class/hwmon), no extra packages.

cpu_c  Intel "coretemp" Package temperature (or the hottest core), else the x86_pkg_temp thermal zone.
ssd_c  NVMe "Composite" temperature (or a SATA drive via the drivetemp module).
Either is None when the sensor is not there (e.g. on a Mac or in tests).
"""
from __future__ import annotations

from pathlib import Path

HWMON = Path("/sys/class/hwmon")
THERMAL = Path("/sys/class/thermal")

CPU_WARN_C = 90.0      # Intel NUC CPUs throttle around 100 C
SSD_WARN_C = 70.0      # most NVMe drives throttle around 70-80 C


def _read(path: Path):
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _milli(path: Path):
    raw = _read(path)
    try:
        return round(int(raw) / 1000.0, 1) if raw is not None else None
    except ValueError:
        return None


def _temps(dev: Path):
    """[(label, celsius)] for every tempN_input in one hwmon device."""
    out = []
    for f in sorted(dev.glob("temp*_input")):
        c = _milli(f)
        if c is not None:
            out.append(((_read(f.with_name(f.name.replace("_input", "_label"))) or "").lower(), c))
    return out


def read_temps(hwmon: Path = HWMON, thermal: Path = THERMAL) -> dict:
    cpu = ssd = None
    try:
        devices = sorted(hwmon.iterdir())
    except OSError:
        devices = []
    for dev in devices:
        name = (_read(dev / "name") or "").lower()
        temps = _temps(dev)
        if not temps:
            continue
        if name == "coretemp" and cpu is None:
            pkg = [c for label, c in temps if label.startswith("package")]
            cpu = pkg[0] if pkg else max(c for _, c in temps)
        elif name in ("nvme", "drivetemp") and ssd is None:
            comp = [c for label, c in temps if label.startswith("composite")]
            ssd = comp[0] if comp else temps[0][1]
    if cpu is None:
        try:
            zones = sorted(thermal.glob("thermal_zone*"))
        except OSError:
            zones = []
        for z in zones:
            if (_read(z / "type") or "") in ("x86_pkg_temp", "TCPU", "cpu-thermal"):
                cpu = _milli(z / "temp")
                if cpu is not None:
                    break
    return {"cpu_c": cpu, "ssd_c": ssd}
