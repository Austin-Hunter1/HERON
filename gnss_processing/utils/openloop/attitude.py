"""
Drone attitude for the lever-arm correction (plan section 3.2, D-089).

Source: the ArduPilot DataFlash log, read after the flight (`ATT` or `XKF1`).
Fallback: a constant attitude from config.

Why a CSV export and not the binary `.bin` log: reading the binary log needs
`pymavlink`, which is not a dependency of `gnss_processing`. The first version
reads a CSV export (for example `mavlogdump.py --format csv --types ATT log.bin`,
or Mission Planner "Create CSV"). The time tie to GPS time uses the `GPS`
messages of the same log, which carry `GWk` and `GMS` with `TimeUS`. This is
listed in `docs/NB03_CONCERNS.md`.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.interpolate import interp1d

from . import frames


@dataclass
class Attitude:
    """Roll, pitch, yaw (rad) of the body frame FRD against NED, at GPS times."""

    time_gpst_s: np.ndarray
    roll_rad: np.ndarray
    pitch_rad: np.ndarray
    yaw_rad: np.ndarray
    source: str = ""

    def at(self, t_gpst_s) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Roll, pitch, yaw at `t_gpst_s` by linear interpolation.

        Yaw is unwrapped first, so a heading through north does not interpolate
        through south. A constant attitude (one record) is returned as it is.
        """
        t = np.atleast_1d(np.asarray(t_gpst_s, dtype=float))
        if len(self.time_gpst_s) == 1:
            ones = np.ones_like(t)
            return self.roll_rad[0] * ones, self.pitch_rad[0] * ones, self.yaw_rad[0] * ones
        out = []
        for angle in (self.roll_rad, self.pitch_rad, np.unwrap(self.yaw_rad)):
            f = interp1d(self.time_gpst_s, angle, bounds_error=False, fill_value=np.nan)
            out.append(f(t))
        return out[0], out[1], out[2]

    def body_rates(self, t_gpst_s) -> np.ndarray:
        """
        Body angular rate omega_nb^b (N, 3) in rad/s, from the Euler-angle rates.

        p = phi_dot - psi_dot sin(theta)
        q = theta_dot cos(phi) + psi_dot cos(theta) sin(phi)
        r = -theta_dot sin(phi) + psi_dot cos(theta) cos(phi)
        """
        t = np.atleast_1d(np.asarray(t_gpst_s, dtype=float))
        if len(self.time_gpst_s) == 1:
            return np.zeros((len(t), 3))
        h = 0.01
        r0, p0, y0 = self.at(t)
        r1, p1, y1 = self.at(t + h)
        rm, pm, ym = self.at(t - h)
        dr, dp, dy = (r1 - rm) / (2 * h), (p1 - pm) / (2 * h), (y1 - ym) / (2 * h)
        p = dr - dy * np.sin(p0)
        q = dp * np.cos(r0) + dy * np.cos(p0) * np.sin(r0)
        r = -dp * np.sin(r0) + dy * np.cos(p0) * np.cos(r0)
        return np.stack([p, q, r], axis=-1)

    def dcm_body_to_ned(self, t_gpst_s) -> np.ndarray:
        """C_b^n (N, 3, 3) at `t_gpst_s`."""
        return frames.rot_body_to_ned(*self.at(t_gpst_s))


def constant_attitude(roll_deg: float = 0.0, pitch_deg: float = 0.0,
                      yaw_deg: float = 0.0) -> Attitude:
    """A constant attitude from config (D-089 fallback)."""
    return Attitude(np.array([0.0]), np.array([np.deg2rad(roll_deg)]),
                    np.array([np.deg2rad(pitch_deg)]), np.array([np.deg2rad(yaw_deg)]),
                    "constant")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def time_us_to_gpst(gps_rows: list[dict[str, str]]):
    """
    A linear map from the autopilot boot time (TimeUS, us) to GPS seconds.

    Each ArduPilot `GPS` message has `TimeUS`, `GWk` (week) and `GMS` (ms of
    week). A least-squares line through all of them gives the map. The residual
    RMS goes back to the caller so the notebook can show how good the tie is.
    """
    time_us = np.array([float(r["TimeUS"]) for r in gps_rows])
    gpst = np.array([float(r["GWk"]) * 604800.0 + float(r["GMS"]) * 1e-3 for r in gps_rows])
    valid = np.array([float(r["GWk"]) > 0 for r in gps_rows])
    if valid.sum() < 2:
        raise ValueError("the GPS messages in the log have no valid GPS week and time")
    a, b = np.polyfit(time_us[valid] * 1e-6, gpst[valid], 1)
    residual = gpst[valid] - (a * time_us[valid] * 1e-6 + b)
    return (lambda us: a * np.asarray(us, dtype=float) * 1e-6 + b), float(np.std(residual))


def read_ardupilot_csv(att_csv: str | Path, gps_csv: str | Path,
                       message: str = "ATT") -> Attitude:
    """
    Attitude from CSV exports of an ArduPilot log (D-089).

    `ATT` gives `Roll`, `Pitch`, `Yaw` in degrees. `XKF1` gives the same names.
    ArduPilot reports yaw as a heading in 0..360 degrees, which is the NED yaw of
    the ZYX convention.
    """
    att_rows = _read_csv(Path(att_csv))
    gps_rows = _read_csv(Path(gps_csv))
    if not att_rows:
        raise ValueError(f"{att_csv}: no {message} rows")
    to_gpst, rms = time_us_to_gpst(gps_rows)
    t = to_gpst([float(r["TimeUS"]) for r in att_rows])
    order = np.argsort(t)
    roll = np.deg2rad([float(r["Roll"]) for r in att_rows])[order]
    pitch = np.deg2rad([float(r["Pitch"]) for r in att_rows])[order]
    yaw = np.deg2rad([float(r["Yaw"]) for r in att_rows])[order]
    return Attitude(t[order], roll, pitch, yaw,
                    f"ardupilot:{message}:{Path(att_csv).name} (time tie rms {rms*1e3:.1f} ms)")
