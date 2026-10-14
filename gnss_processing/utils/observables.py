"""
From tracked code phase to pseudoranges.

A tracking channel measures one thing that matters here: `code_phase_ms`, the
cumulative code phase, which advances at the *satellite's* code rate and therefore
measures satellite time.  Two steps turn that into a pseudorange.

**Anchor it.**  Code phase is cumulative but its origin is arbitrary -- it starts
wherever acquisition happened to seed it.  A decoded navigation message supplies
the missing constant: at the symbol where message M begins, satellite time was
`M.tow_at_message_start_s`.  Pair that with the code phase at the same symbol and
every other epoch's transmit time follows by simple addition.  That pairing is a
`TimeAnchor`, and it is the only thing the navigation message is needed for.

**Put every satellite on one clock.**  Each channel's epochs land on its own code
phase grid, so no two satellites produce measurements at the same instant.  A
position fix needs them simultaneous, so each channel's transmit time is
interpolated onto a common receiver-time grid.

The receiver clock in that last step is just the sample counter -- `uptime_ms`
mapped linearly to seconds.  It is not GPS time and does not need to be: a
constant offset is absorbed by the clock bias the position solve estimates, and
the *drift* of the sample clock relative to GPS shows up as a slope in that
estimate.  That slope is the "clock solution", and it is a real measurement of the
front end's oscillator rather than a nuisance parameter.

Corrections in this module are the ones that need no receiver position: the
satellite clock polynomial, relativity, and group delay.  Everything that depends
on where the receiver is -- Sagnac, troposphere, ionospheric mapping -- lives in
`utils.navigation`, where a position estimate exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import time_systems
from .nav.ephemeris import SPEED_OF_LIGHT, BdsEphemeris, CnavEphemeris, Ephemeris, LnavEphemeris

# Nominal carrier frequencies, for scaling a single-frequency ionospheric delay
# between bands.  The Klobuchar model is defined for L1.
L1_FREQ_HZ = 1575.42e6
L2_FREQ_HZ = 1227.60e6
L5_FREQ_HZ = 1176.45e6

BAND_FREQ_HZ: dict[str, float] = {
    "GPS_L1CA": L1_FREQ_HZ,
    "GPS_L1C": L1_FREQ_HZ,
    "GPS_L2C": L2_FREQ_HZ,
    "GPS_L5": L5_FREQ_HZ,
    "BDS_B2A": L5_FREQ_HZ,
}

# A rough transit time, used only to place the nominal receiver clock so that
# pseudoranges come out near their true magnitude.  Any constant would do -- the
# clock bias absorbs it -- but a sensible one makes the numbers readable.
NOMINAL_TRANSIT_TIME_S = 0.075


@dataclass(frozen=True)
class TimeAnchor:
    """Ties one satellite's cumulative code phase to absolute time of week."""

    sat_id: str
    tow_s: float
    """Time of week at the anchor instant, in the system named by `time_system`."""

    code_phase_ms: float
    """Cumulative code phase at that same instant."""

    week: int | None = None
    source: str = ""
    """Human-readable provenance, e.g. "CNAV type 11, TOW count 100001"."""

    time_system: str = "GPST"
    """The time scale of `tow_s` and `week`: "GPST", or "BDT" for BeiDou.  BDT has
    no leap seconds and runs 14 s behind GPST, so a BDT anchor must be converted
    before it is mixed with GPS data; nothing in this class does that."""

    def transmit_time_s(self, code_phase_ms: np.ndarray | float) -> np.ndarray | float:
        """
        Satellite time at any epoch, from its cumulative code phase.

        Exact rather than approximate: code phase advances at the satellite's own
        code rate, so a difference in code phase *is* a difference in satellite
        time.  No Doppler term, no iteration.
        """
        return self.tow_s + (np.asarray(code_phase_ms) - self.code_phase_ms) * 1e-3


