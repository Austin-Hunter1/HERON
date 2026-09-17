"""The payload link protocol.

Three layers, each in its own module, so any one can change alone:

1. ``messages``: what we say. Pydantic models for commands, acks, and
   telemetry, and the JSON encoding.
2. ``framing``: how we mark message boundaries and detect corruption.
   One frame is ``<json>*<crc16 hex>\\n``.
3. ``transport``: how bytes move. Loopback (tests), serial radio, UDP,
   and MAVLink ``TUNNEL`` through the flight controller (D-016).

The onboard supervisor and the base station both use these modules and
nothing else to talk to each other.
"""

from heron_common.protocol.framing import FrameParser, crc16, encode_frame
from heron_common.protocol.messages import (
    Ack,
    Command,
    CommandName,
    Message,
    SdrStatus,
    Telemetry,
    decode_message,
    encode_message,
)
from heron_common.protocol.transport import (
    LinkConfig,
    LoopbackTransport,
    Transport,
    TransportError,
    make_transport,
)

__all__ = [
    "Ack",
    "Command",
    "CommandName",
    "FrameParser",
    "LinkConfig",
    "LoopbackTransport",
    "Message",
    "SdrStatus",
    "Telemetry",
    "Transport",
    "TransportError",
    "crc16",
    "decode_message",
    "encode_frame",
    "encode_message",
    "make_transport",
]
