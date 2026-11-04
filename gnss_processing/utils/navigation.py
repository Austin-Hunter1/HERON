"""
The navigation solution: satellite positions, corrected pseudoranges, and the fix.

This module closes the loop that `utils.observables` opens.  Everything here needs
a receiver position -- either to compute (the fix itself) or to depend on
(Sagnac, troposphere, the elevation an ionospheric mapping needs) -- which is why
it is separate from the measurement side.

Three things worth knowing before reading the code:

**Satellite positions need no iteration.**  The usual textbook loop -- guess the
range, get a transit time, evaluate the orbit, repeat -- exists because a receiver
that only measures pseudorange does not know when the signal left.  Here the
transmit time is measured directly, from cumulative code phase anchored by a
decoded time of week.  So the orbit is evaluated once, at the time it was actually
transmitted.

**Sagnac is not optional, and its transit time must not come from the receiver
clock.**  The satellite's ECEF position is computed at transmission, but the fix is
expressed in the frame as it stands at reception, and the earth turns about 30
metres' worth during a 70 ms transit.  The transit that rotation needs is taken
geometrically, from the a-priori position -- `receive_time_s - t_gps` would carry
the receiver clock bias into it, and that bias is milliseconds here, not
microseconds.  Because the error is common to every satellite it is a rigid
rotation of the whole constellation, so it never shows up in the residuals: it
moves the fix by 1-3 m on this data and leaves every diagnostic looking clean.

**`gnss_tools` already has the least-squares solve**, and it is used here rather
than re-implemented.  What this module adds around it is the geometry matrix,
residuals and DOP -- the quantities that say whether a fix should be believed,
which the bare solver does not return.

**More than one constellation** (D-057).  Satellites of different systems share the
receiver position but not the receiver clock as the pseudorange sees it: each system
has its own time scale and its own signal path, so each system after the first gets
one extra bias unknown, the inter-system bias.  GPS plus BeiDou is therefore five
unknowns and needs five satellites.  The system of a satellite is the letter of its id
("G01", "C13").  A set with one system, and no weights, takes the original path
through `gnss_tools`, so its results do not change.  Only when that does not apply
does `_solve_weighted` run: a Gauss-Newton solve with the extra columns and an
optional weight per satellite.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from gnss_tools.coords.utils import ecf2enu, ecf2geo, ecf2sky, geo2ecf, rotate_pos_ecf
from gnss_tools.misc.pvt_estimation import compute_least_squares_position_and_clock

from . import observables as obs
from .nav.ephemeris import OMEGA_E_DOT, SPEED_OF_LIGHT, BdsEphemeris, Ephemeris

# Only ever used to place an initial guess, so the spherical value is ample.
WGS84_MEAN_RADIUS_M = 6371000.0

# A fix farther than this from the earth's surface (above or below) is not a fix
# of a receiver on or near the ground; it is a least-squares iteration that ran
# away -- typically because a pseudorange is wrong by thousands of kilometres.
# `solve_series` does not accept such an epoch. That matters because it seeds
# each epoch with the previous fix: one diverged epoch that was accepted would
# poison the seed of every epoch after it.  (`solve_epoch` itself still reports
# it as converged: the satellite exclusion in `solve_epoch_with_raim` needs the
# runaway solution's residuals to find the satellite that caused it.)
MAX_PLAUSIBLE_ALTITUDE_M = 1.0e6


def is_plausible_fix(solution: "EpochSolution") -> bool:
    """True when the fix is within `MAX_PLAUSIBLE_ALTITUDE_M` of the earth's surface."""
    return bool(
        np.all(np.isfinite(solution.position_ecef_m))
        and abs(np.linalg.norm(solution.position_ecef_m) - WGS84_MEAN_RADIUS_M)
        <= MAX_PLAUSIBLE_ALTITUDE_M
    )


@dataclass(frozen=True)
class WeightMap:
    """
    A weight for each satellite from its C/N0: linear between two corner points,
    flat outside them.

    The defaults are the MATLAB receiver's map (`setWLS.m`: 35 dB-Hz -> 0.1 and
    52 dB-Hz -> 3).  They are values to tune, so a notebook builds its own `WeightMap`
    from its config cell.  A satellite with no C/N0 estimate gets the low weight: not
    trusted, not dropped.
    """

    low_cn0_dbhz: float = 35.0
    high_cn0_dbhz: float = 52.0
    low_weight: float = 0.1
    high_weight: float = 3.0

    def __post_init__(self) -> None:
        if not self.low_cn0_dbhz < self.high_cn0_dbhz:
            raise ValueError("low_cn0_dbhz must be below high_cn0_dbhz")
        if not 0.0 < self.low_weight <= self.high_weight:
            raise ValueError("weights must be positive, with low_weight <= high_weight")

    def __call__(self, cn0_dbhz: np.ndarray) -> np.ndarray:
        cn0 = np.asarray(cn0_dbhz, dtype=float)
        fraction = (cn0 - self.low_cn0_dbhz) / (self.high_cn0_dbhz - self.low_cn0_dbhz)
        weights = self.low_weight + np.clip(fraction, 0.0, 1.0) * (
            self.high_weight - self.low_weight
        )
        return np.where(np.isfinite(cn0), weights, self.low_weight)


GROUP_SEPARATOR = "@"
"""Joins a satellite id to the label of the receiver stream it was measured on."""