def anchor_from_cnav(
    stream, message, sat_id: str | None = None, *, sat_id_prefix: str = "G"
) -> TimeAnchor:
    """
    Build a `TimeAnchor` from a decoded CNAV message and the stream it came from.

    `message.symbol_index` indexes the same symbol array `stream` holds, so the
    code phase at the message's first symbol is a direct lookup -- which is the
    whole reason `SymbolStream` carries `code_phase_ms` alongside its values.
    """
    if message.symbol_index >= len(stream.code_phase_ms):
        raise ValueError(
            f"message at symbol {message.symbol_index} is past the end of a "
            f"{len(stream.code_phase_ms)}-symbol stream"
        )
    return TimeAnchor(
        sat_id=sat_id or f"{sat_id_prefix}{message.prn:02d}",
        tow_s=message.tow_at_message_start_s,
        code_phase_ms=float(stream.code_phase_ms[message.symbol_index]),
        source=f"CNAV type {message.message_type}, TOW count {message.tow_count}",
    )


def anchor_from_bcnav2(stream, frame, week: int | None = None) -> TimeAnchor:
    """
    Build a `TimeAnchor` from a decoded B-CNAV2 frame and the stream it came from.

    The SOW of a B-CNAV2 frame names the first preamble chip of that same frame, and
    `frame.symbol_index` is where that preamble starts in `stream`, so the code phase
    there is a direct lookup.  The time system is BDT.  The BDT week number is only
    in message type 10 (`Type10.wn`); pass it as `week` when it is known.
    """
    if frame.symbol_index >= len(stream.code_phase_ms):
        raise ValueError(
            f"frame at symbol {frame.symbol_index} is past the end of a "
            f"{len(stream.code_phase_ms)}-symbol stream"
        )
    return TimeAnchor(
        sat_id=f"C{frame.prn:02d}",
        tow_s=frame.sow_at_frame_start_s,
        code_phase_ms=float(stream.code_phase_ms[frame.symbol_index]),
        week=week,
        source=f"B-CNAV2 type {frame.message_type}, SOW count {frame.sow_count}",
        time_system="BDT",
    )


def anchor_from_lnav(stream, subframe, sat_id: str) -> TimeAnchor:
    """Build a `TimeAnchor` from a decoded LNAV subframe."""
    if subframe.symbol_index >= len(stream.code_phase_ms):
        raise ValueError(
            f"subframe at symbol {subframe.symbol_index} is past the end of a "
            f"{len(stream.code_phase_ms)}-symbol stream"
        )
    return TimeAnchor(
        sat_id=sat_id,
        tow_s=subframe.tow_at_subframe_start_s,
        code_phase_ms=float(stream.code_phase_ms[subframe.symbol_index]),
        source=f"LNAV subframe {subframe.subframe_id}, TOW count {subframe.tow_count}",
    )


@dataclass
class Observables:
    """Pseudoranges for several satellites on one common measurement grid."""

    epoch_uptime_ms: np.ndarray
    """(N,) the receiver-clock instants measurements were formed at."""

    receive_time_s: np.ndarray
    """(N,) nominal receiver-clock time of week.  Offset from true GPS time by the
    receiver clock bias, which the position solve estimates."""

    sat_ids: list[str]
    transmit_time_s: np.ndarray
    """(N, M) satellite time of transmission, NaN where a channel had no data."""

    pseudorange_m: np.ndarray
    """(N, M) raw pseudorange, before any correction."""

    week: int | None = None
    anchors: dict[str, TimeAnchor] = field(default_factory=dict)

    time_system: str = "GPST"
    """The time scale of `receive_time_s` and `transmit_time_s`: "GPST" or "BDT".
    A set built from BeiDou anchors alone is in BDT; a combined set is in GPST."""

    signal_type_ids: dict[str, str] = field(default_factory=dict)
    """Satellite id -> signal type id (e.g. "GPS_L5", "BDS_B2A"), for a combined set
    whose satellites were tracked on different signals.  A satellite with no entry
    takes the `signal_type_id` the caller passes to the solver."""

    @property
    def num_epochs(self) -> int:
        return len(self.epoch_uptime_ms)

    def valid_mask(self) -> np.ndarray:
        """(N, M) True where a satellite contributed a measurement."""
        return np.isfinite(self.pseudorange_m)

    def satellites_per_epoch(self) -> np.ndarray:
        """(N,) how many satellites each epoch has.  Below four, no fix exists."""
        return self.valid_mask().sum(axis=1)


