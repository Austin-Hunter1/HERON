"""
Broadcast ephemerides, and the two orbit models GPS actually uses.

Every GPS navigation message ultimately answers the same question -- where was the
satellite when it transmitted, and what was its clock doing -- but the legacy and
modernised messages parameterise the answer differently:

    LNAV (L1 C/A, and every RINEX navigation file)
        Classic Keplerian: sqrt(A) and a mean motion correction `deln`, both fixed
        over the fit interval.  IS-GPS-200N Table 20-IV.

    CNAV (L2C, L5) and CNAV-2 (L1C)
        Quasi-Keplerian with rates: the semi-major axis is stated as a difference
        `delta_a` from a fixed reference plus a rate `a_dot`, the mean motion
        correction gains its own rate `delta_n0_dot`, and the node rate is a
        difference from a reference rate.  IS-GPS-200N Table 30-II.

The spec is emphatic that the two data sets must not be mixed (IS-GPS-200N
30.3.1), so they are two dataclasses here rather than one with optional fields.
What they share is the Kepler solve and the rotation into ECEF, and that lives in
`_orbit_from_kepler_elements` -- given the semi-major axis and mean motion each
model derives its own way, the rest of the algorithm is genuinely identical.

`gnss_tools.misc.gps_ephemeris` already implements the LNAV model correctly, but
its convenience wrapper `compute_gps_satellite_pvt_from_ephemeris` calls both of
its own helpers with the wrong signatures and cannot run.  Rather than reach
around that from every caller, `LnavEphemeris` re-states the LNAV algorithm here
so both models present one interface -- and `tests/test_nav_ephemeris.py` checks
it against the `gnss_tools` low-level functions so the duplication cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# WGS 84 constants, as the interface specs state them for GPS users.  These are
# the *defined* values for the broadcast algorithm and deliberately differ in the
# last digits from the best physical estimates -- using anything else moves
# satellite positions by metres.
MU_EARTH = 3.986005e14  # m^3/s^2, WGS 84 gravitational constant for GPS users
OMEGA_E_DOT = 7.2921151467e-5  # rad/s, WGS 84 earth rotation rate
SPEED_OF_LIGHT = 2.99792458e8  # m/s
# Relativistic correction factor F = -2 sqrt(mu) / c^2 (IS-GPS-200N 20.3.3.3.3.1).
RELATIVISTIC_F = -4.442807633e-10

# CNAV states the semi-major axis and node rate as differences from these
# references (IS-GPS-200N Table 30-I footnotes).
CNAV_A_REF = 26559710.0  # meters
CNAV_OMEGA_DOT_REF = -2.6e-9  # semi-circles/second

SECONDS_PER_WEEK = 604800
HALF_WEEK_SECONDS = 302400

SEMICIRCLES_TO_RADIANS = np.pi


def _wrap_time_from_epoch(dt: float) -> float:
    """
    Fold a time difference into +/- half a week.

    Both specs spell this out because `t - toe` is computed from a time of week and
    a week rollover between them otherwise produces a 604,800 second error --
    which is not a subtle wrongness, it puts the satellite on the other side of the
    earth.
    """
    if dt > HALF_WEEK_SECONDS:
        return dt - SECONDS_PER_WEEK
    if dt < -HALF_WEEK_SECONDS:
        return dt + SECONDS_PER_WEEK
    return dt


def _solve_kepler(mean_anomaly: float, eccentricity: float, iterations: int = 10) -> float:
    """
    Eccentric anomaly from Kepler's equation, by the spec's own Newton iteration.

    The spec asks for "a minimum of three iterations"; ten costs nothing here and
    converges to machine precision for GPS's near-circular orbits.
    """
    E = mean_anomaly
    for _ in range(iterations):
        E = E + (mean_anomaly - E + eccentricity * np.sin(E)) / (
            1.0 - eccentricity * np.cos(E)
        )
    return E


@dataclass(frozen=True)
class OrbitState:
    """One satellite's position and clock correction at one instant."""

    position_ecef_m: np.ndarray
    """(3,) ECEF position of the antenna phase centre, metres."""

    eccentric_anomaly_rad: float
    """Needed by the caller only to form the relativistic clock term, which the
    clock correction already applies -- exposed because it is the one intermediate
    that is genuinely useful outside."""

    time_from_ephemeris_s: float
    """`tk`, after week-crossover folding.  A large magnitude means the ephemeris
    is being used well outside its fit interval."""


