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
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from gnss_tools.coords.utils import ecf2enu, ecf2geo, ecf2sky, geo2ecf, rotate_pos_ecf
from gnss_tools.misc.pvt_estimation import compute_least_squares_position_and_clock

from . import observables as obs
from .nav.ephemeris import OMEGA_E_DOT, SPEED_OF_LIGHT, Ephemeris

# Only ever used to place an initial guess, so the spherical value is ample.
WGS84_MEAN_RADIUS_M = 6371000.0


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

    @property
    def num_satellites(self) -> int:
        return len(self.sat_ids)

    @property
    def is_overdetermined(self) -> bool:
        """
        False when there are exactly four satellites.

        Worth surfacing rather than hiding: with four, the residuals are zero by
        construction and say nothing at all about measurement quality.
        """
        return self.num_satellites > 4

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
            t_gps = t_tx - eph.clock_correction_s(t_tx)
            position = eph.orbit_state(t_gps).position_ecef_m
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
            signal_type_id=signal_type_id,
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
            frequency = obs.BAND_FREQ_HZ.get(signal_type_id, obs.L1_FREQ_HZ)
            delay_s = obs.klobuchar_delay_s(
                iono_alpha,
                iono_beta,
                latitude_semicircles=float(latitude_deg) / 180.0,
                longitude_semicircles=float(longitude_deg) / 180.0,
                elevation_semicircles=elevation / 180.0,
                azimuth_semicircles=azimuth / 180.0,
                gps_time_of_week_s=observables.receive_time_s[:, None],
                frequency_hz=frequency,
            )
            iono = SPEED_OF_LIGHT * delay_s
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


def solve_epoch(
    corrected_pseudoranges: np.ndarray,
    satellite_positions_ecef_m: np.ndarray,
    sat_ids: list[str],
    *,
    initial_position: np.ndarray | None = None,
    initial_clock_bias: float = 0.0,
) -> EpochSolution | None:
    """
    One epoch's least-squares position and clock, or None if it cannot be solved.

    Returns None rather than a NaN-filled solution when fewer than four satellites
    are available: four unknowns need four equations, and an under-determined
    "fix" is not a degraded answer but a meaningless one.
    """
    usable = np.isfinite(corrected_pseudoranges) & np.isfinite(
        satellite_positions_ecef_m[:, 0]
    )
    if usable.sum() < 4:
        return None

    ranges = np.ascontiguousarray(corrected_pseudoranges[usable], dtype=float)
    positions = np.ascontiguousarray(satellite_positions_ecef_m[usable], dtype=float)
    used_ids = [sat_ids[i] for i in np.nonzero(usable)[0]]

    if initial_position is None:
        # Never start Newton's method at the earth's centre.  The geometry matrix
        # there is built from directions that all point outward from a single
        # point, and `compute_least_squares_position_and_clock` returns NaN rather
        # than converging.  The centroid of the satellite directions, dropped onto
        # the earth's surface, is a far better guess and costs nothing -- it is
        # within a few thousand kilometres of any receiver that can see them.
        centroid = positions.mean(axis=0)
        norm = np.linalg.norm(centroid)
        initial_position = (
            centroid / norm * WGS84_MEAN_RADIUS_M if norm > 0 else np.zeros(3)
        )

    x_hat, b_hat = compute_least_squares_position_and_clock(
        ranges,
        positions,
        initial_position=(
            None if initial_position is None else np.asarray(initial_position, dtype=float)
        ),
        initial_clock_bias=float(initial_clock_bias),
    )
    x_hat = np.asarray(x_hat, dtype=float)
    converged = bool(np.all(np.isfinite(x_hat)) and np.isfinite(b_hat))
    if not converged:
        return EpochSolution(
            position_ecef_m=np.full(3, np.nan),
            clock_bias_m=float("nan"),
            residuals_m=np.full(len(used_ids), np.nan),
            sat_ids=used_ids,
            geometry_matrix=np.zeros((len(used_ids), 4)),
            converged=False,
        )

    delta = positions - x_hat
    geometric_range = np.linalg.norm(delta, axis=1)
    geometry = np.column_stack([-delta / geometric_range[:, None], np.ones(len(used_ids))])
    residuals = ranges - geometric_range - b_hat

    return EpochSolution(
        position_ecef_m=x_hat,
        clock_bias_m=float(b_hat),
        residuals_m=residuals,
        sat_ids=used_ids,
        geometry_matrix=geometry,
        converged=True,
    )


def solve_series(
    observables: obs.Observables,
    ephemerides: dict[str, Ephemeris],
    *,
    signal_type_id: str,
    settings: CorrectionSettings | None = None,
    initial_position_ecef_m: np.ndarray | None = None,
    iono_alpha: tuple[float, float, float, float] | None = None,
    iono_beta: tuple[float, float, float, float] | None = None,
) -> SolutionSeries:
    """
    Solve every measurement epoch, seeding each fix with the previous one.

    Seeding matters less for convergence than for speed -- the solver is Newton's
    method and converges from the earth's centre in a handful of iterations -- but
    it also keeps the series from wandering between equivalent solutions on a
    marginal epoch.
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
    out_position = np.full((n, 3), np.nan)
    out_bias = np.full(n, np.nan)
    out_count = np.zeros(n, dtype=int)
    out_residuals = np.full((n, m), np.nan)
    dop_names = ("gdop", "pdop", "hdop", "vdop", "tdop")
    dop = {name: np.full(n, np.nan) for name in dop_names}

    seed = initial_position_ecef_m
    seed_bias = 0.0
    for i in range(n):
        solution = solve_epoch(
            corrected[i],
            positions[i],
            observables.sat_ids,
            initial_position=seed,
            initial_clock_bias=seed_bias,
        )
        if solution is None or not solution.converged:
            continue
        out_position[i] = solution.position_ecef_m
        out_bias[i] = solution.clock_bias_m
        out_count[i] = solution.num_satellites
        for sat_id, residual in zip(solution.sat_ids, solution.residuals_m):
            out_residuals[i, observables.sat_ids.index(sat_id)] = residual
        values = solution.dop
        for name in dop_names:
            dop[name][i] = getattr(values, name)
        seed, seed_bias = solution.position_ecef_m, solution.clock_bias_m

    return SolutionSeries(
        epoch_uptime_ms=observables.epoch_uptime_ms,
        receive_time_s=observables.receive_time_s,
        position_ecef_m=out_position,
        clock_bias_m=out_bias,
        num_satellites=out_count,
        residuals_m=out_residuals,
        sat_ids=list(observables.sat_ids),
        dop=dop,
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
