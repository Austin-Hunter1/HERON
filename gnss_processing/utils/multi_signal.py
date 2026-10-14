"""
Preparing BeiDou B2a tracking for navigation, next to GPS.

Notebook 02 decodes a tracking run in four steps: turn the tracked epochs into symbols,
find the message in them, anchor the satellite's code phase to a time of week, and
assemble an ephemeris.  For GPS that is `cnav.decode` and a RINEX download.  For B2a
the signal carries its own ephemeris, so one function does all four, per channel or for
a whole run.  It lives here, not in the notebook, because the single-signal flow and the
combined GPS + BeiDou flow (D-058) both need it, and because it can be tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import observables
from .nav import bcnav2
from .nav import symbols as nav_symbols
from .nav.ephemeris import BdsEphemeris

SIGNAL_TYPE_ID = "BDS_B2A"


@dataclass
class DecodedChannel:
    """One tracked satellite after decoding.  `failure` says why it has no anchor."""

    stream: object
    result: bcnav2.Bcnav2DecodeResult | None = None
    ephemeris: BdsEphemeris | None = None
    anchor: observables.TimeAnchor | None = None
    anchor_uptime_ms: float | None = None
    failure: str | None = None


def decode_bcnav2_channel(outputs, sat_id: str) -> DecodedChannel:
    """
    Symbols, frames, ephemeris and time anchor for one B2a channel.

    A channel is usable only when it has an anchor *and* a matched ephemeris (types
    10, 11 and 30, D-055).  Without the ephemeris it cannot be placed in the sky, so
    it is reported as a failure and gets no anchor.
    """
    stream = nav_symbols.extract(outputs, SIGNAL_TYPE_ID)
    if not len(stream):
        return DecodedChannel(stream, failure=f"{sat_id} (never reached the state that makes epochs symbols)")
    result = bcnav2.decode(stream.soft, expected_prn=int(sat_id[1:]))
    if not result.synced:
        return DecodedChannel(stream, result, failure=f"{sat_id} ({len(stream):,} symbols, no frame survived the CRC)")
    ephemeris = bcnav2.assemble_ephemeris(result, sat_id=sat_id)
    if ephemeris is None:
        return DecodedChannel(
            stream, result,
            failure=f"{sat_id} ({len(result.frames)} frames, but no matched type 10 + 11 + 30 set yet)",
        )
    frame = result.frames[0]
    return DecodedChannel(
        stream, result, ephemeris,
        anchor=observables.anchor_from_bcnav2(stream, frame, week=ephemeris.week),
        anchor_uptime_ms=float(stream.uptime_ms[frame.symbol_index]),
    )


@dataclass
class DecodedRun:
    """A whole B2a tracking run, decoded."""

    channels: dict[str, DecodedChannel] = field(default_factory=dict)

    @property
    def anchors(self) -> dict[str, observables.TimeAnchor]:
        return {s: c.anchor for s, c in self.channels.items() if c.anchor is not None}

    @property
    def ephemerides(self) -> dict[str, BdsEphemeris]:
        return {s: c.ephemeris for s, c in self.channels.items() if c.anchor is not None}

    @property
    def anchor_uptime_ms(self) -> dict[str, float]:
        return {s: c.anchor_uptime_ms for s, c in self.channels.items() if c.anchor is not None}

    @property
    def failures(self) -> list[str]:
        return [c.failure for c in self.channels.values() if c.failure]


def decode_bcnav2_run(run) -> DecodedRun:
    """`decode_bcnav2_channel` for every channel of a `TrackingRun`."""
    return DecodedRun({sid: decode_bcnav2_channel(run[sid].outputs, sid) for sid in run.signal_ids})
