"""Transports: how frame bytes move between payload and ground.

A transport moves bytes. It knows nothing about frames or messages.
This keeps the radio hardware choice (Q-006, D-016) out of the protocol
code: to change the radio, add a transport and change one config key.

Transports:

- ``loopback``: two in-process ends joined by queues. For unit tests
  and the ``--demo`` mode.
- ``serial``: a serial port, for a telemetry radio on the bench.
- ``udp``: UDP datagrams, for Wi-Fi or Ethernet on the bench.
- ``mavlink``: MAVLink ``TUNNEL`` messages through the flight
  controller link. This is the flight transport (D-016).

The serial, UDP, and MAVLink transports live in their own modules and
import their libraries lazily, so a machine without ``pyserial`` or
``pymavlink`` can still run tests with the loopback transport.
"""

from __future__ import annotations

import queue
from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, Field


class TransportError(Exception):
    """The transport cannot open, send, or receive."""


class Transport(ABC):
    """Interface every transport implements.

    All methods are non-blocking. The link manager polls ``recv`` in
    its main loop. A transport must never raise from ``recv`` for a
    transient problem; it returns ``b""`` and reports the problem
    through ``is_open``/logging so the loop keeps running (O14).
    """

    @abstractmethod
    def open(self) -> None:
        """Open the device or socket. Raise ``TransportError`` on failure."""

    @abstractmethod
    def close(self) -> None:
        """Close the device or socket. Safe to call twice."""

    @abstractmethod
    def send(self, data: bytes) -> None:
        """Queue ``data`` for transmission. ``data`` holds whole frames."""

    @abstractmethod
    def recv(self) -> bytes:
        """Return bytes that arrived since the last call, or ``b""``."""

    @abstractmethod
    def describe(self) -> str:
        """Return a short human-readable description for logs."""

    @property
    def is_open(self) -> bool:
        """True while the transport can move bytes."""
        return True


class LoopbackTransport(Transport):
    """In-process transport for tests and demos.

    Make a pair with ``LoopbackTransport.pair()``. Bytes sent on one
    end arrive on the other. Tests can break the link with
    ``connected = False`` (both directions drop) to simulate link loss.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._inbox: queue.Queue[bytes] = queue.Queue()
        self._peer: LoopbackTransport | None = None
        self.connected = True
        self.sent_bytes = 0
        self.dropped_frames = 0

    @classmethod
    def pair(cls) -> tuple[LoopbackTransport, LoopbackTransport]:
        """Return two joined ends, ``(a, b)``."""
        a, b = cls("loopback-a"), cls("loopback-b")
        a._peer, b._peer = b, a
        return a, b

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def send(self, data: bytes) -> None:
        self.sent_bytes += len(data)
        if self._peer is None or not self.connected or not self._peer.connected:
            self.dropped_frames += 1
            return
        self._peer._inbox.put(data)

    def recv(self) -> bytes:
        chunks = []
        while True:
            try:
                chunks.append(self._inbox.get_nowait())
            except queue.Empty:
                break
        return b"".join(chunks)

    def describe(self) -> str:
        return f"loopback({self.name})"


class SerialTransportConfig(BaseModel):
    """Settings for the ``serial`` transport."""

    port: str = Field(default="/dev/ttyUSB0", description="Prefer /dev/serial/by-id/... paths")
    baud: int = Field(default=57600, gt=0)
    timeout_s: float = Field(default=0.0, ge=0)


class UdpTransportConfig(BaseModel):
    """Settings for the ``udp`` transport.

    Set ``remote_port`` to 0 on the side that should learn its peer from
    the first received datagram (the payload does this so the laptop
    address need not be known ahead of time).
    """

    local_host: str = "0.0.0.0"
    local_port: int = Field(default=14600, ge=0, le=65535)
    remote_host: str = "127.0.0.1"
    remote_port: int = Field(default=14601, ge=0, le=65535)


class MavlinkTransportConfig(BaseModel):
    """Settings for the ``mavlink`` transport (D-016).

    ``device`` is a pymavlink connection string: a serial device path
    (``/dev/serial/by-id/...``) on the payload, or ``udpin:0.0.0.0:14550``
    style on the ground. See ``mavlink_transport`` for the frame chunking
    rules and the ArduPilot routing requirements.
    """

    device: str = "/dev/ttyUSB0"
    baud: int = Field(default=57600, gt=0)
    source_system: int = Field(default=1, ge=1, le=255)
    source_component: int = Field(default=191, ge=1, le=255)
    target_system: int = Field(default=250, ge=0, le=255)
    target_component: int = Field(default=0, ge=0, le=255)
    payload_type: int = Field(default=200, ge=0, le=65535)
    heartbeat_hz: float = Field(default=1.0, gt=0)
    chunk_bytes: int = Field(default=125, ge=16, le=125)
    reassembly_timeout_s: float = Field(default=5.0, gt=0)
    accept_any_source: bool = Field(
        default=False, description="Accept TUNNEL frames from any sysid/compid"
    )


class LinkConfig(BaseModel):
    """The ``[link]`` config table, shared by both sides."""

    transport: Literal["loopback", "serial", "udp", "mavlink"] = "loopback"
    serial: SerialTransportConfig = Field(default_factory=SerialTransportConfig)
    udp: UdpTransportConfig = Field(default_factory=UdpTransportConfig)
    mavlink: MavlinkTransportConfig = Field(default_factory=MavlinkTransportConfig)


def make_transport(config: LinkConfig, name: str = "link") -> Transport:
    """Build the transport named by ``config.transport``.

    Import the optional libraries only for the transport in use, so a
    missing library gives a clear error for that transport alone.
    """
    if config.transport == "loopback":
        return LoopbackTransport(name)
    if config.transport == "serial":
        from heron_common.protocol.serial_transport import SerialTransport

        return SerialTransport(config.serial)
    if config.transport == "udp":
        from heron_common.protocol.udp_transport import UdpTransport

        return UdpTransport(config.udp)
    if config.transport == "mavlink":
        from heron_common.protocol.mavlink_transport import MavlinkTransport

        return MavlinkTransport(config.mavlink)
    raise TransportError(f"Unknown transport: {config.transport}")
