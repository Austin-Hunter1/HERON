"""Load and check the ground simulation config.

Why: the project rule is that no tunable value lives in code. This module
reads one TOML file and stops with a clear message when a value is bad.
"""

from __future__ import annotations

import tomllib
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

ALLOWED_CONSTELLATIONS = {"G", "E", "C", "J"}


class ConfigError(Exception):
    """The config file is missing or has a bad value."""


class _Strict(BaseModel):
    """Base class. An unknown key is an error, so a typo does not pass."""

    model_config = ConfigDict(extra="forbid")


class SiteConfig(_Strict):
    """Receiver position and height."""

    latitude_deg: float
    longitude_deg: float
    ground_height_m: float
    receiver_height_agl_m: float

    @field_validator("latitude_deg")
    @classmethod
    def _lat(cls, v: float) -> float:
        if not -90.0 <= v <= 90.0:
            raise ValueError("latitude_deg must be between -90 and 90")
        return v

    @field_validator("longitude_deg")
    @classmethod
    def _lon(cls, v: float) -> float:
        if not -180.0 <= v <= 180.0:
            raise ValueError("longitude_deg must be between -180 and 180")
        return v

    @field_validator("receiver_height_agl_m")
    @classmethod
    def _height(cls, v: float) -> float:
        if v <= 0.0:
            raise ValueError("receiver_height_agl_m must be more than 0")
        return v


class TimeConfig(_Strict):
    """Test date, local time window and sample step."""

    date: date
    start_local: time
    end_local: time
    timezone: str
    step_s: float

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as err:
            raise ValueError(f"unknown time zone: {v}") from err
        return v

    @field_validator("step_s")
    @classmethod
    def _step(cls, v: float) -> float:
        if v <= 0.0:
            raise ValueError("step_s must be more than 0")
        return v

    @model_validator(mode="after")
    def _order(self) -> TimeConfig:
        if self.end_local <= self.start_local:
            raise ValueError("end_local must be after start_local")
        return self

    def window(self) -> tuple[datetime, datetime]:
        """Return the (start, end) of the test as time-zone aware datetimes."""
        tz = ZoneInfo(self.timezone)
        return (
            datetime.combine(self.date, self.start_local, tzinfo=tz),
            datetime.combine(self.date, self.end_local, tzinfo=tz),
        )

    def sample_offsets_s(self) -> list[float]:
        """Return the sample times as seconds after the start (end included)."""
        start, end = self.window()
        total = (end - start) / timedelta(seconds=1)
        count = int(total // self.step_s)
        offsets = [i * self.step_s for i in range(count + 1)]
        if offsets[-1] < total:
            offsets.append(total)
        return offsets


class SignalConfig(_Strict):
    """Which satellites and which carrier to use."""

    constellations: list[str]
    carrier_hz: float
    elevation_mask_deg: float

    @field_validator("constellations")
    @classmethod
    def _consts(cls, v: list[str]) -> list[str]:
        bad = sorted(set(v) - ALLOWED_CONSTELLATIONS)
        if not v or bad:
            raise ValueError(
                f"constellations must be a non-empty list of {sorted(ALLOWED_CONSTELLATIONS)}"
                + (f"; bad: {bad}" if bad else "")
            )
        return v

    @field_validator("carrier_hz")
    @classmethod
    def _carrier(cls, v: float) -> float:
        if v <= 0.0:
            raise ValueError("carrier_hz must be more than 0")
        return v

    @field_validator("elevation_mask_deg")
    @classmethod
    def _mask(cls, v: float) -> float:
        if not 5.0 <= v < 90.0:
            raise ValueError("elevation_mask_deg must be at least 5 and less than 90")
        return v


class PathConfig(_Strict):
    """Folders. Relative paths start at the config file folder."""

    planner_dir: Path
    data_dir: Path
    water_geojson: Path
    output_dir: Path


class MapConfig(_Strict):
    """Satellite map settings for the HTML page."""

    tile_url: str
    attribution: str
    max_native_zoom: int
    radius_m: float  # water inside this distance of the receiver goes on the map

    @field_validator("radius_m")
    @classmethod
    def _radius(cls, v: float) -> float:
        if v <= 0.0:
            raise ValueError("radius_m must be more than 0")
        return v


class TerrainConfig(_Strict):
    """Terrain from a USGS 3DEP elevation grid. Leave the table out for flat ground."""

    dem_file: Path  # saved grid; made by `--fetch-dem`
    source_url: str  # 3DEP ImageServer base URL
    radius_m: float  # grid half-width around the receiver
    cell_m: float  # grid cell size
    step_m: float  # spacing of the points checked along a ray
    max_ray_m: float  # a satellite ray is followed this far
    clearance_m: float  # the ground must be this far above a ray to block it
    # Optional. When absent, the water level is the median DEM height in the
    # nearest water body.
    water_level_m: float | None = None

    @field_validator("radius_m", "cell_m", "step_m", "max_ray_m")
    @classmethod
    def _positive(cls, v: float) -> float:
        if v <= 0.0:
            raise ValueError("must be more than 0")
        return v

    @model_validator(mode="after")
    def _sizes(self) -> TerrainConfig:
        if self.cell_m >= self.radius_m:
            raise ValueError("cell_m must be smaller than radius_m")
        if self.clearance_m < 0.0:
            raise ValueError("clearance_m must not be negative")
        return self


class SimConfig(_Strict):
    """The whole config file."""

    site: SiteConfig
    time: TimeConfig
    signals: SignalConfig
    map: MapConfig
    paths: PathConfig
    terrain: TerrainConfig | None = None


def load_config(path: Path) -> SimConfig:
    """Read the TOML file at `path` and return a checked `SimConfig`.

    Relative paths in the `[paths]` table become absolute, based on the
    folder of the config file, so the tool works from any working folder.
    """
    path = Path(path).resolve()
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        cfg = SimConfig.model_validate(raw)
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(f"{path}: not valid TOML: {err}") from err
    except ValueError as err:  # pydantic's ValidationError is a ValueError
        raise ConfigError(f"{path}: bad config:\n{err}") from err
    base = path.parent
    resolved = {
        name: (base / value).resolve() if not value.is_absolute() else value
        for name, value in cfg.paths.model_dump().items()
    }
    update: dict = {"paths": PathConfig(**resolved)}
    if cfg.terrain is not None:
        dem = cfg.terrain.dem_file
        update["terrain"] = cfg.terrain.model_copy(
            update={"dem_file": dem if dem.is_absolute() else (base / dem).resolve()}
        )
    return cfg.model_copy(update=update)