def _interpolate_code_phase(
    outputs,
    epoch_uptime_ms: np.ndarray,
    *,
    max_gap_factor: float | None = None,
    guard_after_gap_ms: float = 0.0,
) -> np.ndarray:
    """
    Code phase at arbitrary receiver-clock instants.

    Linear interpolation is exact enough to be uninteresting here: code phase is
    smooth and nearly linear in time, and consecutive epochs are 10-20 ms apart, so
    the curvature over one interval is far below a millimetre of range.  Requests
    outside the tracked span return NaN rather than extrapolating -- an
    extrapolated code phase is a fabricated measurement.

    The same rule applies INSIDE the tracked span when a channel lost lock and
    was re-acquired (`utils.tracking_supervisor`).  The merged record then has a
    hole of several hundred milliseconds in `uptime_epoch_ms`, and the code
    phase after the hole carries the code periods the dropped samples took with
    them -- up to a full code period of jump (c x 1 ms = 300 km on L1 C/A).  A
    straight line across that hole puts a made-up, satellite-dependent fraction
    of the jump into every request that lands in it, which is a fabricated
    measurement just as much as an extrapolated one.  So:

    - `max_gap_factor`: an interval between two consecutive epochs that is longer
      than this many times the MEDIAN epoch spacing is a hole, and requests
      inside it return NaN.  Relative to the median, not an absolute time, so
      one value fits 5 ms (L1 C/A) and 20 ms (L5) epochs alike.  `None` keeps
      the old behaviour (interpolate across everything).
    - `guard_after_gap_ms`: also return NaN for this long after the hole ends,
      while the freshly seeded loop settles.  0 disables it.
    """
    valid = outputs.valid
    uptime = outputs.uptime_epoch_ms[valid]
    code_phase = outputs.code_phase_ms[valid]
    if len(uptime) < 2:
        return np.full(len(epoch_uptime_ms), np.nan)
    out = np.interp(epoch_uptime_ms, uptime, code_phase, left=np.nan, right=np.nan)

    if max_gap_factor is None:
        return out
    spacing = np.diff(uptime)
    hole_limit_ms = max_gap_factor * float(np.median(spacing))
    hole_indices = np.nonzero(spacing > hole_limit_ms)[0]
    for i in hole_indices:
        # Hole is the open interval between epoch i and epoch i+1; the guard
        # extends it past the far end.
        in_hole = (epoch_uptime_ms > uptime[i]) & (
            epoch_uptime_ms < uptime[i + 1] + guard_after_gap_ms
        )
        out[in_hole] = np.nan
    return out


