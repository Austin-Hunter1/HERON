"""
Preparing BeiDou B2a and B1C tracking for navigation, next to GPS.

`decode_gps_run` does the same for the GPS signals (L1 C/A and L5), so the combined
flow can take any of the four runs through one call each (D-073).

Notebook 02 decodes a tracking run in four steps: turn the tracked epochs into symbols,
find the message in them, anchor the satellite's code phase to a time of week, and
assemble an ephemeris.  For GPS that is `cnav.decode` and a RINEX download.  For B2a
(B-CNAV2) and B1C (B-CNAV1) the signal carries its own ephemeris, so one function does
all four, per channel or for a whole run.  It lives here, not in the notebook, because the single-signal flow and the
combined GPS + BeiDou flow (D-058) both need it, and because it can be tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import broadcast_ephemeris, observables
from .nav import bcnav1, bcnav2, cnav, lnav
from .nav import symbols as nav_symbols
from .nav.ephemeris import BdsEphemeris, Ephemeris

SIGNAL_TYPE_ID = "BDS_B2A"
B1C_SIGNAL_TYPE_ID = "BDS_B1C"
GPS_L1CA_SIGNAL_TYPE_ID = "GPS_L1CA"
GPS_L5_SIGNAL_TYPE_ID = "GPS_L5"


@dataclass
class DecodedChannel:
    """One tracked satellite after decoding.  `failure` says why it has no anchor."""

    stream: object
    result: bcnav2.Bcnav2DecodeResult | bcnav1.Bcnav1DecodeResult | None = None
    ephemeris: Ephemeris | None = None
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


def decode_bcnav1_channel(outputs, sat_id: str) -> DecodedChannel:
    """
    Symbols, frames, ephemeris and time anchor for one B1C channel.

    One B-CNAV1 frame holds the whole ephemeris and the clock in its subframe 2, so a
    channel is usable as soon as one frame passes its CRC and its IODE matches its
    IODC.  A frame is 18 s long, so a channel needs about 40 s of tracking: the
    overlay search, the pull-in, and then one whole frame at the right place.
    """
    stream = nav_symbols.extract(outputs, B1C_SIGNAL_TYPE_ID)
    if not len(stream):
        return DecodedChannel(stream, failure=f"{sat_id} (never reached the state that makes epochs symbols)")
    result = bcnav1.decode(stream.soft, expected_prn=int(sat_id[1:]))
    if not result.synced:
        return DecodedChannel(stream, result, failure=f"{sat_id} ({len(stream):,} symbols, no frame survived the CRC)")
    ephemeris = bcnav1.assemble_ephemeris(result, sat_id=sat_id)
    if ephemeris is None:
        return DecodedChannel(
            stream, result,
            failure=f"{sat_id} ({len(result.frames)} frames, but IODE never matched the IODC low byte)",
        )
    frame = result.frames[0]
    return DecodedChannel(
        stream, result, ephemeris,
        anchor=observables.anchor_from_bcnav1(stream, frame),
        anchor_uptime_ms=float(stream.uptime_ms[frame.symbol_index]),
    )


def decode_beidou_channel(outputs, sat_id: str, signal_type_id: str) -> DecodedChannel:
    """`decode_bcnav2_channel` or `decode_bcnav1_channel`, by the signal the channel tracked."""
    if signal_type_id == SIGNAL_TYPE_ID:
        return decode_bcnav2_channel(outputs, sat_id)
    if signal_type_id == B1C_SIGNAL_TYPE_ID:
        return decode_bcnav1_channel(outputs, sat_id)
    raise ValueError(f"{signal_type_id!r} is not a BeiDou signal with a decoder here")


@dataclass
class DecodedRun:
    """A whole BeiDou tracking run (B2a or B1C), decoded."""

    channels: dict[str, DecodedChannel] = field(default_factory=dict)

    @property
    def anchors(self) -> dict[str, observables.TimeAnchor]:
        return {s: c.anchor for s, c in self.channels.items() if c.anchor is not None}

    @property
    def ephemerides(self) -> dict[str, Ephemeris]:
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


def decode_bcnav1_run(run) -> DecodedRun:
    """`decode_bcnav1_channel` for every channel of a `TrackingRun`."""
    return DecodedRun({sid: decode_bcnav1_channel(run[sid].outputs, sid) for sid in run.signal_ids})


def decode_beidou_run(run, signal_type_id: str) -> DecodedRun:
    """The decoder that matches `signal_type_id`, for every channel of a `TrackingRun`."""
    return DecodedRun(
        {sid: decode_beidou_channel(run[sid].outputs, sid, signal_type_id) for sid in run.signal_ids}
    )


def decode_gps_channel(outputs, sat_id: str, signal_type_id: str) -> DecodedChannel:
    """
    Symbols, message and time anchor for one GPS L1 C/A or L5 channel.

    The anchor is the first subframe (LNAV) or message (CNAV) that passes its parity
    or CRC check; both are self-timing.  GPS carries no ephemeris that suits this
    flow, so `ephemeris` stays empty here and `decode_gps_run` fills it from the
    broadcast file.
    """
    stream = nav_symbols.extract(outputs, signal_type_id)
    if not len(stream):
        return DecodedChannel(stream, failure=f"{sat_id} (never reached the state that makes epochs symbols)")
    if signal_type_id == GPS_L1CA_SIGNAL_TYPE_ID:
        result = lnav.decode(stream.soft)
        if not result.synced:
            return DecodedChannel(stream, result, failure=f"{sat_id} ({len(stream):,} symbols, no subframe passed parity)")
        first = result.subframes[0]
        anchor = observables.anchor_from_lnav(stream, first, sat_id=sat_id)
    elif signal_type_id == GPS_L5_SIGNAL_TYPE_ID:
        result = cnav.decode(
            stream.soft,
            message_duration_s=cnav.MESSAGE_DURATION_S[signal_type_id],
            expected_prn=int(sat_id[1:]),
        )
        if not result.synced:
            return DecodedChannel(stream, result, failure=f"{sat_id} ({len(stream):,} symbols, none survived the CRC)")
        first = result.messages[0]
        anchor = observables.anchor_from_cnav(stream, first, sat_id=sat_id)
    else:
        raise ValueError(f"{signal_type_id!r} is not a GPS signal with a decoder here")
    return DecodedChannel(
        stream, result, anchor=anchor, anchor_uptime_ms=float(stream.uptime_ms[first.symbol_index])
    )


def decode_gps_run(
    run,
    signal_type_id: str,
    *,
    reference_tow: float,
    week: int,
    epoch_datetime,
    records,
) -> DecodedRun:
    """
    `decode_gps_channel` for every channel of a `TrackingRun`, plus ephemerides.

    The ephemerides come from the broadcast file's `records`, chosen for
    `reference_tow` in `week`, as the single-signal flow does.  A channel with an anchor
    but no ephemeris cannot be placed in the sky, so it loses its anchor and is
    reported as a failure.  `reference_tow` may come from another run of the same
    collect: the ephemeris is picked by the nearest `toe`, and a few seconds between
    two streams change nothing there.
    """
    channels = {sid: decode_gps_channel(run[sid].outputs, sid, signal_type_id) for sid in run.signal_ids}
    anchored = sorted(sid for sid, channel in channels.items() if channel.anchor is not None)
    ephemerides = broadcast_ephemeris.ephemerides_for(
        anchored, reference_tow, week, epoch_datetime, records=records, verbose=False
    )
    for sid in anchored:
        eph = ephemerides.get(sid)
        if eph is None:
            channels[sid].failure = f"{sid} (decoded, but the broadcast file has no ephemeris for it)"
            channels[sid].anchor = None
        else:
            channels[sid].ephemeris = eph
    return DecodedRun(channels)
