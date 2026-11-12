"""
Receiver trajectory input for notebook 03 (plan section 3.1, D-087, D-088).

Every position method (PPK, RTK, PPP, PPP-AR, a surveyed static point) gives the
same thing: a time series of one reference point. Notebook 03 reads it through
`Trajectory`, so the method can change and the tracking does not change.

First version (D-088): RTKLIB `.pos` files and a static point. The rover can be
the Here3 now or a ZED-F9P later (D-087); both reach this module as a `.pos`
file from RTKLIB. UBX, CSV and the notebook 02 solution are later work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicHermiteSpline, CubicSpline

from . import frames

GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)

# RTKLIB quality flag Q: 1 fix, 2 float, 3 SBAS, 4 DGPS, 5 single, 6 PPP.
RTKLIB_QUALITY = {1: "fix", 2: "float", 3: "sbas", 4: "dgps", 5: "single", 6: "ppp"}


@dataclass
class Trajectory:
    """
    A time series of one reference point on the drone.

    `time_gpst_s` is continuous GPS seconds since the GPS epoch (week * 604800 +
    time of week), so a run that crosses a week boundary has no jump.
    `reference_point` names the point (for example "ARP_rover"); `antennas` uses it
    to pick the lever arm to subtract.
    """

    time_gpst_s: np.ndarray
    position_ecef_m: np.ndarray
    velocity_ecef_mps: np.ndarray | None = None
    sigma_enu_m: np.ndarray | None = None
    quality: np.ndarray | None = None
    reference_point: str = "ARP_rover"
    source: str = ""
    extras: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.time_gpst_s = np.asarray(self.time_gpst_s, dtype=float)
        self.position_ecef_m = np.atleast_2d(np.asarray(self.position_ecef_m, dtype=float))
        if self.position_ecef_m.shape != (len(self.time_gpst_s), 3):
            raise ValueError("position_ecef_m must have shape (len(time_gpst_s), 3)")
        if len(self.time_gpst_s) > 1 and np.any(np.diff(self.time_gpst_s) <= 0):
            raise ValueError("trajectory times must increase strictly")

    def interpolate(self, t_gpst_s) -> tuple[np.ndarray, np.ndarray]:
        """
        Position and velocity (N, 3) at `t_gpst_s`.

        Cubic Hermite when the velocity is known (it matches both position and
        velocity at each record), otherwise a cubic spline whose derivative is the
        velocity. A static trajectory (one record) returns that point and zero
        velocity. Times outside the records give NaN, never an extrapolation:
        an extrapolated drone position would put a wrong model in the correlator
        without a warning.
        """
        t = np.atleast_1d(np.asarray(t_gpst_s, dtype=float))
        if len(self.time_gpst_s) == 1:
            pos = np.repeat(self.position_ecef_m, len(t), axis=0)
            return pos, np.zeros_like(pos)
        if self.velocity_ecef_mps is not None:
            spline = CubicHermiteSpline(self.time_gpst_s, self.position_ecef_m,
                                        self.velocity_ecef_mps, axis=0, extrapolate=False)
        else:
            spline = CubicSpline(self.time_gpst_s, self.position_ecef_m, axis=0,
                                 extrapolate=False)
        return spline(t), spline(t, 1)

    def mask_quality(self, accepted: tuple[str, ...]) -> "Trajectory":
        """A copy that keeps only records whose quality name is in `accepted`."""
        if self.quality is None:
            return self
        keep = np.isin(self.quality, accepted)
        return Trajectory(
            self.time_gpst_s[keep], self.position_ecef_m[keep],
            None if self.velocity_ecef_mps is None else self.velocity_ecef_mps[keep],
            None if self.sigma_enu_m is None else self.sigma_enu_m[keep],
            self.quality[keep], self.reference_point, self.source, dict(self.extras),
        )


def static_trajectory(lat_deg: float, lon_deg: float, h_ell_m: float,
                      reference_point: str = "ARP_rover") -> Trajectory:
    """A surveyed static point (bridge or ground test, D-088)."""
    pos = frames.geodetic_to_ecef(np.deg2rad(lat_deg), np.deg2rad(lon_deg), h_ell_m)
    return Trajectory(np.array([0.0]), pos[None, :], None, None,
                      np.array(["static"]), reference_point, "static")


def _gpst_seconds(date_text: str, time_text: str, time_system: str) -> float:
    """Continuous GPS seconds from an RTKLIB date and time (GPST or UTC)."""
    stamp = datetime.strptime(f"{date_text} {time_text}", "%Y/%m/%d %H:%M:%S.%f")
    seconds = (stamp.replace(tzinfo=timezone.utc) - GPS_EPOCH).total_seconds()
    if time_system == "UTC":
        raise ValueError(
            "RTKLIB .pos in UTC needs the leap-second count; write the file in GPST "
            "(RTKLIB option out-timesys = gpst) instead."
        )
    return seconds


def read_rtklib_pos(path: str | Path, reference_point: str = "ARP_rover") -> Trajectory:
    """
    Read an RTKLIB `.pos` file (D-088).

    Supported layouts: GPST time as `yyyy/mm/dd hh:mm:ss.sss` or as `week tow`,
    and position as XYZ-ECEF or as latitude/longitude/height in degrees. The
    header line that starts with `%  GPST` names the columns. The velocity
    columns (`vn(m/s)` etc., RTKLIB option out-outvel) are used when present.
    """
    path = Path(path)
    header = None
    rows: list[list[str]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("%"):
            if "GPST" in line or "UTC" in line:
                header = line[1:].split()
            continue
        if line.strip():
            rows.append(line.split())
    if header is None:
        raise ValueError(f"{path}: no RTKLIB column header line ('%  GPST ...') found")
    if not rows:
        raise ValueError(f"{path}: no solution records")

    time_system = "UTC" if header[0] == "UTC" else "GPST"
    week_tow = len(rows[0]) > 1 and "/" not in rows[0][0]
    offset = 2  # date + time, or week + tow
    names = header[1:]
    data = np.array([[float(v) for v in r[offset:offset + len(names)]] for r in rows])
    col = {name: i for i, name in enumerate(names)}

    if week_tow:
        times = np.array([float(r[0]) * 604800.0 + float(r[1]) for r in rows])
    else:
        times = np.array([_gpst_seconds(r[0], r[1], time_system) for r in rows])

    if "x-ecef(m)" in col:
        pos = data[:, [col["x-ecef(m)"], col["y-ecef(m)"], col["z-ecef(m)"]]]
        sig = None
        if "sdx(m)" in col:
            sig = data[:, [col["sdx(m)"], col["sdy(m)"], col["sdz(m)"]]]
    elif "latitude(deg)" in col:
        pos = frames.geodetic_to_ecef(np.deg2rad(data[:, col["latitude(deg)"]]),
                                      np.deg2rad(data[:, col["longitude(deg)"]]),
                                      data[:, col["height(m)"]])
        sig = None
        if "sde(m)" in col:
            sig = data[:, [col["sde(m)"], col["sdn(m)"], col["sdu(m)"]]]
    else:
        raise ValueError(f"{path}: position columns not recognised: {names}")

    vel = None
    if "vn(m/s)" in col:
        lat, lon, _ = frames.ecef_to_geodetic(pos)
        ned = data[:, [col["vn(m/s)"], col["ve(m/s)"], col["vu(m/s)"]]].copy()
        ned[:, 2] *= -1.0  # up -> down
        rot = np.transpose(frames.rot_ecef_to_ned(lat, lon), (0, 2, 1))
        vel = np.einsum("nij,nj->ni", rot, ned)

    quality = None
    if "Q" in col:
        quality = np.array([RTKLIB_QUALITY.get(int(q), str(int(q))) for q in data[:, col["Q"]]])

    return Trajectory(times, pos, vel, sig, quality, reference_point, f"rtklib:{path.name}")


TRAJECTORY_READERS = {
    "rtklib_pos": read_rtklib_pos,
}