def _orbit_from_kepler_elements(
    *,
    semi_major_axis_m: float,
    corrected_mean_motion: float,
    tk: float,
    M0: float,
    e: float,
    omega: float,
    i0: float,
    i_dot: float,
    Omega0: float,
    Omega_dot: float,
    toe: float,
    Cus: float,
    Cuc: float,
    Crs: float,
    Crc: float,
    Cis: float,
    Cic: float,
) -> OrbitState:
    """
    The half of the algorithm both models share, from mean anomaly to ECEF.

    Every angular argument is in radians here -- the messages state them in
    semi-circles, and each model's `orbit_state` converts before calling in.  Doing
    that conversion at the boundary rather than inside means this function can be
    read against the spec's equations directly, where everything is radians.
    """
    Mk = M0 + corrected_mean_motion * tk
    Ek = _solve_kepler(Mk, e)

    # True anomaly, taken through atan2 so the quadrant is unambiguous.
    sin_nu = np.sqrt(1.0 - e * e) * np.sin(Ek)
    cos_nu = np.cos(Ek) - e
    nu = np.arctan2(sin_nu, cos_nu)

    phi = nu + omega  # argument of latitude
    two_phi = 2.0 * phi
    sin_2phi, cos_2phi = np.sin(two_phi), np.cos(two_phi)

    delta_u = Cus * sin_2phi + Cuc * cos_2phi
    delta_r = Crs * sin_2phi + Crc * cos_2phi
    delta_i = Cis * sin_2phi + Cic * cos_2phi

    u = phi + delta_u
    r = semi_major_axis_m * (1.0 - e * np.cos(Ek)) + delta_r
    i = i0 + i_dot * tk + delta_i

    x_orbital = r * np.cos(u)
    y_orbital = r * np.sin(u)

    # Corrected longitude of ascending node.  The `- OMEGA_E_DOT * toe` term is
    # what puts the result in ECEF rather than an inertial frame.
    Omega_k = Omega0 + (Omega_dot - OMEGA_E_DOT) * tk - OMEGA_E_DOT * toe
    sin_O, cos_O = np.sin(Omega_k), np.cos(Omega_k)
    cos_i = np.cos(i)

    position = np.array(
        [
            x_orbital * cos_O - y_orbital * cos_i * sin_O,
            x_orbital * sin_O + y_orbital * cos_i * cos_O,
            y_orbital * np.sin(i),
        ]
    )
    return OrbitState(position, float(Ek), float(tk))


