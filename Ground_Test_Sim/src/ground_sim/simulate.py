"""Compute specular points and Fresnel zones for one fixed receiver.

Why flat-ground maths here: the receiver is only a few metres above the
ground. Earth curvature changes the result by far less than a millimetre
at this scale, so the exact flat-plane formula is used. (The planner's
`specular_point` uses a numeric search with a 0.4 m stop size. That is
too coarse for a 2 m high receiver: the track would jitter.)
The satellite positions and the az/el maths come from Jack Abrams' engine.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from ground_sim.config import SimConfig

C_M_S = 299_792_458.0
# Same lower limit as the planner's fresnel_axes: below this the formula
# gives zones of kilometres and has no practical value.
MIN_FRESNEL_EL_DEG = 8.0


@dataclass(frozen=True)
class Sample:
    """One satellite at one time, as seen by the receiver."""

    t_s: float  # seconds after the start of the window
    az_deg: float
    el_deg: float
    east_m: float  # specular point, local frame, origin = receiver
    north_m: float
    along_m: float  # first Fresnel zone semi-axis, along the azimuth
    across_m: float  # first Fresnel zone semi-axis, across the azimuth
    extra_path_m: float  # reflected path minus direct path
    # Set by `water.annotate`. Without water data they keep these defaults.
    lat: float = 0.0  # specular point
    lon: float = 0.0
    on_water: bool = False  # specular point inside a mapped water body
    water_frac: float = 0.0  # part of the first Fresnel zone over water
    blocked: bool = False  # terrain blocks the satellite ray or a reflection leg
    surface_m: float = 0.0  # height of the reflecting water surface used (DEM datum)
    body: str = ""  # name of the water body the specular point is in

    @property
    def available(self) -> bool:
        """True when the reflection exists: on water and not blocked by terrain."""
        return self.on_water and not self.blocked

    @property
    def incidence_deg(self) -> float:
        """Angle between the signal and the ground normal (0 = straight down)."""
        return 90.0 - self.el_deg


@dataclass
class Track:
    """An unbroken run of samples of one satellite above the mask."""

    sid: str
    constellation: str
    samples: list[Sample] = field(default_factory=list)


def fresnel_axes(h_m: float, el_deg: float, wavelength_m: float) -> tuple[float, float]:
    """Return the (along, across) semi-axes of the first Fresnel zone, metres.

    Same formula as `Specular_Planner/engine/geometry.fresnel_axes`, but the
    wavelength is an argument, so the carrier can come from the config:
    across = sqrt(L h / sin e), along = sqrt(L h / sin^3 e).
    """
    s = math.sin(math.radians(max(el_deg, MIN_FRESNEL_EL_DEG)))
    return math.sqrt(wavelength_m * h_m / s**3), math.sqrt(wavelength_m * h_m / s)


def flat_ground_specular(h_m: float, az_deg: float, el_deg: float) -> tuple[float, float, float]:
    """Return (east, north, extra path) of the reflection on flat ground.

    The point is `h / tan(el)` away from the receiver, towards the satellite
    azimuth. The reflected path is longer than the direct path by `2 h sin(el)`.
    """
    rho = h_m / math.tan(math.radians(el_deg))
    az = math.radians(az_deg)
    return rho * math.sin(az), rho * math.cos(az), 2.0 * h_m * math.sin(math.radians(el_deg))


def _utc_times(cfg: SimConfig) -> tuple[list[datetime], list[float]]:
    """Return the sample datetimes (UTC) and their offsets from the start."""
    start_local, _ = cfg.time.window()
    start = start_local.astimezone(UTC)
    offsets = cfg.time.sample_offsets_s()
    return [start + timedelta(seconds=o) for o in offsets], offsets


def sky_passes(cfg: SimConfig, engine: SimpleNamespace, sats: list) -> list[Track]:
    """Return one `Track` per satellite pass above the elevation mask.

    Only time, azimuth and elevation are set. The reflection geometry
    depends on the water height, so `reflect_sample` adds it later. A
    satellite that sets and rises again in the window gives two tracks. A
    gap below the mask ends a track, so the table never shows a pass that
    was not seen.
    """
    geo = engine.geometry
    site = cfg.site
    rx = geo.lla_to_ecef(
        site.latitude_deg, site.longitude_deg, site.ground_height_m + site.receiver_height_agl_m
    )
    times, offsets = _utc_times(cfg)

    tracks: list[Track] = []
    for sat in sats:
        current: Track | None = None
        for dt, t_s in zip(times, offsets, strict=True):
            az, el = geo.az_el(sat.ecef(dt), rx, site.latitude_deg, site.longitude_deg)
            if el < cfg.signals.elevation_mask_deg:
                current = None
                continue
            if current is None:
                current = Track(sid=sat.sid, constellation=sat.constellation)
                tracks.append(current)
            current.samples.append(Sample(t_s, az, el, 0.0, 0.0, 0.0, 0.0, 0.0))
    tracks.sort(key=lambda tr: (tr.samples[0].t_s, tr.sid))
    return tracks


def reflect_sample(s: Sample, height_m: float, wavelength_m: float) -> Sample:
    """Return the sample with specular point and Fresnel zone for an antenna `height_m` up."""
    east, north, extra = flat_ground_specular(height_m, s.az_deg, s.el_deg)
    along, across = fresnel_axes(height_m, s.el_deg, wavelength_m)
    return dataclasses.replace(
        s, east_m=east, north_m=north, along_m=along, across_m=across, extra_path_m=extra
    )


def simulate(
    cfg: SimConfig, engine: SimpleNamespace, sats: list, reflect_height_m: float
) -> list[Track]:
    """Return the passes with the reflection geometry for one reflecting height.

    `reflect_height_m` is the antenna height above the reflecting surface.
    """
    wavelength = C_M_S / cfg.signals.carrier_hz
    passes = sky_passes(cfg, engine, sats)
    return [
        Track(
            tr.sid,
            tr.constellation,
            [reflect_sample(s, reflect_height_m, wavelength) for s in tr.samples],
        )
        for tr in passes
    ]