def form_observables(
    tracking_outputs: dict,
    anchors: dict[str, TimeAnchor],
    *,
    epoch_uptime_ms: np.ndarray | None = None,
    epoch_interval_ms: float = 100.0,
    week: int | None = None,
    max_gap_factor: float | None = None,
    guard_after_gap_ms: float = 0.0,
    time_system: str | None = None,
) -> Observables:
    """
    Build pseudoranges for every anchored satellite on one common grid.

    `time_system` is the scale the result is expressed in.  Left at None it is the
    scale of the anchors when they all agree (a BeiDou-only set is then in BDT), and
    GPST when they do not.  Satellites whose anchor is in another scale are moved by
    the constant 14 s offset, so every transmit time and the receive time share one
    scale -- which is what makes `receive - transmit` a range.

    `tracking_outputs` maps satellite id to a `SignalTrackingOutputs`.  Only
    satellites that also appear in `anchors` contribute -- an unanchored channel
    has no absolute time and its code phase means nothing on its own.

    The grid defaults to every `epoch_interval_ms` over the span all anchored
    channels share.  100 ms is a deliberate choice: fast enough to show the
    receiver clock drifting, slow enough that a minute of tracking is a few hundred
    fixes rather than tens of thousands.

    `max_gap_factor` and `guard_after_gap_ms` control what happens at a hole left
    by a lost-and-reacquired channel; see `_interpolate_code_phase`.  Both
    default to the old behaviour (interpolate across holes), so a caller opts in.
    """
    sat_ids = sorted(set(tracking_outputs) & set(anchors))
    if not sat_ids:
        raise ValueError(
            "no satellite has both tracking outputs and a time anchor; nothing can "
            "be measured. Decode the navigation message first."
        )

    if epoch_uptime_ms is None:
        starts, stops = [], []
        for sat_id in sat_ids:
            outputs = tracking_outputs[sat_id]
            uptime = outputs.uptime_epoch_ms[outputs.valid]
            if len(uptime) < 2:
                continue
            starts.append(uptime[0])
            stops.append(uptime[-1])
        if not starts:
            raise ValueError("no channel has enough epochs to interpolate")
        # The common span, not the union: a measurement epoch is only usable where
        # every satellite has data, and stepping outside that would silently drop
        # satellites mid-run.
        start, stop = max(starts), min(stops)
        if stop <= start:
            raise ValueError(
                "the tracked channels do not overlap in time; no common measurement "
                "epoch exists"
            )
        epoch_uptime_ms = np.arange(start, stop, epoch_interval_ms)
    epoch_uptime_ms = np.asarray(epoch_uptime_ms, dtype=float)

    if time_system is None:
        scales = {anchors[sat_id].time_system for sat_id in sat_ids}
        time_system = scales.pop() if len(scales) == 1 else "GPST"

    n, m = len(epoch_uptime_ms), len(sat_ids)
    transmit = np.full((n, m), np.nan)
    for j, sat_id in enumerate(sat_ids):
        code_phase = _interpolate_code_phase(
            tracking_outputs[sat_id],
            epoch_uptime_ms,
            max_gap_factor=max_gap_factor,
            guard_after_gap_ms=guard_after_gap_ms,
        )
        transmit[:, j] = time_systems.convert_time_of_week(
            anchors[sat_id].transmit_time_s(code_phase), anchors[sat_id].time_system, time_system
        )

    # Anchor the nominal receiver clock so pseudoranges land near their true
    # magnitude.  The offset is arbitrary and the clock bias absorbs it; picking a
    # sensible one only makes the numbers readable.
    with np.errstate(invalid="ignore"):
        first_epoch = np.nanmax(transmit[0]) if n else 0.0
    receive_time_0 = first_epoch + NOMINAL_TRANSIT_TIME_S
    receive_time = receive_time_0 + (epoch_uptime_ms - epoch_uptime_ms[0]) * 1e-3

    pseudorange = SPEED_OF_LIGHT * (receive_time[:, None] - transmit)

    return Observables(
        epoch_uptime_ms=epoch_uptime_ms,
        receive_time_s=receive_time,
        sat_ids=sat_ids,
        transmit_time_s=transmit,
        pseudorange_m=pseudorange,
        week=week,
        anchors={s: anchors[s] for s in sat_ids},
        time_system=time_system,
    )


def common_epoch_grid(
    tracking_outputs: list[dict],
    epoch_interval_ms: float = 100.0,
) -> np.ndarray:
    """
    One receiver-instant grid for several tracking runs of one recording.

    `form_observables` picks its own grid from the channels it is given, so two runs
    formed separately get different grids and cannot be combined
    (`combine_observables`).  This takes every channel of every run and returns the
    grid over the span all of them share, which both runs are then formed on.
    """
    starts, stops = [], []
    for outputs_by_sat in tracking_outputs:
        for outputs in outputs_by_sat.values():
            uptime = outputs.uptime_epoch_ms[outputs.valid]
            if len(uptime) >= 2:
                starts.append(uptime[0])
                stops.append(uptime[-1])
    if not starts:
        raise ValueError("no channel has enough epochs to interpolate")
    start, stop = max(starts), min(stops)
    if stop <= start:
        raise ValueError("the tracked channels do not overlap in time; no common grid exists")
    return np.arange(start, stop, epoch_interval_ms)


