"""UDP transport (bench Wi-Fi or Ethernet).

One datagram carries one ``send`` call. Datagrams can arrive out of
order or be lost; the frame CRC and the message ``seq`` handle that.

When ``remote_port`` is 0, the transport learns the peer address from
the first datagram it receives. The payload uses this so the config
does not need the laptop's address.
"""

from __future__ import annotations

import logging
import socket

from heron_common.protocol.transport import Transport, TransportError, UdpTransportConfig

log = logging.getLogger(__name__)

MAX_DATAGRAM = 65507


class UdpTransport(Transport):
    """Move frame bytes as UDP datagrams."""

    def __init__(self, config: UdpTransportConfig) -> None:
        self._config = config
        self._sock: socket.socket | None = None
        self._remote: tuple[str, int] | None = None
        if config.remote_port:
            self._remote = (config.remote_host, config.remote_port)

    def open(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self._config.local_host, self._config.local_port))
            sock.setblocking(False)
        except OSError as exc:
            raise TransportError(
                f"Cannot bind UDP {self._config.local_host}:{self._config.local_port}: {exc}"
            ) from exc
        self._sock = sock
        log.info("UDP transport open on %s:%d", self._config.local_host, self._config.local_port)

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    def send(self, data: bytes) -> None:
        if self._sock is None or self._remote is None:
            return
        try:
            self._sock.sendto(data, self._remote)
        except OSError as exc:
            log.warning("UDP send failed: %s", exc)

    def recv(self) -> bytes:
        if self._sock is None:
            return b""
        chunks = []
        while True:
            try:
                data, addr = self._sock.recvfrom(MAX_DATAGRAM)
            except BlockingIOError:
                break
            except ConnectionResetError:
                # Windows reports an ICMP "port unreachable" from an earlier
                # send as a recv error. The peer is simply not up yet.
                break
            except OSError as exc:
                log.warning("UDP recv failed: %s", exc)
                break
            if self._remote is None or not self._config.remote_port:
                if self._remote != addr:
                    log.info("UDP peer learned: %s:%d", *addr)
                self._remote = addr
            chunks.append(data)
        return b"".join(chunks)

    def describe(self) -> str:
        return f"udp({self._config.local_host}:{self._config.local_port})"
