"""Messages on the payload link.

Three message kinds cross the link:

- ``Command`` (ground to payload): ``start``, ``stop``, ``status``,
  ``ping`` (D-019).
- ``Ack`` (payload to ground): the reply to one command, matched by
  ``seq``.
- ``Telemetry`` (payload to ground): the live health set of
  REQUIREMENTS O10, sent at a configured rate.

Each message is one JSON object with a ``kind`` field. ``encode_message``
returns a complete frame (see ``framing``). ``decode_message`` turns a
verified frame payload back into a model, or raises ``ValueError``.

Change a message here and both sides change together. Add fields with
defaults so an old peer still parses a new message.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, ValidationError

from heron_common.protocol.framing import encode_frame

PROTOCOL_VERSION = 1


class CommandName(StrEnum):
    """Commands the ground station can send."""

    START = "start"
    STOP = "stop"
    STATUS = "status"
    PING = "ping"


class Command(BaseModel):
    """A command from the ground station to the payload.

    ``seq`` increases by one per command so the payload can drop a
    duplicate that a retry produced. ``args`` carries optional values,
    for example ``{"flight_id": "lake_a_run1"}`` with ``start``.
    """

    kind: Literal["cmd"] = "cmd"
    v: int = PROTOCOL_VERSION
    seq: int = Field(ge=0)
    name: CommandName
    args: dict[str, str] = Field(default_factory=dict)
    sent_at: float = Field(description="Sender UNIX time, seconds")


class Ack(BaseModel):
    """The payload's reply to one command."""

    kind: Literal["ack"] = "ack"
    v: int = PROTOCOL_VERSION
    seq: int = Field(ge=0, description="seq of the command this answers")
    ok: bool
    message: str = ""
    state: str = Field(description="Recording state after the command")
    sent_at: float


class SdrStatus(BaseModel):
    """Health of one SDR, as reported by its recorder process."""

    id: str
    present: bool = False
    streaming: bool = False
    ref_locked: bool | None = Field(default=None, description="None when not reported")
    overflows: int = 0
    host_drops: int = 0
    samples: int = 0
    rate_mbps: float = 0.0
    segment: int = 0
    fault: str | None = None


class Telemetry(BaseModel):
    """The live health set (REQUIREMENTS O10)."""

    kind: Literal["tlm"] = "tlm"
    v: int = PROTOCOL_VERSION
    seq: int = Field(ge=0)
    sent_at: float
    uptime_s: float
    state: str
    flight_id: str | None = None
    recording_s: float = 0.0
    sdrs: list[SdrStatus] = Field(default_factory=list)
    data_rate_mbps: float = 0.0
    disk_free_gb: float = 0.0
    disk_free_pct: float = 0.0
    disk_alert: bool = False
    cpu_pct: float = 0.0
    temps_c: dict[str, float] = Field(default_factory=dict)
    last_cmd_seq: int | None = None
    fault: str | None = None
    version: str = "unknown"
    link_good_frames: int = 0
    link_bad_frames: int = 0


Message = Annotated[Command | Ack | Telemetry, Field(discriminator="kind")]

_KIND_TO_MODEL: dict[str, type[BaseModel]] = {
    "cmd": Command,
    "ack": Ack,
    "tlm": Telemetry,
}


def encode_message(message: Command | Ack | Telemetry) -> bytes:
    """Serialize a message to one complete frame.

    Use compact JSON with no spaces to save link bandwidth. Drop fields
    that equal their default (``exclude_defaults``) for the same reason;
    the receiver fills them back in.
    """
    data = message.model_dump(mode="json", exclude_defaults=True)
    data["kind"] = message.kind  # Always present: the receiver dispatches on it.
    payload = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return encode_frame(payload)


def decode_message(payload: bytes) -> Command | Ack | Telemetry:
    """Parse one verified frame payload into a message model.

    Raise ``ValueError`` with a clear reason when the JSON or the fields
    are bad. The caller logs it and keeps the link running.
    """
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Frame is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Frame JSON is not an object")
    kind = data.get("kind")
    model = _KIND_TO_MODEL.get(kind) if isinstance(kind, str) else None
    if model is None:
        raise ValueError(f"Unknown message kind: {kind!r}")
    try:
        return model.model_validate(data)  # type: ignore[return-value]
    except ValidationError as exc:
        raise ValueError(f"Bad {kind} message: {exc.errors()[0]['msg']}") from exc