def system_letter(sat_id: str) -> str:
    """
    The bias group of a satellite id: its constellation letter ("G", "C", ...), plus
    the stream label when the id has one.

    One receiver clock serves every satellite of one stream, and one inter-system bias
    serves every satellite of one constellation in that stream.  Two radios that were
    never locked together (D-073) have two clocks, so the same constellation seen by
    both is two groups: "G@L1" and "G@L5".  An id without a label, "G05", is in group
    "G", which is the single-stream behaviour of D-057, unchanged.
    """
    letter = sat_id[0]
    if GROUP_SEPARATOR in sat_id:
        return letter + GROUP_SEPARATOR + sat_id.split(GROUP_SEPARATOR, 1)[1]
    return letter


def default_reference_system(groups: list[str]) -> str:
    """
    The group whose clock is the reference: the first GPS group, else the first group.

    Every other group is solved as a bias relative to it.  The choice changes no
    position -- only which group the biases are quoted against.
    """
    for group in groups:
        if group[0] == "G":
            return group
    return groups[0]


@dataclass
class CorrectionSettings:
    """
    Which corrections to apply.  Each is switchable so a notebook can show what it
    is worth -- the satellite clock is tens of kilometres, Sagnac tens of metres,
    the troposphere a few metres, and seeing that spread is most of the lesson.
    """

    satellite_clock: bool = True
    group_delay: bool = True
    sagnac: bool = True
    troposphere: bool = True
    ionosphere: bool = True

    @classmethod
    def none(cls) -> "CorrectionSettings":
        return cls(False, False, False, False, False)


@dataclass
class EpochSolution:
    """One epoch's fix, with everything needed to judge it."""

    position_ecef_m: np.ndarray
    clock_bias_m: float
    residuals_m: np.ndarray
    """Post-fit residuals, one per satellite used.  Metres, not kilometres, when the
    corrections are right."""

    sat_ids: list[str]
    geometry_matrix: np.ndarray
    converged: bool

    inter_system_bias_m: dict[str, float] = field(default_factory=dict)
    """Bias of each system other than the reference, relative to the reference
    system's clock (`clock_bias_m`).  Empty for a one-system fix."""

    fixed_inter_system_biases: tuple[str, ...] = ()
    """Systems whose bias was held at a given value instead of estimated."""

    @property
    def num_satellites(self) -> int:
        return len(self.sat_ids)

    @property
    def num_unknowns(self) -> int:
        """Position (3), the clock, and one bias for each estimated extra system."""
        return 4 + len(self.inter_system_bias_m) - len(self.fixed_inter_system_biases)

    @property
    def is_overdetermined(self) -> bool:
        """
        False when there are exactly as many satellites as unknowns.

        Worth surfacing rather than hiding: with four (five for two systems), the
        residuals are zero by construction and say nothing at all about measurement
        quality.
        """
        return self.num_satellites > self.num_unknowns

    @property
    def dop(self) -> "DilutionOfPrecision":
        return dilution_of_precision(self.geometry_matrix, self.position_ecef_m)


@dataclass
class DilutionOfPrecision:
    gdop: float
    pdop: float
    hdop: float
    vdop: float
    tdop: float


@dataclass
class SolutionSeries:
    """A run of fixes, one per measurement epoch."""

    epoch_uptime_ms: np.ndarray
    receive_time_s: np.ndarray
    position_ecef_m: np.ndarray
    """(N, 3), NaN where no fix was possible."""

    clock_bias_m: np.ndarray
    num_satellites: np.ndarray
    residuals_m: np.ndarray
    """(N, M) post-fit residual per satellite, NaN where unused."""

    sat_ids: list[str]
    dop: dict[str, np.ndarray] = field(default_factory=dict)
    inter_system_bias_m: dict[str, np.ndarray] = field(default_factory=dict)
    """(N,) bias of each non-reference system relative to the reference system, in
    metres, NaN where there is no fix.  Empty when only one system was solved."""

    inter_system_bias_fixed: np.ndarray | None = None
    """(N,) True for an epoch whose inter-system bias was held at the series median
    because too few satellites were in view to estimate it (D-057)."""

    excluded_sat_ids: list[list[str]] = field(default_factory=list)
    """One entry per epoch, same length and order as every other per-epoch
    array on this class (`position_ecef_m`, `clock_bias_m`, ...) -- the
    satellite IDs `solve_series`'s RAIM gate (`residual_gate_m`) dropped
    before accepting that epoch's fix, or `[]` for an epoch where nothing was
    dropped. `solve_clock_series` never excludes anything, but still fills
    this with one `[]` per epoch, for the same reason: so a caller can zip it
    against any other per-epoch field without checking which solver produced
    this series."""

    @property
    def valid(self) -> np.ndarray:
        return np.isfinite(self.position_ecef_m[:, 0])

    def enu_about(self, reference_ecef_m: np.ndarray) -> np.ndarray:
        """(N, 3) east/north/up relative to a reference, NaN where there is no fix."""
        out = np.full_like(self.position_ecef_m, np.nan)
        good = self.valid
        if good.any():
            out[good] = ecf2enu(reference_ecef_m, self.position_ecef_m[good])
        return out

    def clock_drift_ppm(self) -> tuple[float, float]:
        """
        `(drift_ppm, rms_residual_m)` from a straight line through the clock bias.

        The receiver clock here is the sample counter, so its drift relative to GPS
        time is the front end's oscillator error in parts per million -- a real
        measurement of the hardware, not a nuisance parameter.  The residual about
        the line says how much of the trace is not a constant-rate offset.
        """
        good = self.valid & np.isfinite(self.clock_bias_m)
        if good.sum() < 2:
            return float("nan"), float("nan")
        t = self.receive_time_s[good]
        t = t - t[0]
        bias = self.clock_bias_m[good]
        slope, intercept = np.polyfit(t, bias, 1)
        residual = bias - (slope * t + intercept)
        # slope is m/s of range error; divide by c to get a fractional rate.
        return float(slope / SPEED_OF_LIGHT * 1e6), float(np.sqrt(np.mean(residual**2)))


