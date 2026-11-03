"""
Assisted acquisition: use the time, the place and the sky mask to search harder (D-080).

A normal acquisition knows nothing: it searches every PRN over the full Doppler range,
and the threshold must hold the false-alarm rate over that whole grid.  For a weak
signal (indoors, under a roof, behind a wall) that is the wrong trade.  With three
pieces of information the search can be much smaller:

* **time** -- the UTC start of the collect, from its collect id (D-030), or given;
* **place** -- the approximate receiver position;
* **sky mask** -- the part of the sky that barriers block.  The default is the entire
  sky (no barrier).

With the broadcast ephemeris of that day (CDDIS), these predict which satellites are
above the mask, and the Doppler of each one.  The search then runs in two stages:

1. **Clock offset.**  The receiver oscillator moves every Doppler by the same amount
   (-1.64 ppm, about -2.6 kHz at L1, on the 2026-10-13 collect), which the
   prediction cannot know.  A normal, wide search of the few highest predicted
   satellites measures it: the median of measured minus predicted Doppler.
2. **Narrow search.**  Every other visible satellite is searched in a narrow window
   around its predicted Doppler plus that offset, with a longer dwell (more
   non-coherent blocks).  A narrow window has fewer cells, so the threshold for the
   same false-alarm total is lower; the longer dwell lifts a weak peak.  When stage 1
   finds no satellite, there is no offset, and stage 2 searches the visible
   satellites over the full Doppler range, still with the longer dwell.

Satellites below the mask are not searched.  Receiver motion is not modelled: a
drone at 10 m/s moves the Doppler by up to 50 Hz at L1, which the window covers.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from . import bpsk_acquisition, navigation, observables, time_systems

SPEED_OF_LIGHT_M_S = 299_792_458.0
# GPS time minus UTC since 2017-01-01.  The broadcast message states it too; a change
# is announced months ahead, so a parameter (not a lookup) is enough here.
GPS_MINUS_UTC_S = 18.0
# Records more than this far from the epoch on either side are loaded.  It matches
# the 2 h fit interval that `broadcast_ephemeris` and `rinex3_nav` select within.
EPHEMERIS_SPAN = timedelta(hours=2)


# ---------------------------------------------------------------------------
# Sky mask
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BlockedSector:
    """
    A part of the sky that a barrier blocks: azimuths from `az_from_deg` clockwise
    to `az_to_deg`, at elevations up to `up_to_elevation_deg`.

    The sector may cross north: (300, 60) covers 300-360 and 0-60 degrees.
    """

    az_from_deg: float
    az_to_deg: float
    up_to_elevation_deg: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.up_to_elevation_deg <= 90.0:
            raise ValueError(
                f"a blocked sector must block up to 0-90 deg elevation, got {self.up_to_elevation_deg}"
            )

    def covers(self, azimuth_deg: float, elevation_deg: float) -> bool:
        """True when the direction is inside this sector."""
        start = self.az_from_deg % 360.0
        width = (self.az_to_deg - self.az_from_deg) % 360.0
        if width == 0.0:
            width = 360.0  # a sector from an azimuth to the same azimuth is all round
        inside = (azimuth_deg - start) % 360.0 <= width
        return inside and elevation_deg <= self.up_to_elevation_deg


@dataclass(frozen=True)
class SkyMask:
    """Where the sky is open: above `min_elevation_deg` and outside every blocked sector."""

    min_elevation_deg: float = 0.0
    blocked: tuple[BlockedSector, ...] = ()

    def __post_init__(self) -> None:
        if not -10.0 <= self.min_elevation_deg < 90.0:
            raise ValueError(
                f"the minimum elevation must be from -10 to 90 deg, got {self.min_elevation_deg}"
            )

    def is_open(self, azimuth_deg: float, elevation_deg: float) -> bool:
        if elevation_deg < self.min_elevation_deg:
            return False
        return not any(sector.covers(azimuth_deg, elevation_deg) for sector in self.blocked)

    def describe(self) -> str:
        """One line for a printout or a report."""
        if self.min_elevation_deg <= 0.0 and not self.blocked:
            return "entire sky"
        parts = [f"elevation >= {self.min_elevation_deg:g} deg"]
        parts += [
            f"blocked az {s.az_from_deg:g}-{s.az_to_deg:g} up to {s.up_to_elevation_deg:g} deg"
            for s in self.blocked
        ]
        return "; ".join(parts)

    @classmethod
    def from_values(cls, min_elevation_deg: float, sectors) -> SkyMask:
        """
        Make a mask from the notebook values: a minimum elevation, and a list of
        `(az_from_deg, az_to_deg, up_to_elevation_deg)` tuples.  An empty list and
        0 deg is the entire sky.
        """
        blocked = []
        for sector in sectors or ():
            if len(sector) != 3:
                raise ValueError(
                    f"a blocked sector is (az_from_deg, az_to_deg, up_to_elevation_deg), got {sector!r}"
                )
            blocked.append(BlockedSector(*(float(v) for v in sector)))
        return cls(float(min_elevation_deg), tuple(blocked))


_SECTOR_TEXT = re.compile(r"^\s*(-?[\d.]+)\s*-\s*(-?[\d.]+)\s*:\s*([\d.]+)\s*$")


def parse_blocked_sectors(text: str) -> list[tuple[float, float, float]]:
    """
    Read blocked sectors from one line of text: `az_from-az_to:up_to_el`, separated
    by `;`.  For example `300-60:60; 120-180:30`.  An empty text is no sector (the
    entire sky).  The result is the list form that the notebook takes.
    """
    sectors = []
    for part in text.split(";"):
        if not part.strip():
            continue
        match = _SECTOR_TEXT.match(part)
        if match is None:
            raise ValueError(
                f"blocked sector {part.strip()!r} is not 'az_from-az_to:up_to_elevation' "
                "(for example 300-60:60)"
            )
        sector = tuple(float(v) for v in match.groups())
        BlockedSector(*sector)  # check the values now, not in the notebook
        sectors.append(sector)
    return sectors


# ---------------------------------------------------------------------------
# Time, place and ephemeris
# ---------------------------------------------------------------------------


def collect_start_utc(collect_id: str) -> datetime:
    """
    The UTC start of a collect, from its id `YYYYMMDD_HHMMSS_...` (D-030).

    A collect id without that start is an error: a guessed time gives wrong
    predictions with no warning.
    """
    try:
        return datetime.strptime("_".join(collect_id.split("_")[:2]), "%Y%m%d_%H%M%S")
    except ValueError:
        raise ValueError(
            f"collect id {collect_id!r} does not start with YYYYMMDD_HHMMSS; give the time "
            "(ASSIST_TIME_UTC in notebook 01, or the time field in the window)."
        ) from None


def parse_utc(text: str) -> datetime:
    """Read a UTC time 'YYYY-MM-DD HH:MM:SS' (a 'T' may replace the space)."""
    try:
        return datetime.fromisoformat(text.strip().replace("Z", ""))
    except ValueError:
        raise ValueError(f"time {text!r} is not 'YYYY-MM-DD HH:MM:SS' (UTC).") from None


def load_broadcast_ephemerides(
    sat_ids, epoch_gpst: datetime, resources_dir: str | Path | None = None
) -> dict:
    """
    One broadcast ephemeris per satellite id, for the GPS time `epoch_gpst`.

    GPS (`G..`) comes from the daily RINEX 2 brdc file, BeiDou (`C..`) from the RINEX 3
    file; both are downloaded from CDDIS once and cached in RESOURCES_PATH.  A
    satellite with no record within the fit interval is left out.  BeiDou GEO
    satellites are not in the result: `rinex3_nav` does not parse them, and they do
    not transmit B1C or B2a.
    """
    from . import broadcast_ephemeris, rinex3_nav

    sat_ids = list(sat_ids)
    week, tow = broadcast_ephemeris.gps_week_and_tow(epoch_gpst)
    out: dict = {}

    gps = [s for s in sat_ids if s.startswith("G")]
    if gps:
        records = broadcast_ephemeris.load_brdc_span(
            epoch_gpst - EPHEMERIS_SPAN, epoch_gpst + EPHEMERIS_SPAN, resources_dir
        )
        out.update(broadcast_ephemeris.ephemerides_for(
            gps, tow, week, epoch_gpst, records=records, verbose=False
        ))

    bds = [s for s in sat_ids if s.startswith("C")]
    if bds:
        bdt_week, bdt_tow = time_systems.gpst_to_bdt(week, tow)
        records: dict = {}
        days = sorted({(epoch_gpst - EPHEMERIS_SPAN).date(), (epoch_gpst + EPHEMERIS_SPAN).date()})
        for day in days:
            day_records, _skipped = rinex3_nav.load_day(datetime(day.year, day.month, day.day), resources_dir)
            for sid, recs in day_records.items():
                records.setdefault(sid, []).extend(recs)
        for sid in bds:
            record = rinex3_nav.select_ephemeris(records.get(sid, []), bdt_week, bdt_tow)
            if record is not None:
                out[sid] = record
    return out


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SatellitePrediction:
    """Where one satellite is, and what Doppler it should show, at the assist time."""

    sat_id: str
    azimuth_deg: float
    elevation_deg: float
    doppler_hz: float
    open_sky: bool
    """True when the direction is above the mask: the satellite is searched."""


def predict_satellites(
    sat_ids,
    ephemerides: dict,
    gps_tow_s: float,
    receiver_ecef_m: np.ndarray,
    carrier_hz: float,
    mask: SkyMask,
) -> dict[str, SatellitePrediction]:
    """
    Azimuth, elevation and Doppler of each satellite that has an ephemeris.

    The Doppler is `-range_rate / c * carrier`, the sign notebook 01 measures: an
    approaching satellite has a positive Doppler.  The range rate is the satellite
    velocity (a 1 s central difference of the orbit) along the line of sight; the
    receiver is taken as static.  A satellite with no ephemeris is not in the result.
    """
    receiver_ecef_m = np.asarray(receiver_ecef_m, dtype=float)
    out: dict[str, SatellitePrediction] = {}
    for sid in sat_ids:
        eph = ephemerides.get(sid)
        if eph is None:
            continue
        t = observables.ephemeris_time_s(eph, gps_tow_s, "GPST")
        before = eph.orbit_state(t - 0.5).position_ecef_m
        after = eph.orbit_state(t + 0.5).position_ecef_m
        position = 0.5 * (before + after)
        az, el = navigation.sky_positions(receiver_ecef_m, position)
        az, el = float(az[0]), float(el[0])
        line_of_sight = position - receiver_ecef_m
        line_of_sight /= np.linalg.norm(line_of_sight)
        range_rate = float(np.dot(after - before, line_of_sight))
        out[sid] = SatellitePrediction(
            sat_id=sid, azimuth_deg=az % 360.0, elevation_deg=el,
            doppler_hz=-range_rate / SPEED_OF_LIGHT_M_S * carrier_hz,
            open_sky=mask.is_open(az % 360.0, el),
        )
    return out


def predict_for_collect(
    sat_ids,
    utc: datetime,
    position_geo: tuple[float, float, float],
    carrier_hz: float,
    mask: SkyMask,
    *,
    gps_minus_utc_s: float = GPS_MINUS_UTC_S,
    resources_dir: str | Path | None = None,
) -> dict[str, SatellitePrediction]:
    """
    The whole prediction in one call: UTC to GPS time, the ephemeris download, the
    receiver position, then `predict_satellites`.  `utc` is the time of the first
    sample searched.
    """
    from . import broadcast_ephemeris

    if position_geo is None or len(position_geo) != 3:
        raise ValueError(
            "assisted acquisition needs the receiver position (lat deg, lon deg, height m)."
        )
    gpst = utc + timedelta(seconds=gps_minus_utc_s)
    _, tow = broadcast_ephemeris.gps_week_and_tow(gpst)
    ephemerides = load_broadcast_ephemerides(sat_ids, gpst, resources_dir)
    receiver = navigation.geodetic_to_ecef(*(float(v) for v in position_geo))
    return predict_satellites(sat_ids, ephemerides, tow, receiver, carrier_hz, mask)


# ---------------------------------------------------------------------------
# Two-stage search
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssistSettings:
    """The tunable values of the assisted search (notebook 01 config, D-080)."""

    dwell_multiplier: int = 10
    """Stage 2 uses `num_blocks * dwell_multiplier` non-coherent blocks."""
    stage1_satellites: int = 4
    """How many of the highest visible satellites stage 1 searches wide."""
    doppler_halfwidth_hz: float = 250.0
    """Stage 2 window: predicted + offset +/- this."""
    full_doppler_hz: float = 5000.0
    """Stage 1 range, and stage 2's when there is no offset: +/- this."""

    def __post_init__(self) -> None:
        if int(self.dwell_multiplier) != self.dwell_multiplier or self.dwell_multiplier < 1:
            raise ValueError(f"the dwell multiplier must be a whole number >= 1, got {self.dwell_multiplier}")
        if int(self.stage1_satellites) != self.stage1_satellites or self.stage1_satellites < 1:
            raise ValueError(f"stage 1 needs at least 1 satellite, got {self.stage1_satellites}")
        if self.doppler_halfwidth_hz <= 0 or self.full_doppler_hz <= 0:
            raise ValueError("the Doppler half-widths must be more than 0 Hz.")