def combine_observables(
    parts: list[Observables],
    signal_type_ids: list[str],
    *,
    time_system: str = "GPST",
) -> Observables:
    """
    Join observables of several tracking runs (for example GPS L5 and BeiDou B2a of
    one collect) into one set on one measurement grid (D-058).

    The parts must be measured at the same receiver instants: same
    `epoch_uptime_ms`, which means both runs started at the same `START_OFFSET_MS` on
    the same sample clock, and the caller passed one grid to `form_observables` for
    both.  If the grids differ the receiver clock of one part is not the receiver
    clock of the other, and joining them would put a false offset into every
    pseudorange, so it is refused.

    Each part's `receive_time_s` was set separately, to a nominal 75 ms after its own
    latest satellite, so the parts disagree by an arbitrary constant.  They are put on
    the first part's receive time here, shifting the pseudoranges by the same amount;
    what remains between the systems is the real receiver inter-system bias, which
    the solver estimates (D-057).

    `signal_type_ids` names the signal each part was tracked on, in the same order.
    """
    if not parts:
        raise ValueError("no observables to combine")
    if len(signal_type_ids) != len(parts):
        raise ValueError("give one signal type id for each part")
    reference = parts[0]
    for number, part in enumerate(parts[1:], start=1):
        if not np.array_equal(part.epoch_uptime_ms, reference.epoch_uptime_ms):
            raise ValueError(
                f"part {number} is measured at different receiver instants than part 0; "
                "run both with the same START_OFFSET_MS and pass one epoch grid to "
                "form_observables"
            )

    def to_scale(part: Observables):
        shift = time_systems.convert_time_of_week(0.0, part.time_system, time_system)
        return part.receive_time_s + shift, part.transmit_time_s + shift

    common_receive, _ = to_scale(reference)
    sat_ids: list[str] = []
    transmit_columns, pseudorange_columns = [], []
    anchors: dict[str, TimeAnchor] = {}
    types: dict[str, str] = {}
    for part, signal_type_id in zip(parts, signal_type_ids):
        receive, transmit = to_scale(part)
        for column, sat_id in enumerate(part.sat_ids):
            if sat_id in sat_ids:
                raise ValueError(f"satellite {sat_id} appears in more than one part")
            sat_ids.append(sat_id)
            types[sat_id] = part.signal_type_ids.get(sat_id, signal_type_id)
        transmit_columns.append(transmit)
        # Same range as before, measured against the common receive time.
        pseudorange_columns.append(
            part.pseudorange_m + SPEED_OF_LIGHT * (common_receive - receive)[:, None]
        )
        anchors.update(part.anchors)
    return Observables(
        epoch_uptime_ms=reference.epoch_uptime_ms,
        receive_time_s=common_receive,
        sat_ids=sat_ids,
        transmit_time_s=np.hstack(transmit_columns),
        pseudorange_m=np.hstack(pseudorange_columns),
        week=reference.week,
        anchors=anchors,
        time_system=time_system,
        signal_type_ids=types,
    )


def cn0_on_grid(outputs, epoch_uptime_ms: np.ndarray, component: int) -> np.ndarray:
    """
    The C/N0 estimate of one channel at the receiver instants of a grid, in dB-Hz.

    Tracking writes a C/N0 estimate every few hundred milliseconds
    (`cn0_uptime_ms`, `cn0_dbhz`).  This interpolates it onto the grid; outside the
    span it covers the value is NaN, not an extrapolation.  `component` is the one the
    loops ran on (the pilot).
    """
    block = outputs.cn0_valid
    times = np.asarray(outputs.cn0_uptime_ms)[block]
    values = np.asarray(outputs.cn0_dbhz)[block, component]
    if len(times) < 1:
        return np.full(len(epoch_uptime_ms), np.nan)
    return np.interp(epoch_uptime_ms, times, values, left=np.nan, right=np.nan)


def ephemeris_time_s(ephemeris: Ephemeris, time_s, time_system: str):
    """
    A time of week in the scale the ephemeris is defined in.

    GPS ephemerides are functions of GPST and the BeiDou one of BDT.  `time_system` is
    the scale `time_s` is in; the result is what to hand to `orbit_state` and
    `clock_correction_s`.
    """
    native = "BDT" if isinstance(ephemeris, BdsEphemeris) else "GPST"
    return time_systems.convert_time_of_week(time_s, time_system, native)


