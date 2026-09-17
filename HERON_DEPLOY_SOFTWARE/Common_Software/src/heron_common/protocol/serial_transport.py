"""Serial port transport (bench telemetry radios, wired serial).

Uses ``pyserial``. Reads are non-blocking: ``recv`` takes whatever is
in the input buffer. A lost device (unplugged USB) closes the port and
``is_open`` turns false; the link manager retries ``open`` on a timer.
"""

from __future__ import annotations

import logging

from heron_common.protocol.transport import SerialTransportConfig, Transport, TransportError

log = logging.getLogger(__name__)


class SerialTransport(Transport):
    """Move frame bytes over one serial port."""

    def __init__(self, config: SerialTransportConfig) -> None:
        self._config = config
        self._port = None

    def open(self) -> None:
        try:
            import serial  # Imported here so tests run without pyserial.
        except ImportError as exc:
            raise TransportError("pyserial is not installed (uv sync --extra serial)") from exc
        try:
            self._port = serial.Serial(
                self._config.port,
                baudrate=self._config.baud,
                timeout=self._config.timeout_s,
                write_timeout=1.0,
            )
        except (serial.SerialException, OSError) as exc:
            raise TransportError(f"Cannot open serial port {self._config.port}: {exc}") from exc
        log.info("Serial transport open: %s @ %d", self._config.port, self._config.baud)

    def close(self) -> None:
        if self._port is not None:
            try:
                self._port.close()
            except OSError:
                pass
            self._port = None

    @property
    def is_open(self) -> bool:
        return self._port is not None

    def send(self, data: bytes) -> None:
        if self._port is None:
            return
        try:
            self._port.write(data)
        except (OSError, Exception) as exc:  # pyserial raises its own errors.
            log.warning("Serial write failed, closing port: %s", exc)
            self.close()

    def recv(self) -> bytes:
        if self._port is None:
            return b""
        try:
            waiting = self._port.in_waiting
            if waiting:
                return self._port.read(waiting)
            return b""
        except (OSError, Exception) as exc:
            log.warning("Serial read failed, closing port: %s", exc)
            self.close()
            return b""

    def describe(self) -> str:
        return f"serial({self._config.port}@{self._config.baud})"
