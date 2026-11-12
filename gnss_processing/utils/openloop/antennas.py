"""
From the trajectory point to each antenna phase centre (plan section 3.2).

R_A = P + C_n^e C_b^n (l_A - l_P)                         (position)
V_A = V_P + C_n^e C_b^n (omega_nb^b x (l_A - l_P))        (velocity)

`l` are lever arms in the FRD body frame from one body origin. The phase-centre
offset (PCO) is a fixed vector along the antenna boresight for each band (D-090).
The lever arms are null until they are measured (D-091); `require_lever_arms`
stops with a clear message in that case.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import frames
from .attitude import Attitude
from .trajectory import Trajectory


@dataclass
class AntennaGeometry:
    """
    One antenna on the airframe.

    `lever_arm_b` is the antenna reference point (ARP) in the FRD body frame, metres.
    `boresight_b` is the unit boresight in the body frame: (0, 0, -1) for a zenith
    antenna (up is minus down) and (0, 0, 1) for a nadir antenna.
    `pco_m` maps a band name ("L1", "L5") to the offset along the boresight.
    """

    name: str
    lever_arm_b: np.ndarray | None
    boresight_b: np.ndarray
    pco_m: dict[str, float] = field(default_factory=dict)

    def phase_centre_b(self, band: str) -> np.ndarray:
        """The phase centre in the body frame for `band` (ARP + PCO along boresight)."""
        if self.lever_arm_b is None:
            raise ValueError(
                f"antenna '{self.name}': lever arm not set (D-091). Measure it and put it "
                "in configs/openloop.yml (antennas.<name>.lever_arm_b, metres, FRD)."
            )
        pco = float(self.pco_m.get(band, 0.0))
        return np.asarray(self.lever_arm_b, dtype=float) + pco * frames.unit(self.boresight_b)


def require_lever_arms(antennas: dict[str, AntennaGeometry]) -> None:
    """Stop with one message that lists every antenna that has no lever arm (D-091)."""
    missing = [name for name, a in antennas.items() if a.lever_arm_b is None]
    if missing:
        raise ValueError(
            "lever arms not set for: " + ", ".join(missing)
            + ". They are not measured yet (D-091). Put the measured FRD vectors in "
            "configs/openloop.yml, or use the synthetic demo."
        )


def phase_centre_track(
    trajectory: Trajectory,
    attitude: Attitude,
    reference: AntennaGeometry,
    target: AntennaGeometry,
    band: str,
    t_gpst_s,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Position and velocity (N, 3) of `target`'s phase centre at `t_gpst_s`.

    `reference` is the antenna the trajectory refers to (the rover antenna); its
    PCO for `band` is not used, because RTKLIB already refers the solution to the
    ARP or APC that the config states (`trajectory.reference_point`).
    """
    t = np.atleast_1d(np.asarray(t_gpst_s, dtype=float))
    p, v = trajectory.interpolate(t)
    if reference.lever_arm_b is None or target.lever_arm_b is None:
        require_lever_arms({reference.name: reference, target.name: target})
    arm_b = target.phase_centre_b(band) - np.asarray(reference.lever_arm_b, dtype=float)
    lat, lon, _ = frames.ecef_to_geodetic(p)
    c_n_e = np.transpose(frames.rot_ecef_to_ned(lat, lon), (0, 2, 1))
    c_b_n = attitude.dcm_body_to_ned(t)
    c_b_e = np.einsum("nij,njk->nik", c_n_e, c_b_n)
    position = p + np.einsum("nij,j->ni", c_b_e, arm_b)
    omega = attitude.body_rates(t)
    velocity = v + np.einsum("nij,nj->ni", c_b_e, np.cross(omega, arm_b))
    return position, velocity