@dataclass
class AssistReport:
    """What the assisted search predicted, measured and did, for printing and plots."""

    mask: SkyMask
    predictions: dict[str, SatellitePrediction]
    stage1_ids: list[str] = field(default_factory=list)
    stage2_ids: list[str] = field(default_factory=list)
    offset_hz: float | None = None
    stage2_window: str = ""
    not_predicted: list[str] = field(default_factory=list)
    """Searchable PRNs with no ephemeris: not searched."""

    def lines(self, results: dict) -> list[str]:
        """A table of every predicted satellite, and what happened to it."""
        out = [f"Sky mask: {self.mask.describe()}"]
        if self.offset_hz is None:
            out.append("Stage 1 found no satellite: no clock offset; stage 2 searched the full range.")
        else:
            out.append(f"Clock offset (median measured - predicted Doppler): {self.offset_hz:+.0f} Hz")
        out.append(f"Stage 2 window: {self.stage2_window}")
        out.append("")
        out.append(f"  {'PRN':<5}{'az':>6}{'el':>6}{'pred. Dopp.':>13}{'stage':>7}{'SNR':>7}"
                   f"{'thresh.':>9}{'meas. Dopp.':>13}  result")
        for sid, p in sorted(self.predictions.items(), key=lambda kv: -kv[1].elevation_deg):
            # A stage-1 satellite that stage 1 did not find is searched again in
            # stage 2; its result is the stage-2 one.
            stages = [n for n, ids in (("1", self.stage1_ids), ("2", self.stage2_ids)) if sid in ids]
            stage = ", ".join(stages) or "-"
            r = results.get(sid)
            if r is None:
                if p.elevation_deg < 0.0:
                    result = "below the horizon"
                else:
                    result = "blocked by mask" if not p.open_sky else "not searched"
                snr = thr = meas = ""
            else:
                result = "acquired" if r.signal_detected else "below threshold"
                snr, thr = f"{r.peak_snr_db:.1f}", f"{r.detection_threshold_db:.2f}"
                meas = f"{r.acq_doppler_hz:+.0f}" if r.signal_detected else ""
            out.append(f"  {sid:<5}{p.azimuth_deg:6.0f}{p.elevation_deg:6.1f}{p.doppler_hz:+13.0f}"
                       f"{stage:>7}{snr:>7}{thr:>9}{meas:>13}  {result}")
        if self.not_predicted:
            out.append(f"No ephemeris (not searched): {', '.join(self.not_predicted)}")
        return out


