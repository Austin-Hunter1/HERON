"""Where recordings go: the data drive when it is mounted, otherwise the NUC's own SSD.

The default setups record to /media/DataStore/heron. If nothing is mounted at /media/DataStore
(no second drive, or it failed to mount) the payload records to ~/heron_recordings on the system
SSD instead of refusing. The disk reserve still applies, so the system drive never fills up.
"""
import os
from pathlib import Path

DATA_DRIVE = "data drive"
SYSTEM_SSD = "NUC SSD"


def default_ssd_dir():
    return Path.home() / "heron_recordings"


def _existing(path):
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def drive_label(path):
    """'NUC SSD' if the folder is on the same filesystem as /, else 'data drive'."""
    try:
        return SYSTEM_SSD if os.stat(_existing(path)).st_dev == os.stat("/").st_dev else DATA_DRIVE
    except OSError:
        return SYSTEM_SSD


def resolve(config, ssd_dir=None):
    """The folder this setup records into right now, and why."""
    ssd_dir = Path(ssd_dir) if ssd_dir else default_ssd_dir()
    s = config["storage"]
    mount = s.get("require_mount") or ""
    if mount and not os.path.ismount(mount):
        return dict(path=str(ssd_dir), drive=SYSTEM_SSD, fallback=True, configured=s["path"],
                    mount=mount, note="Data drive not mounted; recording to NUC SSD")
    return dict(path=s["path"], drive=drive_label(s["path"]), fallback=False, configured=s["path"], note=None)


def candidate_roots(config, ssd_dir=None):
    """Every folder that may hold recordings, so old sessions stay visible after a change."""
    ssd_dir = Path(ssd_dir) if ssd_dir else default_ssd_dir()
    roots = []
    for p in ([resolve(config, ssd_dir)["path"], config["storage"]["path"]] if config else []) + \
            ["/media/DataStore/heron", str(ssd_dir)]:
        if p not in roots:
            roots.append(p)
    return [Path(p) for p in roots]