@dataclass(frozen=True)
class LnavEphemeris:
    """
    Legacy Keplerian ephemeris -- GPS LNAV, and every RINEX navigation record.

    Angles are stored in the units the message states them in: semi-circles for the
    angular elements, radians for the harmonic correction terms.  `orbit_state`
    converts.  Keeping the stored values in message units is what lets a decoded
    ephemeris be compared field-by-field against a RINEX one without a unit
    conversion standing between them.
    """

    sat_id: str
    week: int
    toe: float
    sqrt_a: float
    e: float
    i0: float  # semi-circles
    i_dot: float  # semi-circles/s
    Omega0: float  # semi-circles
    Omega_dot: float  # semi-circles/s
    omega: float  # semi-circles
    M0: float  # semi-circles
    deln: float  # semi-circles/s
    Cuc: float
    Cus: float
    Crc: float
    Crs: float
    Cic: float
    Cis: float
    toc: float
    af0: float
    af1: float
    af2: float
    tgd: float = 0.0
    iode: int | None = None
    health: int = 0

    @property
    def semi_major_axis_m(self) -> float:
        return self.sqrt_a * self.sqrt_a

    def orbit_state(self, t_gps_sec: float) -> OrbitState:
        """
        Position at GPS time-of-week `t_gps_sec`, which must be the time of
        *transmission* -- receive time minus the transit time, not receive time.
        """
        tk = _wrap_time_from_epoch(t_gps_sec - self.toe)
        A = self.semi_major_axis_m
        n0 = np.sqrt(MU_EARTH / (A * A * A))
        n = n0 + self.deln * SEMICIRCLES_TO_RADIANS
        return _orbit_from_kepler_elements(
            semi_major_axis_m=A,
            corrected_mean_motion=n,
            tk=tk,
            M0=self.M0 * SEMICIRCLES_TO_RADIANS,
            e=self.e,
            omega=self.omega * SEMICIRCLES_TO_RADIANS,
            i0=self.i0 * SEMICIRCLES_TO_RADIANS,
            i_dot=self.i_dot * SEMICIRCLES_TO_RADIANS,
            Omega0=self.Omega0 * SEMICIRCLES_TO_RADIANS,
            Omega_dot=self.Omega_dot * SEMICIRCLES_TO_RADIANS,
            toe=self.toe,
            Cus=self.Cus,
            Cuc=self.Cuc,
            Crs=self.Crs,
            Crc=self.Crc,
            Cis=self.Cis,
            Cic=self.Cic,
        )

    def clock_polynomial_s(self, t_gps_sec: float) -> float:
        """
        The broadcast clock polynomial alone: `af0 + af1*dt + af2*dt^2`.

        Separate from `clock_correction_s` because this is the half that IGS
        precise clock products are directly comparable with.  Neither the broadcast
        parameters nor an SP3 clock includes the **periodic relativistic
        correction** -- both leave it to the receiver, which is why
        `clock_correction_s` adds it and this does not.  Compare a
        relativity-inclusive broadcast clock against SP3 and the difference is
        dominated by a sinusoid at the orbital period, `2*F*e*sqrt(A)*c` peak to
        peak: 17 m on a satellite with e = 0.013, against a true clock error of
        well under a metre.
        """
        dt = _wrap_time_from_epoch(t_gps_sec - self.toc)
        return float(self.af0 + self.af1 * dt + self.af2 * dt * dt)

    def relativistic_correction_s(self, state: OrbitState) -> float:
        """
        The periodic term an eccentric orbit puts on the satellite's clock.

        It is a property of where the satellite is, not of its clock, which is why
        it takes an orbit state rather than a time.
        """
        return float(
            RELATIVISTIC_F * self.e * self.sqrt_a * np.sin(state.eccentric_anomaly_rad)
        )

    def clock_correction_s(self, t_gps_sec: float, state: OrbitState | None = None) -> float:
        """SV clock bias including relativity, excluding group delay."""
        if state is None:
            state = self.orbit_state(t_gps_sec)
        return self.clock_polynomial_s(t_gps_sec) + self.relativistic_correction_s(state)