def estimate_offset_hz(results: dict, predictions: dict[str, SatellitePrediction]) -> float | None:
    """
    The common Doppler offset: the median of measured minus predicted over the
    detected satellites.  The median, so one false detection does not move it.
    None when no satellite was detected.
    """
    residuals = [
        r.acq_doppler_hz - predictions[sid].doppler_hz
        for sid, r in results.items()
        if r.signal_detected and sid in predictions
    ]
    return float(np.median(residuals)) if residuals else None


def snapped_window(
    base_config: bpsk_acquisition.AcquisitionConfiguration, low_hz: float, high_hz: float
) -> tuple[float, float]:
    """
    Doppler limits that make the configuration search at least `low_hz..high_hz`.

    Why: `AcquisitionConfiguration` turns its limits into whole FFT bins with
    `int()`, which rounds toward zero, and its last bin is exclusive.  A 1 ms replica
    has 1 kHz bins, so a +/-250 Hz window could become no bin at all.  This picks the
    whole bins that cover the window, and returns values a quarter bin inside them so
    `int()` gives exactly those bins for either sign.
    """
    resolution = base_config.sample_rate / base_config.replica_length_samples
    first = int(np.floor(low_hz / resolution))
    stop = int(np.floor(high_hz / resolution)) + 1

    def value(bin_index: int) -> float:
        return (bin_index + 0.25 * np.sign(bin_index)) * resolution

    return value(first), value(stop)