def satellite_positions(
    observables: obs.Observables,
    ephemerides: dict[str, Ephemeris],
    *,
    apply_sagnac: bool = True,
    receiver_position_ecef_m: np.ndarray | None = None,
) -> np.ndarray:
    """
    (N, M, 3) satellite ECEF positions at transmission, NaN where unavailable.

    This is the function that stands in for the broken
    `gnss_tools.misc.rinex_gps_ephemeris.compute_gps_satellite_pvt_from_ephemeris`,
    which calls both of its own helpers with the wrong signatures and cannot run.
    Here the orbit models live on the ephemeris objects themselves
    (`utils.nav.ephemeris`), so LNAV and CNAV parameter sets are interchangeable at
    this level.

    With `apply_sagnac`, each position is rotated forward by the earth's rotation
    over that signal's transit time, expressing it in the ECEF frame as it stood
    when the signal arrived.  About 30 m at a 70 ms transit -- small next to the
    satellite clock but far larger than the fix's own precision.

    `receiver_position_ecef_m` supplies that transit time **geometrically**, as
    `|r_sat - r_rx| / c`, and should be given whenever an a-priori is available.
    Without it the transit falls back to `receive_time_s - t_gps`, which still
    contains the receiver clock bias -- and that bias is not small here.  The
    receiver clock is the sample counter, anchored in `utils.observables` to a
    nominal 75 ms transit, so it starts out wrong by however far the closest
    satellite's real transit is from 75 ms: measured at 7.2 ms on this L5 collect
    and -2.6 ms on L1 C/A.  A transit error rotates the satellite by about
    1.9 m/ms, so the fallback path costs metres, not the centimetres a
    microsecond-scale bias would.

    The geometric route needs no iteration and no clock estimate.  An a-priori good
    to a kilometre gives the transit to about 3 microseconds, worth well under a
    centimetre of rotation -- three orders below the error it removes.
    """
    if receiver_position_ecef_m is not None:
        receiver_position_ecef_m = np.asarray(receiver_position_ecef_m, dtype=float)
    n, m = observables.transmit_time_s.shape
    out = np.full((n, m, 3), np.nan)
    for j, sat_id in enumerate(observables.sat_ids):
        eph = ephemerides.get(sat_id)
        if eph is None:
            continue
        for i in range(n):
            t_tx = observables.transmit_time_s[i, j]
            if not np.isfinite(t_tx):
                continue
            # `t_tx` is SV time -- what the satellite's own clock stamped on the
            # signal.  The orbit is a function of GPS time, and the two differ by
            # the satellite clock error, which can exceed a hundred microseconds.
            # At 3.9 km/s that is half a metre of satellite position, so the
            # correction is applied before the orbit is evaluated, not after.
            #
            # The clock polynomial itself is evaluated at SV time.  It varies over
            # hours, so the sub-millisecond difference between the two arguments is
            # utterly negligible there -- the loop that some receivers run is not
            # worth its complexity.
            #
            # The ephemeris is a function of its own system's time, so `t_tx` (in the
            # set's scale) is first moved into that: 14 s for BeiDou in a GPST set.
            t_native = obs.ephemeris_time_s(eph, t_tx, observables.time_system)
            clock_s = eph.clock_correction_s(t_native)
            t_gps = t_tx - clock_s  # in the set's scale; used for the Sagnac transit
            position = eph.orbit_state(t_native - clock_s).position_ecef_m
            if apply_sagnac:
                if receiver_position_ecef_m is None:
                    # Fallback: carries the receiver clock bias with it.  See the
                    # docstring -- on this data that is milliseconds, so metres.
                    transit = observables.receive_time_s[i] - t_gps
                else:
                    # Geometry instead of the receiver clock, so no clock bias
                    # enters.  The un-rotated position is the right one to measure
                    # from: the rotation is what is being solved for here, and it
                    # changes the range by far less than the metre or so of
                    # a-priori error already tolerated.
                    transit = float(
                        np.linalg.norm(position - receiver_position_ecef_m)
                        / SPEED_OF_LIGHT
                    )
                # NOTE the sign.  We need the transmit-time position expressed in
                # the ECEF frame as it stood at RECEPTION, and that frame has
                # rotated by +Omega*tau in the meantime -- so the coordinates
                # transform by R_z(-Omega*tau).  `rotate_pos_ecf(p, tau)` applies
                # R_z(+Omega*tau), hence the negated transit time.  Getting this
                # backwards does not fail; it doubles a ~30 m error into ~60 m and
                # leaves it looking like a clock problem.
                position = np.atleast_2d(
                    rotate_pos_ecf(position, -transit, OMEGA_E_DOT)
                )[0]
            out[i, j] = position
    return out