@dataclass(frozen=True)
class CnavEphemeris:
    """
    Modernised quasi-Keplerian ephemeris -- CNAV (L2C, L5) and CNAV-2 (L1C).

    Three differences from `LnavEphemeris`, and each one exists because the
    modernised messages spend their extra bits on rates:

      * the semi-major axis is `A_REF + delta_a`, drifting at `a_dot`;
      * the mean motion correction drifts too, at `delta_n0_dot`;
      * the node rate is stated relative to `OMEGA_DOT_REF`.

    `top` is carried because messages 10/11 and 30-37 must agree on it for their
    data to belong to the same CEI set, which is the check `is_consistent_with`
    performs.
    """

    sat_id: str
    week: int
    toe: float
    top: float
    delta_a: float  # metres, relative to CNAV_A_REF
    a_dot: float  # metres/s
    delta_n0: float  # semi-circles/s
    delta_n0_dot: float  # semi-circles/s^2
    M0: float  # semi-circles
    e: float
    omega: float  # semi-circles
    Omega0: float  # semi-circles
    delta_Omega_dot: float  # semi-circles/s, relative to CNAV_OMEGA_DOT_REF
    i0: float  # semi-circles
    i_dot: float  # semi-circles/s
    Cis: float
    Cic: float
    Crs: float
    Crc: float
    Cus: float
    Cuc: float
    toc: float = 0.0
    af0: float = 0.0
    af1: float = 0.0
    af2: float = 0.0
    tgd: float = 0.0
    isc_l5i5: float | None = None
    isc_l5q5: float | None = None
    isc_l1ca: float | None = None
    isc_l2c: float | None = None
    health_l1: int = 0
    health_l2: int = 0
    health_l5: int = 0

    @property
    def semi_major_axis_at_toe_m(self) -> float:
        return CNAV_A_REF + self.delta_a

    @property
    def sqrt_a(self) -> float:
        """For the relativistic clock term, which is defined in terms of sqrt(A)."""
        return float(np.sqrt(self.semi_major_axis_at_toe_m))

    @property
    def Omega_dot(self) -> float:
        """Full node rate in semi-circles/s, reference plus broadcast difference."""
        return CNAV_OMEGA_DOT_REF + self.delta_Omega_dot

    def orbit_state(self, t_gps_sec: float) -> OrbitState:
        tk = _wrap_time_from_epoch(t_gps_sec - self.toe)
        A0 = self.semi_major_axis_at_toe_m
        Ak = A0 + self.a_dot * tk
        n0 = np.sqrt(MU_EARTH / (A0 * A0 * A0))
        # The mean motion correction itself drifts: delta_n_A = delta_n0 + 1/2
        # delta_n0_dot * tk.  Dropping the 1/2 is a tempting transcription slip
        # that stays invisible near toe and grows quadratically away from it.
        delta_n = self.delta_n0 + 0.5 * self.delta_n0_dot * tk
        n = n0 + delta_n * SEMICIRCLES_TO_RADIANS
        return _orbit_from_kepler_elements(
            semi_major_axis_m=Ak,
            corrected_mean_motion=n,
            tk=tk,
            M0=self.M0 * SEMICIRCLES_TO_RADIANS,
            e=self.e,
            omega=self.omega * SEMICIRCLES_TO_RADIANS,
            i0=self.i0 * SEMICIRCLES_TO_RADIANS,
            i_dot=self.i_dot * SEMICIRCLES_TO_RADIANS,
            Omega0=self.Omega0 * SEMICIRCLES_TO_RADIANS,
            Omega_dot=self.Omega_dot * SEMICIRCLES_TO_RADIANS,
            toe=self.toe,
            Cus=self.Cus,
            Cuc=self.Cuc,
            Crs=self.Crs,
            Crc=self.Crc,
            Cis=self.Cis,
            Cic=self.Cic,
        )

    def clock_polynomial_s(self, t_gps_sec: float) -> float:
        """The clock polynomial alone -- see `LnavEphemeris.clock_polynomial_s`."""
        dt = _wrap_time_from_epoch(t_gps_sec - self.toc)
        return float(self.af0 + self.af1 * dt + self.af2 * dt * dt)

    def relativistic_correction_s(self, state: OrbitState) -> float:
        """The periodic relativistic term -- see `LnavEphemeris`."""
        return float(
            RELATIVISTIC_F * self.e * self.sqrt_a * np.sin(state.eccentric_anomaly_rad)
        )

    def clock_correction_s(self, t_gps_sec: float, state: OrbitState | None = None) -> float:
        if state is None:
            state = self.orbit_state(t_gps_sec)
        return self.clock_polynomial_s(t_gps_sec) + self.relativistic_correction_s(state)


Ephemeris = LnavEphemeris | CnavEphemeris


def from_rinex(record, sat_id: str) -> LnavEphemeris:
    """
    Adapt a `gnss_tools.rinex_io.rinex_nav.RINEX_LNAVEphemeris` to `LnavEphemeris`.

    RINEX navigation files carry LNAV parameters whatever signal the receiver
    tracked, so a decoded CNAV ephemeris and a downloaded RINEX one are never the
    same *model*.  They are still directly comparable in the position they predict,
    which is what the navigation notebook checks.

    Note `record.a` is already the semi-major axis, not its square root -- the
    RINEX parser squares the broadcast sqrt(A) on the way in.
    """
    return LnavEphemeris(
        sat_id=sat_id,
        week=record.week_num,
        toe=record.toe,
        sqrt_a=float(np.sqrt(record.a)),
        e=record.e,
        i0=record.i0 / SEMICIRCLES_TO_RADIANS,
        i_dot=record.iDot / SEMICIRCLES_TO_RADIANS,
        Omega0=record.Omega0 / SEMICIRCLES_TO_RADIANS,
        Omega_dot=record.OmegaDot / SEMICIRCLES_TO_RADIANS,
        omega=record.omega / SEMICIRCLES_TO_RADIANS,
        M0=record.M0 / SEMICIRCLES_TO_RADIANS,
        deln=record.deln / SEMICIRCLES_TO_RADIANS,
        Cuc=record.Cuc,
        Cus=record.Cus,
        Crc=record.Crc,
        Crs=record.Crs,
        Cic=record.Cic,
        Cis=record.Cis,
        toc=record.toc,
        af0=record.af0,
        af1=record.af1,
        af2=record.af2,
        tgd=record.tgd,
        iode=getattr(record, "iodc", None),
        health=record.sv_health_flag,
    )