# ---------------------------------------------------------------------------
# Corrections that need no receiver position
# ---------------------------------------------------------------------------


def signal_group_delay_s(ephemeris: Ephemeris, signal_type_id: str) -> float:
    """
    The group delay term for one signal, as its interface spec states it.

    Every band's correction has the same shape -- `-T_GD + ISC_band` -- and each
    spec writes it out separately (IS-GPS-200N 30.3.3.3.1.1.1 for L1 C/A and L2C,
    IS-GPS-705J 20.3.3.3.1.2.1 for L5).  Combined with the clock polynomial it
    gives the band-specific `(dt_sv)_band` those equations define.

    Only `CnavEphemeris` carries inter-signal corrections; LNAV broadcasts T_GD
    alone, which is the L1 P(Y)-to-L1 C/A term, so an LNAV ephemeris used for L5
    gets the T_GD part and no ISC.  That is a real limitation of the legacy message
    rather than a gap here, and it is worth a decimetre or two.
    """
    if isinstance(ephemeris, BdsEphemeris):
        # BeiDou states the pilot and data corrections itself (ICD 7-4 and 7-5).  The
        # pilot's is used: it is the component tracking runs its carrier loop on.
        if signal_type_id != "BDS_B2A":
            raise ValueError(
                f"a BeiDou ephemeris from B-CNAV2 has group delays for B2a only, "
                f"not {signal_type_id!r}"
            )
        return ephemeris.group_delay_s("B2ap")
    tgd = ephemeris.tgd
    isc = 0.0
    if isinstance(ephemeris, CnavEphemeris):
        isc = {
            "GPS_L1CA": ephemeris.isc_l1ca,
            "GPS_L2C": ephemeris.isc_l2c,
            "GPS_L5": ephemeris.isc_l5i5,
        }.get(signal_type_id) or 0.0
    return -tgd + isc


def satellite_clock_corrections(
    observables: Observables,
    ephemerides: dict[str, Ephemeris],
    *,
    signal_type_id: str,
    apply_group_delay: bool = True,
) -> np.ndarray:
    """
    (N, M) satellite clock correction in seconds, including relativity.

    Sign convention, which is the easy thing to get backwards: GPS time of
    transmission is `t = t_sv - dt_sv`.  The satellite's clock runs *ahead* by
    `dt_sv`, so the true transmit time is earlier, the true range is longer, and
    the correction ADDS to the pseudorange -- `+ c * dt_sv`.  Getting the sign
    wrong doubles the error rather than removing it, which at tens of kilometres is
    obvious; getting it wrong on group delay alone hides in the metres.
    """
    n, m = observables.transmit_time_s.shape
    out = np.full((n, m), np.nan)
    for j, sat_id in enumerate(observables.sat_ids):
        eph = ephemerides.get(sat_id)
        if eph is None:
            continue
        sat_signal = observables.signal_type_ids.get(sat_id, signal_type_id)
        delay = signal_group_delay_s(eph, sat_signal) if apply_group_delay else 0.0
        for i in range(n):
            t_sv = observables.transmit_time_s[i, j]
            if not np.isfinite(t_sv):
                continue
            # The ephemeris wants its own system's time, which differs by 14 s for
            # BeiDou in a GPST set.  The clock polynomial and the orbit both use it.
            t_native = ephemeris_time_s(eph, t_sv, observables.time_system)
            out[i, j] = eph.clock_correction_s(t_native) + delay
    return out


# ---------------------------------------------------------------------------
# Atmosphere
# ---------------------------------------------------------------------------