def correct_pseudoranges(
    observables: obs.Observables,
    ephemerides: dict[str, Ephemeris],
    *,
    signal_type_id: str,
    settings: CorrectionSettings | None = None,
    receiver_position_ecef_m: np.ndarray | None = None,
    iono_alpha: tuple[float, float, float, float] | None = None,
    iono_beta: tuple[float, float, float, float] | None = None,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """
    Corrected pseudoranges, plus each correction term separately.

    Returning the breakdown alongside the total is the point: a notebook that only
    prints a corrected pseudorange teaches nothing, while one that can show the
    satellite clock is 30 km and the troposphere is 3 m makes the hierarchy
    obvious.

    `receiver_position_ecef_m` is needed only for the atmosphere, which depends on
    elevation.  An approximate position is entirely adequate there -- a kilometre
    of position error moves the tropospheric delay by well under a millimetre --
    so the a-priori is used and no iteration is required.
    """
    settings = settings or CorrectionSettings()
    terms: dict[str, np.ndarray] = {}
    corrected = observables.pseudorange_m.copy()

    if settings.satellite_clock:
        clock_s = obs.satellite_clock_corrections(
            observables,
            ephemerides,
            signal_type_id=signal_type_id,  # per-satellite types in the set override it
            apply_group_delay=settings.group_delay,
        )
        # t = t_sv - dt_sv, so the true transmit time is earlier and the true range
        # longer: the correction adds.
        term = SPEED_OF_LIGHT * clock_s
        terms["satellite_clock"] = term
        corrected = corrected + np.nan_to_num(term, nan=0.0)

    needs_geometry = settings.troposphere or settings.ionosphere
    if needs_geometry and receiver_position_ecef_m is not None:
        positions = satellite_positions(
            observables,
            ephemerides,
            apply_sagnac=settings.sagnac,
            receiver_position_ecef_m=receiver_position_ecef_m,
        )
        # gnss_tools orders geodetic coordinates (LON, LAT, ALT) -- see
        # `gnss_tools.coords.utils.ecf2geo`.  Reading it as (lat, lon) puts the
        # receiver in the wrong hemisphere and quietly wrecks every elevation.
        geodetic = np.atleast_2d(ecf2geo(np.atleast_2d(receiver_position_ecef_m)))[0]
        longitude_deg, latitude_deg, height_m = geodetic

        n, m = observables.pseudorange_m.shape
        elevation = np.full((n, m), np.nan)
        azimuth = np.full((n, m), np.nan)
        for i in range(n):
            finite = np.isfinite(positions[i, :, 0])
            if not finite.any():
                continue
            sky = ecf2sky(np.atleast_2d(receiver_position_ecef_m), positions[i, finite])
            azimuth[i, finite] = sky[:, 0]
            elevation[i, finite] = sky[:, 1]
        terms["elevation_deg"] = elevation
        terms["azimuth_deg"] = azimuth

        if settings.troposphere:
            tropo = obs.saastamoinen_delay_m(
                np.deg2rad(elevation),
                height_m=float(height_m),
                latitude_rad=float(np.deg2rad(latitude_deg)),
            )
            terms["troposphere"] = -tropo
            corrected = corrected - np.nan_to_num(tropo, nan=0.0)

        if settings.ionosphere and iono_alpha is not None and iono_beta is not None:
            # Klobuchar is GPS's model, in GPS time.  BeiDou satellites get no
            # ionosphere correction (D-053), and each GPS satellite is scaled to the
            # frequency of its own signal.
            gps_time = obs.time_systems.convert_time_of_week(
                observables.receive_time_s, observables.time_system, "GPST"
            )
            iono = np.zeros((n, m))
            for j, sat_id in enumerate(observables.sat_ids):
                if isinstance(ephemerides.get(sat_id), BdsEphemeris):
                    continue
                sat_signal = observables.signal_type_ids.get(sat_id, signal_type_id)
                delay_s = obs.klobuchar_delay_s(
                    iono_alpha,
                    iono_beta,
                    latitude_semicircles=float(latitude_deg) / 180.0,
                    longitude_semicircles=float(longitude_deg) / 180.0,
                    elevation_semicircles=elevation[:, j] / 180.0,
                    azimuth_semicircles=azimuth[:, j] / 180.0,
                    gps_time_of_week_s=gps_time,
                    frequency_hz=obs.BAND_FREQ_HZ.get(sat_signal, obs.L1_FREQ_HZ),
                )
                iono[:, j] = SPEED_OF_LIGHT * delay_s
            terms["ionosphere"] = -iono
            corrected = corrected - np.nan_to_num(iono, nan=0.0)

    return corrected, terms


def dilution_of_precision(
    geometry_matrix: np.ndarray, position_ecef_m: np.ndarray
) -> DilutionOfPrecision:
    """
    DOP from the geometry matrix, with the horizontal/vertical split in local ENU.

    GDOP alone hides the thing that usually matters: with satellites clustered
    overhead, HDOP can be fine while VDOP is dreadful, and a fix quoted as "good"
    on GDOP is then wrong mostly in height.
    """
    try:
        cofactor = np.linalg.inv(geometry_matrix.T @ geometry_matrix)
    except np.linalg.LinAlgError:
        nan = float("nan")
        return DilutionOfPrecision(nan, nan, nan, nan, nan)

    gdop = float(np.sqrt(np.trace(cofactor)))
    pdop = float(np.sqrt(np.trace(cofactor[:3, :3])))
    tdop = float(np.sqrt(cofactor[3, 3]))

    # Rotate the position block into local east/north/up.  (lon, lat, alt) order.
    geodetic = np.atleast_2d(ecf2geo(np.atleast_2d(position_ecef_m)))[0]
    lon, lat = np.deg2rad(geodetic[0]), np.deg2rad(geodetic[1])
    rotation = np.array(
        [
            [-np.sin(lon), np.cos(lon), 0.0],
            [-np.sin(lat) * np.cos(lon), -np.sin(lat) * np.sin(lon), np.cos(lat)],
            [np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)],
        ]
    )
    local = rotation @ cofactor[:3, :3] @ rotation.T
    hdop = float(np.sqrt(local[0, 0] + local[1, 1]))
    vdop = float(np.sqrt(local[2, 2]))
    return DilutionOfPrecision(gdop, pdop, hdop, vdop, tdop)


