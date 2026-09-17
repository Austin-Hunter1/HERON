"""The base station config model.

See ``Base_Software/config/base.example.toml`` for the layout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from heron_common.config import load_config, validate_config
from heron_common.protocol import LinkConfig
from pydantic import BaseModel, Field


class GeneralConfig(BaseModel):
    log_level: str = "INFO"
    log_dir: Path = Path("./heron_base_logs")
    flight_log_dir: Path = Path("./heron_base_logs/flights")


class LinkHealthConfig(BaseModel):
    """How telemetry age maps to a link state (B3), and command retries."""

    degraded_s: float = Field(default=3.0, gt=0, description="Older than this = DEGRADED")
    lost_s: float = Field(default=10.0, gt=0, description="Older than this = LOST")
    command_timeout_s: float = Field(default=3.0, gt=0, description="Wait for an ack")
    command_retries: int = Field(default=3, ge=0, description="Resends after the first try")
    poll_s: float = Field(default=0.1, gt=0)


class CorrectionConfig(BaseModel):
    """Where RTK correction data (RTCM3) from the receiver goes (Q-010).

    ``none`` logs it with the raw stream only. ``serial`` forwards each
    RTCM3 frame to another serial port (for example the telemetry radio
    that feeds the Cube GNSS).
    """

    sink: Literal["none", "serial"] = "none"
    serial_port: str = ""
    serial_baud: int = Field(default=57600, gt=0)


class GnssConfig(BaseModel):
    """The GNSS receiver on the laptop (D-015, Q-002)."""

    enabled: bool = False
    port: str = Field(default="/dev/ttyACM0", description="Prefer /dev/serial/by-id/... paths")
    baud: int = Field(default=38400, gt=0)
    log_dir: Path = Path("./heron_base_logs/gnss")
    correction: CorrectionConfig = Field(default_factory=CorrectionConfig)
    stale_fix_s: float = Field(default=5.0, gt=0, description="No GGA for this long = no fix")


class DisplayConfig(BaseModel):
    refresh_hz: float = Field(default=2.0, gt=0, le=20)
    event_lines: int = Field(default=200, ge=10)


class BaseConfig(BaseModel):
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    link: LinkConfig = Field(default_factory=LinkConfig)
    link_health: LinkHealthConfig = Field(default_factory=LinkHealthConfig)
    gnss: GnssConfig = Field(default_factory=GnssConfig)
    display: DisplayConfig = Field(default_factory=DisplayConfig)


def load_base_config(path: Path) -> BaseConfig:
    return load_config(path, BaseConfig)


def validate_base_dict(data: dict, source: str = "<dict>") -> BaseConfig:
    return validate_config(data, BaseConfig, source)