def _acquire(samples, config, code_params, p_fa, threshold_db, noise_var_method):
    """One `run_acquisition` call on exactly the samples the configuration needs."""
    return bpsk_acquisition.run_acquisition(
        sample_block=np.ascontiguousarray(samples[: config.total_num_samples]),
        sample_block_uptime_epoch_ms=0.0,
        acq_config=config,
        code_parameters=code_params,
        prob_false_alarm_total=p_fa,
        detection_threshold_db=threshold_db,
        noise_var_method=noise_var_method,
    )


def required_samples(base_config: bpsk_acquisition.AcquisitionConfiguration, settings: AssistSettings) -> int:
    """How many samples the longest (stage 2) dwell reads."""
    return base_config.total_num_samples * int(settings.dwell_multiplier)


def run_assisted_acquisition(
    baseband_samples: np.ndarray,
    base_config: bpsk_acquisition.AcquisitionConfiguration,
    code_parameters: dict,
    predictions: dict[str, SatellitePrediction],
    mask: SkyMask,
    settings: AssistSettings,
    prob_false_alarm_total: float,
    detection_threshold_db: float | None = None,
    noise_var_method: str = "abscorrvar",
) -> tuple[dict, AssistReport]:
    """
    The two-stage search (see the module docstring).  Returns `(results, report)`;
    `results` has the same form as `run_acquisition`'s, one entry per searched PRN,
    so tracking uses it with no change.  Each result keeps its own configuration and
    threshold: stage 2 windows have fewer cells, so their threshold is lower.

    `base_config` is notebook 01's normal configuration.  Its Doppler range is
    replaced by `settings.full_doppler_hz` (stage 1) or by the narrow window
    (stage 2), and its `num_blocks` is multiplied in stage 2.
    """
    report = AssistReport(mask=mask, predictions=predictions)
    report.not_predicted = sorted(sid for sid in code_parameters if sid not in predictions)
    visible = sorted(
        (sid for sid, p in predictions.items() if p.open_sky and sid in code_parameters),
        key=lambda sid: -predictions[sid].elevation_deg,
    )
    needed = required_samples(base_config, settings)
    if len(baseband_samples) < needed:
        raise ValueError(
            f"assisted acquisition needs {needed:,} samples for the x{settings.dwell_multiplier} "
            f"dwell, got {len(baseband_samples):,}."
        )
    results: dict = {}
    if not visible:
        report.stage2_window = "none (no satellite above the mask)"
        return results, report

    # Stage 1: the normal dwell over the full range, on the highest satellites.
    report.stage1_ids = visible[: int(settings.stage1_satellites)]
    wide = dataclasses.replace(
        base_config,
        min_search_doppler_hz=-settings.full_doppler_hz,
        max_search_doppler_hz=settings.full_doppler_hz,
    )
    stage1 = _acquire(
        baseband_samples, wide, {sid: code_parameters[sid] for sid in report.stage1_ids},
        prob_false_alarm_total, detection_threshold_db, noise_var_method,
    )
    results.update(stage1)
    report.offset_hz = estimate_offset_hz(stage1, predictions)

    # Stage 2: everything not yet detected, with the longer dwell.
    report.stage2_ids = [sid for sid in visible if not results.get(sid) or not results[sid].signal_detected]
    num_blocks = base_config.num_blocks * int(settings.dwell_multiplier)
    if report.offset_hz is None:
        report.stage2_window = (
            f"+/-{settings.full_doppler_hz:g} Hz (no offset), {num_blocks} blocks"
        )
        long_wide = dataclasses.replace(wide, num_blocks=num_blocks)
        results.update(_acquire(
            baseband_samples, long_wide, {sid: code_parameters[sid] for sid in report.stage2_ids},
            prob_false_alarm_total, detection_threshold_db, noise_var_method,
        ))
        return results, report

    report.stage2_window = (
        f"predicted {report.offset_hz:+.0f} Hz +/-{settings.doppler_halfwidth_hz:g} Hz, {num_blocks} blocks"
    )
    for sid in report.stage2_ids:
        centre = predictions[sid].doppler_hz + report.offset_hz
        low, high = snapped_window(
            base_config, centre - settings.doppler_halfwidth_hz, centre + settings.doppler_halfwidth_hz
        )
        narrow = dataclasses.replace(
            base_config, num_blocks=num_blocks, min_search_doppler_hz=low, max_search_doppler_hz=high,
        )
        results.update(_acquire(
            baseband_samples, narrow, {sid: code_parameters[sid]},
            prob_false_alarm_total, detection_threshold_db, noise_var_method,
        ))
    return results, report