def _initial_position(positions: np.ndarray, initial_position: np.ndarray | None) -> np.ndarray:
    """
    A starting point for Newton's method.  Never the earth's centre.

    The geometry matrix there is built from directions that all point outward from
    a single point, and the solver returns NaN rather than converging.  The centroid
    of the satellite directions, dropped onto the earth's surface, is a far better
    guess and costs nothing -- it is within a few thousand kilometres of any receiver
    that can see them.  It is also what keeps a surveyed antenna position out of the
    solution: the fix never needs to be told where the antenna is.
    """
    if initial_position is not None:
        return np.asarray(initial_position, dtype=float)
    centroid = positions.mean(axis=0)
    norm = np.linalg.norm(centroid)
    return centroid / norm * WGS84_MEAN_RADIUS_M if norm > 0 else np.zeros(3)


def _solve_weighted(
    ranges: np.ndarray,
    positions: np.ndarray,
    system_index: np.ndarray,
    num_systems: int,
    weights: np.ndarray | None,
    initial_position: np.ndarray,
    initial_clock_bias: float,
    *,
    max_iterations: int = 20,
    tolerance_m: float = 1.0e-4,
) -> tuple[np.ndarray, float, np.ndarray, np.ndarray] | None:
    """
    Gauss-Newton position, clock and inter-system biases, with optional weights.

    The unknowns are the position (3), the reference system's clock, and one bias for
    each other system.  Each pseudorange row is the geometric range plus the clock
    plus, for a satellite of another system, that system's bias.  With weights `w`
    the minimised sum is `sum(w * residual^2)`.

    Returns `(position, clock, biases, geometry)` or None if the iteration fails.
    `biases` has `num_systems - 1` entries, in system order.
    """
    n = len(ranges)
    position = np.array(initial_position, dtype=float)
    # Start the clocks where the data says they are, at the starting position, so
    # that a clock offset of thousands of kilometres does not look like geometry.
    geometric = np.linalg.norm(positions - position, axis=1)
    offsets = ranges - geometric
    clock = float(np.mean(offsets[system_index == 0])) if np.any(system_index == 0) else float(initial_clock_bias)
    biases = np.zeros(num_systems - 1)
    for k in range(1, num_systems):
        mask = system_index == k
        biases[k - 1] = float(np.mean(offsets[mask])) - clock if mask.any() else 0.0
    root_weights = np.ones(n) if weights is None else np.sqrt(np.asarray(weights, dtype=float))

    for _ in range(max_iterations):
        delta = positions - position
        geometric = np.linalg.norm(delta, axis=1)
        design = np.zeros((n, 4 + num_systems - 1))
        design[:, :3] = -delta / geometric[:, None]
        design[:, 3] = 1.0
        for k in range(1, num_systems):
            design[system_index == k, 3 + k] = 1.0
        # Row r's bias: none for the reference system, else that system's.
        predicted = geometric + clock + np.concatenate(([0.0], biases))[system_index]
        residual = ranges - predicted
        step, *_ = np.linalg.lstsq(design * root_weights[:, None], residual * root_weights, rcond=None)
        if not np.all(np.isfinite(step)):
            return None
        position = position + step[:3]
        clock += float(step[3])
        biases = biases + step[4:]
        if np.linalg.norm(step[:3]) < tolerance_m and abs(step[3]) < tolerance_m:
            break
    delta = positions - position
    geometric = np.linalg.norm(delta, axis=1)
    geometry = np.zeros((n, 4 + num_systems - 1))
    geometry[:, :3] = -delta / geometric[:, None]
    geometry[:, 3] = 1.0
    for k in range(1, num_systems):
        geometry[system_index == k, 3 + k] = 1.0
    return position, clock, biases, geometry