def klobuchar_delay_s(
    alpha: tuple[float, float, float, float],
    beta: tuple[float, float, float, float],
    *,
    latitude_semicircles: float,
    longitude_semicircles: float,
    elevation_semicircles: np.ndarray | float,
    azimuth_semicircles: np.ndarray | float,
    gps_time_of_week_s: np.ndarray | float,
    frequency_hz: float = L1_FREQ_HZ,
) -> np.ndarray:
    """
    The broadcast single-frequency ionospheric model (IS-GPS-200N 20.3.3.5.2.5).

    Every angle is in **semi-circles**, which is how the spec states the algorithm
    and how the coefficients are scaled; converting at the boundary rather than
    inside keeps this readable against the published equations.

    The model is defined for L1.  Ionospheric delay scales as 1/f^2, so another
    band's delay is the L1 value times (f_L1/f)^2 -- for L5 that is about 1.79, so
    ignoring the scaling on an L5 fix leaves nearly half the ionosphere in.

    It is a coarse model: roughly half the RMS ionospheric error on a good day, and
    it does not pretend otherwise.  Dual-frequency measurement is the real answer,
    and this repository's collects are single-band.
    """
    elevation = np.asarray(elevation_semicircles, dtype=float)
    azimuth = np.asarray(azimuth_semicircles, dtype=float)
    gps_time = np.asarray(gps_time_of_week_s, dtype=float)

    # Earth-centred angle between receiver and ionospheric pierce point.
    psi = 0.0137 / (elevation + 0.11) - 0.022

    phi_i = latitude_semicircles + psi * np.cos(azimuth * np.pi)
    phi_i = np.clip(phi_i, -0.416, 0.416)

    lambda_i = longitude_semicircles + psi * np.sin(azimuth * np.pi) / np.cos(
        phi_i * np.pi
    )
    # Geomagnetic latitude of the pierce point.
    phi_m = phi_i + 0.064 * np.cos((lambda_i - 1.617) * np.pi)

    local_time = np.mod(4.32e4 * lambda_i + gps_time, 86400.0)

    amplitude = sum(alpha[k] * phi_m**k for k in range(4))
    amplitude = np.maximum(amplitude, 0.0)
    period = sum(beta[k] * phi_m**k for k in range(4))
    period = np.maximum(period, 72000.0)

    x = 2.0 * np.pi * (local_time - 50400.0) / period
    # Obliquity: the slant path through the shell is longer at low elevation.
    obliquity = 1.0 + 16.0 * (0.53 - elevation) ** 3

    delay = np.where(
        np.abs(x) < 1.57,
        obliquity * (5e-9 + amplitude * (1.0 - x**2 / 2.0 + x**4 / 24.0)),
        obliquity * 5e-9,
    )
    return delay * (L1_FREQ_HZ / frequency_hz) ** 2


def saastamoinen_delay_m(
    elevation_rad: np.ndarray | float,
    *,
    height_m: float,
    latitude_rad: float,
    pressure_hpa: float | None = None,
    temperature_k: float | None = None,
    relative_humidity: float = 0.5,
) -> np.ndarray:
    """
    Tropospheric delay by the Saastamoinen model with a standard atmosphere.

    A few metres at zenith growing to tens near the horizon, and unlike the
    ionosphere it is not dispersive -- the same delay on every band, so a
    dual-frequency receiver cannot measure it away either.  With no met sensor the
    standard atmosphere is used, which is good to a few centimetres in the dry
    component and considerably worse in the wet one.

    The mapping function is the simple `1/sin(elevation)` obliquity.  That is
    adequate above about 15 degrees and increasingly optimistic below it, which is
    part of why a low-elevation satellite deserves to be de-weighted.
    """
    elevation = np.asarray(elevation_rad, dtype=float)

    # US standard atmosphere referred to sea level, extrapolated to the site.
    if pressure_hpa is None:
        pressure_hpa = 1013.25 * (1.0 - 2.26e-5 * height_m) ** 5.225
    if temperature_k is None:
        temperature_k = 291.15 - 6.5e-3 * height_m
    partial_water_vapour = (
        6.108
        * relative_humidity
        * np.exp((17.15 * temperature_k - 4684.0) / (temperature_k - 38.45))
    )

    # Gravity correction for latitude and height.
    gravity = 1.0 - 0.00266 * np.cos(2.0 * latitude_rad) - 0.00028e-3 * height_m

    zenith_delay = (
        0.002277
        / gravity
        * (
            pressure_hpa
            + (1255.0 / temperature_k + 0.05) * partial_water_vapour
        )
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        mapping = 1.0 / np.sin(np.maximum(elevation, np.deg2rad(3.0)))
    return zenith_delay * mapping
