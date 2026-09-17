"""MAVLink TUNNEL transport: HERON frames through the flight controller.

Decision D-016: the payload link rides on the flight-control telemetry
link. The NUC connects to a Cube Orange telemetry serial port as an
onboard-computer MAVLink component. The base laptop reads the ground
side of the same MAVLink stream (Herelink hotspot UDP, or a telemetry
radio serial port).

How it works:

- HERON frames go inside MAVLink ``TUNNEL`` messages (id 385). The Cube
  routes them by ``target_system``/``target_component`` and never
  interprets them.
- A TUNNEL payload holds at most 128 bytes. A frame larger than
  ``chunk_bytes`` is split. Each chunk starts with a 3-byte header:
  ``frame_id``, ``chunk_index``, ``chunk_count``. The receiver
  reassembles chunks by ``frame_id``. A frame with a missing chunk is
  dropped after ``reassembly_timeout_s``; the next telemetry replaces
  it and the command layer retries a lost command.
- Both ends send a heartbeat so ArduPilot learns the route to each
  sysid/compid. Without a heartbeat the Cube does not forward.

ArduPilot setup (record the final values in DEPLOYMENT.md):

- The NUC port: ``SERIALn_PROTOCOL = 2`` (MAVLink2), ``SERIALn_BAUD``
  to match ``baud``.
- The NUC uses the vehicle's ``source_system`` (default 1) and its own
  ``source_component`` (default 191, MAV_COMP_ID_ONBOARD_COMPUTER).
- The base uses its own ``source_system`` (default 250) so it does not
  collide with Mission Planner (255).

The chunking code is pure Python and tested without pymavlink.
"""

from __future__ import annotations

import logging
import time

from heron_common.protocol.transport import MavlinkTransportConfig, Transport, TransportError

log = logging.getLogger(__name__)

TUNNEL_PAYLOAD_BYTES = 128
CHUNK_HEADER_BYTES = 3
MAX_CHUNKS = 255


def split_frame(frame: bytes, frame_id: int, chunk_bytes: int) -> list[bytes]:
    """Split ``frame`` into TUNNEL payloads with chunk headers.

    ``chunk_bytes`` is the data size per chunk (the header adds 3).
    Raise ``ValueError`` when the frame needs more than 255 chunks.
    """
    if chunk_bytes + CHUNK_HEADER_BYTES > TUNNEL_PAYLOAD_BYTES:
        raise ValueError("chunk_bytes too large for a TUNNEL payload")
    pieces = [frame[i : i + chunk_bytes] for i in range(0, len(frame), chunk_bytes)] or [b""]
    if len(pieces) > MAX_CHUNKS:
        raise ValueError(f"Frame of {len(frame)} bytes needs more than {MAX_CHUNKS} chunks")
    return [
        bytes((frame_id & 0xFF, index, len(pieces))) + piece for index, piece in enumerate(pieces)
    ]


class ChunkReassembler:
    """Rebuild frames from TUNNEL chunk payloads.

    Chunks for one frame share a ``frame_id``. ``feed`` returns the
    whole frame when its last chunk arrives, else ``None``. Partial
    frames older than ``timeout_s`` are dropped.
    """

    def __init__(self, timeout_s: float) -> None:
        self._timeout_s = timeout_s
        self._partial: dict[int, tuple[float, int, dict[int, bytes]]] = {}
        self.dropped_frames = 0

    def feed(self, payload: bytes, now: float | None = None) -> bytes | None:
        now = time.monotonic() if now is None else now
        self._expire(now)
        if len(payload) < CHUNK_HEADER_BYTES:
            return None
        frame_id, index, count = payload[0], payload[1], payload[2]
        if count == 0 or index >= count:
            return None
        data = payload[CHUNK_HEADER_BYTES:]
        if count == 1:
            return data
        started, expected, parts = self._partial.get(frame_id, (now, count, {}))
        if expected != count:
            # A new frame reused this id before the old one completed.
            started, parts = now, {}
        parts[index] = data
        if len(parts) == count:
            self._partial.pop(frame_id, None)
            return b"".join(parts[i] for i in range(count))
        self._partial[frame_id] = (started, count, parts)
        return None

    def _expire(self, now: float) -> None:
        stale = [
            fid for fid, (started, _, _) in self._partial.items() if now - started > self._timeout_s
        ]
        for fid in stale:
            del self._partial[fid]
            self.dropped_frames += 1