def solve_epoch(
    corrected_pseudoranges: np.ndarray,
    satellite_positions_ecef_m: np.ndarray,
    sat_ids: list[str],
    *,
    initial_position: np.ndarray | None = None,
    initial_clock_bias: float = 0.0,
    weights: np.ndarray | None = None,
    fixed_inter_system_bias_m: dict[str, float] | None = None,
    reference_system: str | None = None,
) -> EpochSolution | None:
    """
    One epoch's least-squares position and clock, or None if it cannot be solved.

    Returns None rather than a NaN-filled solution when there are fewer satellites
    than unknowns -- four for one system, five for two: an under-determined "fix" is
    not a degraded answer but a meaningless one.

    With several systems (the first letter of each satellite id) each system after the
    reference gets a bias unknown.  `fixed_inter_system_bias_m` holds a system's bias
    at a given value instead (its pseudoranges are corrected and it joins the
    reference clock), which is how an epoch with too few satellites is still solved
    (D-057).  `reference_system` defaults to "G" if GPS is present, else the first
    system.  `weights` is one per satellite (see `WeightMap`); with none, and one
    system, the original `gnss_tools` solve runs and gives the same numbers as before.
    """
    usable = np.isfinite(corrected_pseudoranges) & np.isfinite(
        satellite_positions_ecef_m[:, 0]
    )
    if usable.sum() < 4:
        return None

    ranges = np.ascontiguousarray(corrected_pseudoranges[usable], dtype=float)
    positions = np.ascontiguousarray(satellite_positions_ecef_m[usable], dtype=float)
    used_ids = [sat_ids[i] for i in np.nonzero(usable)[0]]
    used_weights = None if weights is None else np.asarray(weights, dtype=float)[usable]

    letters = [system_letter(sid) for sid in used_ids]
    present = list(dict.fromkeys(letters))
    if reference_system is None:
        reference_system = default_reference_system(present)
    ordered = [reference_system] + [x for x in present if x != reference_system]
    if reference_system not in present:
        ordered = present
        reference_system = present[0]

    # Systems with a given bias are folded into the reference clock first.
    fixed = dict(fixed_inter_system_bias_m or {})
    fixed_here = tuple(x for x in ordered if x != reference_system and x in fixed)
    for letter in fixed_here:
        mask = np.array([x == letter for x in letters])
        ranges[mask] = ranges[mask] - fixed[letter]
    estimated = [x for x in ordered if x == reference_system or x not in fixed_here]
    system_index = np.array([estimated.index(x) if x in estimated else 0 for x in letters])
    num_systems = len(estimated)

    if len(used_ids) < 3 + num_systems:
        return None

    initial_position = _initial_position(positions, initial_position)

    use_original_path = num_systems == 1 and used_weights is None
    if use_original_path:
        x_hat, b_hat = compute_least_squares_position_and_clock(
            ranges,
            positions,
            initial_position=np.asarray(initial_position, dtype=float),
            initial_clock_bias=float(initial_clock_bias),
        )
        x_hat = np.asarray(x_hat, dtype=float)
        solved = (x_hat, float(b_hat), np.zeros(0), None) if (
            np.all(np.isfinite(x_hat)) and np.isfinite(b_hat)
        ) else None
    else:
        solved = _solve_weighted(
            ranges, positions, system_index, num_systems, used_weights,
            initial_position, initial_clock_bias,
        )
        if solved is not None and not (
            np.all(np.isfinite(solved[0])) and np.isfinite(solved[1])
        ):
            solved = None

    extra_ids = tuple(x for x in estimated if x != reference_system)
    if solved is None:
        return EpochSolution(
            position_ecef_m=np.full(3, np.nan),
            clock_bias_m=float("nan"),
            residuals_m=np.full(len(used_ids), np.nan),
            sat_ids=used_ids,
            geometry_matrix=np.zeros((len(used_ids), 4 + len(extra_ids))),
            converged=False,
            inter_system_bias_m={x: float("nan") for x in (*extra_ids, *fixed_here)},
            fixed_inter_system_biases=fixed_here,
        )

    x_hat, b_hat, biases, geometry = solved
    delta = positions - x_hat
    geometric_range = np.linalg.norm(delta, axis=1)
    if geometry is None:
        geometry = np.column_stack([-delta / geometric_range[:, None], np.ones(len(used_ids))])
    residuals = ranges - geometric_range - b_hat - np.concatenate(([0.0], biases))[system_index]

    bias_report = {x: float(biases[k]) for k, x in enumerate(extra_ids)}
    bias_report.update({x: float(fixed[x]) for x in fixed_here})
    return EpochSolution(
        position_ecef_m=x_hat,
        clock_bias_m=float(b_hat),
        residuals_m=residuals,
        sat_ids=used_ids,
        geometry_matrix=geometry,
        converged=True,
        inter_system_bias_m=bias_report,
        fixed_inter_system_biases=fixed_here,
    )


def solve_epoch_with_raim(
    corrected_pseudoranges: np.ndarray,
    satellite_positions_ecef_m: np.ndarray,
    sat_ids: list[str],
    *,
    initial_position: np.ndarray | None = None,
    initial_clock_bias: float = 0.0,
    residual_gate_m: float = 100.0,
    max_exclusions: int = 3,
    weights: np.ndarray | None = None,
    fixed_inter_system_bias_m: dict[str, float] | None = None,
    reference_system: str | None = None,
) -> tuple[EpochSolution | None, list[str]]:
    """
    `solve_epoch`, plus iterative post-fit residual screening (receiver
    autonomous integrity monitoring, RAIM): a satellite whose residual is
    still large after the fit converges is the single most likely one to be
    wrong -- a reacquisition that seeded an inconsistent cumulative code
    phase (see `utils/tracking_supervisor.py`), a bad ephemeris, undetected
    multipath -- and leaving it in drags every other satellite's residual,
    and the fix itself, off with it.

    Drops the satellite with the largest `|residual|` and re-solves, up to
    `max_exclusions` times, stopping as soon as the largest residual clears
    `residual_gate_m` or no satellite beyond the number of unknowns remains (so an
    exclusion never leaves the fit exactly determined -- zero residuals by
    construction and nothing left to screen).

    Returns `(solution, excluded_sat_ids)` -- `excluded_sat_ids` is `[]`
    when nothing was dropped, including when `solution` is `None` or did
    not converge: a non-converged epoch has all-NaN residuals, and
    `NaN > residual_gate_m` is `False`, so this never fires on one. That is
    deliberate, not a gap to close here -- this catches a fit that
    converged to a confident, wrong answer because of one bad input, not
    one that failed to converge at all.
    """
    ids = list(sat_ids)
    ranges = np.asarray(corrected_pseudoranges, dtype=float)
    positions = np.asarray(satellite_positions_ecef_m, dtype=float)
    weight_array = None if weights is None else np.asarray(weights, dtype=float)
    position_seed = initial_position
    bias_seed = initial_clock_bias
    options = dict(fixed_inter_system_bias_m=fixed_inter_system_bias_m, reference_system=reference_system)

    solution = solve_epoch(
        ranges, positions, ids,
        initial_position=position_seed, initial_clock_bias=bias_seed,
        weights=weight_array, **options,
    )
    excluded: list[str] = []
    for _ in range(max_exclusions):
        if (
            solution is None
            or not solution.converged
            or len(solution.sat_ids) <= solution.num_unknowns
        ):
            break
        abs_residuals = np.abs(solution.residuals_m)
        worst = int(np.argmax(abs_residuals))
        if abs_residuals[worst] <= residual_gate_m:
            break
        worst_id = solution.sat_ids[worst]
        excluded.append(worst_id)
        keep = [i for i, sid in enumerate(ids) if sid != worst_id]
        ids = [ids[i] for i in keep]
        ranges = ranges[keep]
        positions = positions[keep]
        if weight_array is not None:
            weight_array = weight_array[keep]
        position_seed, bias_seed = solution.position_ecef_m, solution.clock_bias_m
        solution = solve_epoch(
            ranges, positions, ids,
            initial_position=position_seed, initial_clock_bias=bias_seed,
            weights=weight_array, **options,
        )
    return solution, excluded


