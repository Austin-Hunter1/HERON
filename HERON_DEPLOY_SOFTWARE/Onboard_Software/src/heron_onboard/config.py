"""The onboard config model.

Every tunable value of the onboard software is here, with its type,
its limits, and its meaning. The TOML file
``Onboard_Software/config/onboard.example.toml`` shows the layout.
Load it with ``load_onboard_config``; a bad file gives one clear error.

Do not put a value in code that belongs here (CLAUDE.md rule 5).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from heron_common.config import load_config, validate_config
from heron_common.protocol import LinkConfig
from pydantic import BaseModel, Field, field_validator, model_validator

SDR_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")


class GeneralConfig(BaseModel):
    """Logging and identity."""

    log_level: str = "INFO"
    log_dir: Path = Path("/media/DataStore/logs")
    tick_s: float = Field(default=0.1, gt=0, description="Main loop period")


class TelemetryConfig(BaseModel):
    """What the payload streams to the ground and how often (O10)."""

    rate_hz: float = Field(default=1.0, gt=0, le=10)
    include_temps: bool = True


class ControlConfig(BaseModel):
    """Recording control and the autonomous fallback (D-010, D-017).

    ``fallback_mode``:

    - ``record_if_no_link``: wait ``link_grace_s`` after boot for a
      ground frame; when none arrives, start recording (default).
    - ``record_at_boot``: start recording as soon as the payload is
      ready, link or not.
    - ``none``: never start on its own; only a ground command starts.

    ``rearm_fallback_on_link_loss``: when true, an IDLE payload that
    loses a link it once had starts recording after ``link_grace_s``
    more seconds. Default false (an operator who saw the link is in
    control).
    """

    fallback_mode: Literal["record_if_no_link", "record_at_boot", "none"] = "record_if_no_link"
    link_grace_s: float = Field(default=120.0, ge=0)
    link_lost_s: float = Field(default=10.0, gt=0, description="No frames for this long = LOST")
    rearm_fallback_on_link_loss: bool = False
    flight_id_prefix: str = Field(default="", max_length=32)

    @field_validator("flight_id_prefix")
    @classmethod
    def _prefix_chars(cls, value: str) -> str:
        if value and not SDR_ID_PATTERN.match(value):
            raise ValueError("use only letters, digits, '_' and '-'")
        return value


class DiskConfig(BaseModel):
    """Data location and the stop-before-full rule (O11)."""

    data_root: Path = Path("/media/DataStore/iq")
    min_free_gb: float = Field(default=20.0, ge=0)
    min_free_pct: float = Field(default=5.0, ge=0, le=100)
    check_interval_s: float = Field(default=2.0, gt=0)


class ChannelConfig(BaseModel):
    """One RX channel of one SDR."""

    id: str = Field(description="Channel name used in file paths, e.g. L5_direct")
    index: int = Field(ge=0, le=1, description="RX channel index on the SDR (0 or 1)")
    center_freq_hz: float = Field(gt=0)
    gain_db: float = Field(ge=0)
    bandwidth_hz: float = Field(gt=0)
    antenna: str = Field(default="RX2", description="RX2 or TX/RX")
    subdev: str = Field(default="", description="UHD subdev spec; empty = default")
    antenna_label: str = Field(default="", description="Physical antenna label, for metadata")
    band: str = Field(
        default="",
        description="GNSS band name from the [bands] table, e.g. L5 (used in metadata.yml)",
    )

    @field_validator("id")
    @classmethod
    def _id_chars(cls, value: str) -> str:
        if not SDR_ID_PATTERN.match(value):
            raise ValueError("use only letters, digits, '_' and '-'")
        return value


class SdrConfig(BaseModel):
    """One SDR unit and its channels."""

    id: str = Field(description="Unit name used in file paths, e.g. b210_1")
    model: Literal["b210", "b200", "b200mini", "b205mini", "b206mini", "other"] = "other"
    serial: str = Field(min_length=1, description="UHD serial number from uhd_find_devices")
    device_args: str = Field(
        default="", description="Extra UHD device args, e.g. recv_buff_size=2e7,num_recv_frames=256"
    )
    sample_rate_hz: float = Field(gt=0)
    clock_source: Literal["internal", "external", "gpsdo"] = Field(
        default="external", description="10 MHz reference input (D-014)"
    )
    time_source: Literal["none", "external", "gpsdo"] = Field(
        default="none",
        description="PPS input. B210: external. B200mini has one reference input, so: none (D-020)",
    )
    channels: list[ChannelConfig] = Field(min_length=1, max_length=2)

    @field_validator("id")
    @classmethod
    def _id_chars(cls, value: str) -> str:
        if not SDR_ID_PATTERN.match(value):
            raise ValueError("use only letters, digits, '_' and '-'")
        return value

    @property
    def pps_aligned(self) -> bool:
        """True when this unit sets its clock from a PPS edge."""
        return self.time_source != "none"

    @model_validator(mode="after")
    def _unique_channels(self) -> SdrConfig:
        ids = [c.id for c in self.channels]
        idx = [c.index for c in self.channels]
        if len(set(ids)) != len(ids):
            raise ValueError("channel ids must be unique within one SDR")
        if len(set(idx)) != len(idx):
            raise ValueError("channel indexes must be unique within one SDR")
        return self

    @property
    def uhd_args(self) -> str:
        """The UHD device-args string for this unit."""
        parts = [f"serial={self.serial}"]
        if self.device_args:
            parts.append(self.device_args)
        return ",".join(parts)

    @property
    def bytes_per_second(self) -> float:
        """Disk rate of this unit at sc8 (2 bytes per complex sample)."""
        return self.sample_rate_hz * 2 * len(self.channels)


class CaptureConfig(BaseModel):
    """Recorder process settings shared by all SDRs (O2, O3, O16)."""

    recorder_binary: Path = Path("/usr/local/bin/heron_recorder")
    segment_seconds: float = Field(default=30.0, gt=0)
    cpu_format: Literal["sc8", "sc16"] = "sc8"
    wire_format: Literal["sc8", "sc16"] = "sc16"
    wire_peak: float = Field(default=0.25, gt=0, le=1, description="Only for sc8 wire format")
    sync_lead_s: float = Field(
        default=6.0, ge=3, description="Seconds from launch to the common PPS sync epoch"
    )
    start_lead_s: float = Field(
        default=3.0, ge=2, description="Seconds from the sync epoch to the first sample"
    )
    buffer_seconds: float = Field(default=4.0, gt=0, description="Host ring buffer per recorder")
    status_interval_s: float = Field(default=1.0, gt=0)
    lock_timeout_s: float = Field(default=10.0, gt=0, description="Wait for ref/LO lock")
    ready_timeout_s: float = Field(default=60.0, gt=0, description="Recorder must report ready")
    stop_timeout_s: float = Field(default=15.0, gt=0, description="Recorder must exit after stop")
    file_extension: str = Field(default="sc8", pattern=r"^[A-Za-z0-9]+$")


class HealthConfig(BaseModel):
    """Health sampling."""

    temp_labels: list[str] = Field(
        default_factory=lambda: ["coretemp", "nvme", "acpitz"],
        description="psutil sensor names to report; empty = all",
    )


class OnboardConfig(BaseModel):
    """The whole onboard config file."""

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    link: LinkConfig = Field(default_factory=LinkConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    control: ControlConfig = Field(default_factory=ControlConfig)
    disk: DiskConfig = Field(default_factory=DiskConfig)
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
    bands: dict[str, float] = Field(
        default_factory=dict,
        description="GNSS band name -> carrier centre frequency in Hz, e.g. L5 = 1176.45e6",
    )
    sdr: list[SdrConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_sdrs(self) -> OnboardConfig:
        ids = [s.id for s in self.sdr]
        serials = [s.serial for s in self.sdr]
        if len(set(ids)) != len(ids):
            raise ValueError("sdr ids must be unique")
        if len(set(serials)) != len(serials):
            raise ValueError("sdr serial numbers must be unique")
        # A channel's band must exist in [bands]. Channels that share a
        # band must share a centre frequency, because metadata.yml keeps
        # one intermediate frequency per band (D-022).
        centre_by_band: dict[str, tuple[str, float]] = {}
        for sdr in self.sdr:
            for ch in sdr.channels:
                if not ch.band:
                    continue
                if ch.band not in self.bands:
                    raise ValueError(
                        f"sdr {sdr.id} channel {ch.id}: band {ch.band!r} is not in [bands]"
                    )
                seen = centre_by_band.get(ch.band)
                if seen is not None and seen[1] != ch.center_freq_hz:
                    raise ValueError(
                        f"channels {seen[0]} and {sdr.id}/{ch.id} share band {ch.band} "
                        "but have different center_freq_hz"
                    )
                centre_by_band.setdefault(ch.band, (f"{sdr.id}/{ch.id}", ch.center_freq_hz))
        return self

    @property
    def aggregate_bytes_per_second(self) -> float:
        """Total disk rate of all units. Compare to the SSD limit (HARDWARE.md)."""
        return sum(s.bytes_per_second for s in self.sdr)

    @property
    def any_pps_aligned(self) -> bool:
        """True when at least one unit uses a PPS time source."""
        return any(s.pps_aligned for s in self.sdr)


def load_onboard_config(path: Path) -> OnboardConfig:
    """Load and validate the onboard config file."""
    return load_config(path, OnboardConfig)


def validate_onboard_dict(data: dict, source: str = "<dict>") -> OnboardConfig:
    """Validate an in-memory config dict (tests and the demo use this)."""
    return validate_config(data, OnboardConfig, source)