class MavlinkTransport(Transport):
    """Move HERON frames as MAVLink TUNNEL messages via pymavlink."""

    def __init__(self, config: MavlinkTransportConfig) -> None:
        self._config = config
        self._conn = None
        self._mavlink = None
        self._frame_id = 0
        self._last_heartbeat = 0.0
        self._reassembler = ChunkReassembler(config.reassembly_timeout_s)
        self._peer_seen: tuple[int, int] | None = None

    def open(self) -> None:
        try:
            from pymavlink import mavutil
        except ImportError as exc:
            raise TransportError("pymavlink is not installed (uv sync --extra mavlink)") from exc
        try:
            self._conn = mavutil.mavlink_connection(
                self._config.device,
                baud=self._config.baud,
                source_system=self._config.source_system,
                source_component=self._config.source_component,
                autoreconnect=True,
            )
        except Exception as exc:  # pymavlink raises many types here.
            raise TransportError(f"Cannot open MAVLink {self._config.device}: {exc}") from exc
        self._mavlink = mavutil.mavlink
        log.info(
            "MAVLink transport open: %s sys=%d comp=%d -> sys=%d comp=%d",
            self._config.device,
            self._config.source_system,
            self._config.source_component,
            self._config.target_system,
            self._config.target_component,
        )
        self._send_heartbeat(force=True)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    @property
    def is_open(self) -> bool:
        return self._conn is not None

    def _send_heartbeat(self, force: bool = False) -> None:
        """Send a heartbeat at ``heartbeat_hz`` so the Cube learns our route."""
        now = time.monotonic()
        if not force and now - self._last_heartbeat < 1.0 / self._config.heartbeat_hz:
            return
        self._last_heartbeat = now
        assert self._conn is not None and self._mavlink is not None
        try:
            self._conn.mav.heartbeat_send(
                self._mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
                self._mavlink.MAV_AUTOPILOT_INVALID,
                0,
                0,
                self._mavlink.MAV_STATE_ACTIVE,
            )
        except Exception as exc:
            log.warning("MAVLink heartbeat failed: %s", exc)

    def send(self, data: bytes) -> None:
        if self._conn is None:
            return
        self._frame_id = (self._frame_id + 1) & 0xFF
        try:
            chunks = split_frame(data, self._frame_id, self._config.chunk_bytes)
        except ValueError as exc:
            log.error("Cannot send frame over MAVLink: %s", exc)
            return
        for chunk in chunks:
            padded = chunk + bytes(TUNNEL_PAYLOAD_BYTES - len(chunk))
            try:
                self._conn.mav.tunnel_send(
                    self._config.target_system,
                    self._config.target_component,
                    self._config.payload_type,
                    len(chunk),
                    padded,
                )
            except Exception as exc:
                log.warning("MAVLink tunnel send failed: %s", exc)
                return

    def recv(self) -> bytes:
        if self._conn is None:
            return b""
        self._send_heartbeat()
        out = []
        while True:
            try:
                msg = self._conn.recv_match(type="TUNNEL", blocking=False)
            except Exception as exc:
                log.warning("MAVLink recv failed: %s", exc)
                break
            if msg is None:
                break
            if msg.payload_type != self._config.payload_type:
                continue
            if not self._config.accept_any_source and self._config.target_system:
                src = (msg.get_srcSystem(), msg.get_srcComponent())
                if src[0] != self._config.target_system:
                    continue
                if self._config.target_component and src[1] != self._config.target_component:
                    continue
                if src != self._peer_seen:
                    self._peer_seen = src
                    log.info("MAVLink peer seen: sys=%d comp=%d", *src)
            payload = bytes(msg.payload[: msg.payload_length])
            frame = self._reassembler.feed(payload)
            if frame is not None:
                out.append(frame)
        return b"".join(out)

    def describe(self) -> str:
        return f"mavlink({self._config.device})"
