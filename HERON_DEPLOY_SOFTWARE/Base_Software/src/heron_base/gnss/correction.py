"""Correction sinks: where RTCM3 frames from the base receiver go (Q-010).

The path is not decided. This interface lets the team plug in the
final one without changes elsewhere. ``NullSink`` drops frames (they
are still in the raw log). ``SerialSink`` forwards them to a serial
port.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from heron_base.config import CorrectionConfig

log = logging.getLogger(__name__)


class CorrectionSink(ABC):
    @abstractmethod
    def write(self, frame: bytes) -> None: ...

    def close(self) -> None:
        return None

    def describe(self) -> str:
        return type(self).__name__


class NullSink(CorrectionSink):
    """Discard frames (the raw log still has them)."""

    def __init__(self) -> None:
        self.count = 0

    def write(self, frame: bytes) -> None:
        self.count += 1

    def describe(self) -> str:
        return "none"


class SerialSink(CorrectionSink):
    """Forward each frame to a serial port. Opens lazily, retries on error."""

    def __init__(self, port: str, baud: int) -> None:
        self._port_name = port
        self._baud = baud
        self._port: Any = None  # serial.Serial once open; imported lazily.
        self.count = 0
        self.errors = 0

    def _open(self) -> bool:
        if self._port is not None:
            return True
        try:
            import serial

            self._port = serial.Serial(self._port_name, baudrate=self._baud, write_timeout=1.0)
            log.info("correction sink open: %s @ %d", self._port_name, self._baud)
            return True
        except Exception as exc:
            self.errors += 1
            if self.errors in (1, 10, 100):
                log.warning("correction sink cannot open %s: %s", self._port_name, exc)
            return False

    def write(self, frame: bytes) -> None:
        if not self._open():
            return
        try:
            self._port.write(frame)
            self.count += 1
        except Exception as exc:
            self.errors += 1
            log.warning("correction sink write failed: %s", exc)
            try:
                self._port.close()
            finally:
                self._port = None

    def close(self) -> None:
        if self._port is not None:
            self._port.close()
            self._port = None

    def describe(self) -> str:
        return f"serial({self._port_name}@{self._baud})"


def make_sink(config: CorrectionConfig) -> CorrectionSink:
    if config.sink == "none":
        return NullSink()
    if config.sink == "serial":
        if not config.serial_port:
            raise ValueError("gnss.correction.serial_port is required for sink = serial")
        return SerialSink(config.serial_port, config.serial_baud)
    raise ValueError(f"unknown correction sink {config.sink}")