def solve_series(
    observables: obs.Observables,
    ephemerides: dict[str, Ephemeris],
    *,
    signal_type_id: str,
    settings: CorrectionSettings | None = None,
    initial_position_ecef_m: np.ndarray | None = None,
    iono_alpha: tuple[float, float, float, float] | None = None,
    iono_beta: tuple[float, float, float, float] | None = None,
    residual_gate_m: float | None = None,
    max_exclusions: int = 3,
    weights: np.ndarray | None = None,
    fixed_inter_system_bias_m: dict[str, float] | None = None,
    reference_system: str | None = None,
) -> SolutionSeries:
    """
    Solve every measurement epoch, seeding each fix with the previous one.

    Seeding matters less for convergence than for speed -- the solver is Newton's
    method and converges from the earth's centre in a handful of iterations -- but
    it also keeps the series from wandering between equivalent solutions on a
    marginal epoch.  The seed of the first epoch is `initial_position_ecef_m` if
    given, else the satellite centroid: a surveyed antenna position is never needed
    and should not be passed in to make a fix look better.

    `residual_gate_m`, when set, routes each epoch through
    `solve_epoch_with_raim` instead of bare `solve_epoch` -- see that
    function. Left at its default `None`, this function's behaviour is
    exactly what it was before that gate existed; this default is
    deliberate, not a placeholder for "should be on" -- a caller opts in
    explicitly (the reference figure is 100.0 m).

    More than one system (D-057).  Each system after the reference gets a bias
    estimated every epoch.  An epoch with at least four satellites but too few to
    estimate the bias (four satellites, two systems) is solved afterwards with the
    bias held at the median of the epochs that did estimate it, and is marked in
    `inter_system_bias_fixed`.  `fixed_inter_system_bias_m` holds a bias at a given
    value in every epoch instead.  `weights` is `(N, M)`, one per satellite and
    epoch (see `WeightMap`); `None` is equal weights.
    """
    settings = settings or CorrectionSettings()
    corrected, _ = correct_pseudoranges(
        observables,
        ephemerides,
        signal_type_id=signal_type_id,
        settings=settings,
        receiver_position_ecef_m=initial_position_ecef_m,
        iono_alpha=iono_alpha,
        iono_beta=iono_beta,
    )
    positions = satellite_positions(
        observables,
        ephemerides,
        apply_sagnac=settings.sagnac,
        receiver_position_ecef_m=initial_position_ecef_m,
    )

    n, m = corrected.shape
    if weights is not None and np.shape(weights) != (n, m):
        raise ValueError(f"weights must be shaped {(n, m)}, got {np.shape(weights)}")
    out_position = np.full((n, 3), np.nan)
    out_bias = np.full(n, np.nan)
    out_count = np.zeros(n, dtype=int)
    out_residuals = np.full((n, m), np.nan)
    out_excluded: list[list[str]] = [[] for _ in range(n)]
    out_isb: dict[str, np.ndarray] = {}
    out_isb_fixed = np.zeros(n, dtype=bool)
    dop_names = ("gdop", "pdop", "hdop", "vdop", "tdop")
    dop = {name: np.full(n, np.nan) for name in dop_names}

    def solve_one(i, seed, seed_bias, fixed):
        row_weights = None if weights is None else np.asarray(weights)[i]
        options = dict(
            initial_position=seed,
            initial_clock_bias=seed_bias,
            weights=row_weights,
            fixed_inter_system_bias_m=fixed,
            reference_system=reference_system,
        )
        if residual_gate_m is None:
            return solve_epoch(corrected[i], positions[i], observables.sat_ids, **options), []
        return solve_epoch_with_raim(
            corrected[i], positions[i], observables.sat_ids,
            residual_gate_m=residual_gate_m, max_exclusions=max_exclusions, **options,
        )

    def record(i, solution):
        out_position[i] = solution.position_ecef_m
        out_bias[i] = solution.clock_bias_m
        out_count[i] = solution.num_satellites
        for sat_id, residual in zip(solution.sat_ids, solution.residuals_m):
            out_residuals[i, observables.sat_ids.index(sat_id)] = residual
        values = solution.dop
        for name in dop_names:
            dop[name][i] = getattr(values, name)
        for letter, value in solution.inter_system_bias_m.items():
            out_isb.setdefault(letter, np.full(n, np.nan))[i] = value
        out_isb_fixed[i] = bool(solution.fixed_inter_system_biases)

    seed = initial_position_ecef_m
    seed_bias = 0.0
    unsolved: list[int] = []
    for i in range(n):
        solution, out_excluded[i] = solve_one(i, seed, seed_bias, fixed_inter_system_bias_m)
        if solution is None or not solution.converged or not is_plausible_fix(solution):
            unsolved.append(i)
            continue
        record(i, solution)
        seed, seed_bias = solution.position_ecef_m, solution.clock_bias_m

    # Second pass: epochs with the satellites for a fix but not for the bias.
    if unsolved and out_isb and fixed_inter_system_bias_m is None:
        held = {
            letter: float(np.nanmedian(values[~out_isb_fixed]))
            for letter, values in out_isb.items()
            if np.isfinite(values[~out_isb_fixed]).any()
        }
        for i in unsolved:
            solution, out_excluded[i] = solve_one(i, None, 0.0, held)
            if solution is None or not solution.converged or not is_plausible_fix(solution):
                continue
            record(i, solution)

    return SolutionSeries(
        epoch_uptime_ms=observables.epoch_uptime_ms,
        receive_time_s=observables.receive_time_s,
        position_ecef_m=out_position,
        clock_bias_m=out_bias,
        num_satellites=out_count,
        residuals_m=out_residuals,
        sat_ids=list(observables.sat_ids),
        dop=dop,
        excluded_sat_ids=out_excluded,
        inter_system_bias_m=out_isb,
        inter_system_bias_fixed=out_isb_fixed if out_isb else None,
    )


def solve_clock_series(
    observables: obs.Observables,
    ephemerides: dict[str, Ephemeris],
    position_ecef_m: np.ndarray,
    *,
    signal_type_id: str,
    settings: CorrectionSettings | None = None,
    iono_alpha: tuple[float, float, float, float] | None = None,
    iono_beta: tuple[float, float, float, float] | None = None,
) -> SolutionSeries:
    """
    Solve only the receiver clock, holding the antenna position fixed.

    This is not a fallback invented for a short satellite count -- it is how a
    timing receiver at a surveyed site normally runs.  Fixing the position removes
    three of the four unknowns, so **one** satellite is enough for a clock
    solution and each additional one is pure redundancy.

    That redundancy is what makes this worth having even when a full fix is
    possible: with the position held, the post-fit residuals are no longer forced
    to zero by the geometry.  With four satellites and four unknowns a position fix
    has zero residuals by construction and says nothing about measurement quality;
    the same four satellites against a known position give three degrees of freedom
    and a genuine diagnostic.

    The estimate is the mean offset between corrected pseudorange and geometric
    range -- the least-squares solution when the only unknown is a common bias.
    """
    settings = settings or CorrectionSettings()
    position_ecef_m = np.asarray(position_ecef_m, dtype=float)

    corrected, _ = correct_pseudoranges(
        observables,
        ephemerides,
        signal_type_id=signal_type_id,
        settings=settings,
        receiver_position_ecef_m=position_ecef_m,
        iono_alpha=iono_alpha,
        iono_beta=iono_beta,
    )
    positions = satellite_positions(
        observables,
        ephemerides,
        apply_sagnac=settings.sagnac,
        # Held, not a-priori -- so the Sagnac transit here is as good as it gets.
        receiver_position_ecef_m=position_ecef_m,
    )

    n, m = corrected.shape
    out_position = np.tile(position_ecef_m, (n, 1))
    out_bias = np.full(n, np.nan)
    out_count = np.zeros(n, dtype=int)
    out_residuals = np.full((n, m), np.nan)

    for i in range(n):
        usable = np.isfinite(corrected[i]) & np.isfinite(positions[i, :, 0])
        if not usable.any():
            out_position[i] = np.nan
            continue
        geometric = np.linalg.norm(positions[i, usable] - position_ecef_m, axis=1)
        offsets = corrected[i, usable] - geometric
        bias = float(np.mean(offsets))
        out_bias[i] = bias
        out_count[i] = int(usable.sum())
        out_residuals[i, usable] = offsets - bias

    return SolutionSeries(
        epoch_uptime_ms=observables.epoch_uptime_ms,
        receive_time_s=observables.receive_time_s,
        position_ecef_m=out_position,
        clock_bias_m=out_bias,
        excluded_sat_ids=[[] for _ in range(n)],
        num_satellites=out_count,
        residuals_m=out_residuals,
        sat_ids=list(observables.sat_ids),
        dop={},
    )


def sky_positions(
    receiver_position_ecef_m: np.ndarray,
    satellite_positions_ecef_m: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """
    `(azimuth_deg, elevation_deg)` for a set of satellite positions.

    A thin wrapper over `gnss_tools.coords.utils.ecf2sky`, here so the skyplot and
    the atmospheric corrections read the geometry the same way.
    """
    sky = ecf2sky(
        np.atleast_2d(receiver_position_ecef_m), np.atleast_2d(satellite_positions_ecef_m)
    )
    return sky[:, 0], sky[:, 1]


def geodetic_to_ecef(latitude_deg: float, longitude_deg: float, height_m: float) -> np.ndarray:
    """
    WGS 84 geodetic to ECEF, as a plain (3,) array.

    Takes latitude first, because that is how everyone says it and how the notebook
    writes the reference site.  `gnss_tools.coords.utils.geo2ecf` wants
    (lon, lat, alt), so the swap happens here -- once, visibly -- rather than at
    every call site.
    """
    return np.atleast_2d(geo2ecf(np.array([[longitude_deg, latitude_deg, height_m]])))[0]


def ecef_to_geodetic(position_ecef_m: np.ndarray) -> tuple[float, float, float]:
    """
    ECEF to `(latitude_deg, longitude_deg, height_m)` -- latitude first.

    The inverse of `geodetic_to_ecef`, and the other half of insulating callers
    from gnss_tools' (lon, lat, alt) convention.
    """
    geodetic = np.atleast_2d(ecf2geo(np.atleast_2d(position_ecef_m)))[0]
    return float(geodetic[1]), float(geodetic[0]), float(geodetic[2])
