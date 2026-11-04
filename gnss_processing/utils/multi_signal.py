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
import numpy as np

from .nav import bcnav1, bcnav2, cnav, cnav2, fnav, lnav
from .nav import symbols as nav_symbols
from .nav.ephemeris import BdsEphemeris, Ephemeris

SIGNAL_TYPE_ID = "BDS_B2A"
B1C_SIGNAL_TYPE_ID = "BDS_B1C"
GPS_L1CA_SIGNAL_TYPE_ID = "GPS_L1CA"
GPS_L5_SIGNAL_TYPE_ID = "GPS_L5"
L1C_SIGNAL_TYPE_ID = "GPS_L1C"
E5A_SIGNAL_TYPE_ID = "GAL_E5A"


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


@dataclass
class Cnav2Decode:
    """What the CNAV-2 decoder found in one symbol stream, in the shape notebook 02 prints."""

    frames: list[cnav2.Cnav2Frame]
    subframes_2: list[cnav2.Subframe2 | None]
    inverted: bool
    """True when the symbols had to be negated for subframe 2 to pass its CRC."""

    @property
    def synced(self) -> bool:
        return any(sf is not None for sf in self.subframes_2)

    def sow_is_consistent(self) -> bool:
        """Successive TOIs step by one frame (the check notebook 02 calls by this name)."""
        return cnav2.toi_is_consistent(self.frames)

    @property
    def page_ids(self) -> list[int]:
        return []


# How many candidate frame starts the CRC is tried at.  The TOI correlation ranks the
# 1800 offsets; the true one is nearly always first, so a few are enough.
CNAV2_FRAME_CANDIDATES = 5


def _decode_cnav2_polarity(soft: np.ndarray, sign: float, offsets):
    """Frames, subframes 2 and the number of CRC passes for one symbol polarity."""
    best: tuple[int, list, list] = (0, [], [])
    for offset in offsets:
        frames = cnav2.decode(sign * soft, frame_offset=int(offset))
        subframes = [cnav2.decode_subframe_2(f) for f in frames]
        # Only an upright pass counts: a subframe that passes the CRC only after being
        # flipped means THIS polarity is the wrong one, and its TOI is wrong with it.
        passes = sum(sf is not None and not sf.inverted for sf in subframes)
        if passes > best[0]:
            best = (passes, frames, subframes)
    return best[1], best[2], best[0]


def decode_cnav2_stream(stream, sat_id: str) -> DecodedChannel:
    """
    Symbols, frames, ephemeris and time anchor for one L1C stream.

    One frame's subframe 2 holds the whole ephemeris and the clock, so a satellite is
    usable once one subframe 2 passes its CRC AND the frame before it has a valid TOI:
    the start of frame k is ``ITOW(k) * 7200 + TOI(k-1) * 18`` (the TOI names the NEXT
    frame).  So the first anchor needs two whole frames after the frame search, about
    36 s of symbols plus up to 18 s to the first frame start.

    Polarity: the data axis sign is not known.  Both signs are decoded and the one whose
    subframe 2 passes the CRC without a flip wins, because the TOI's top bit is its sign
    and a wrong polarity would give a wrong TOI.
    """
    soft = np.asarray(stream.soft, dtype=float)
    if len(soft) < cnav2.FRAME_SYMBOLS + cnav2.SUBFRAME_1_SYMBOLS:
        return DecodedChannel(
            stream, Cnav2Decode([], [], False),
            failure=f"{sat_id} ({len(soft):,} symbols, under one frame plus a TOI)",
        )
    offsets = cnav2.rank_frame_offsets(soft, CNAV2_FRAME_CANDIDATES)
    candidates = [(sign, *_decode_cnav2_polarity(soft, sign, offsets)) for sign in (1.0, -1.0)]
    sign, frames, subframes, passes = max(candidates, key=lambda c: c[3])
    result = Cnav2Decode(frames, subframes, inverted=sign < 0)
    if passes == 0:
        return DecodedChannel(
            stream, result, failure=f"{sat_id} ({len(stream):,} symbols, no subframe 2 survived the CRC)"
        )
    for k in range(1, len(frames)):
        sf2 = subframes[k]
        if sf2 is not None and frames[k - 1].toi_is_valid:
            tow_s = cnav2.frame_start_tow_s(frames[k - 1], sf2)
            symbol_index = frames[k].symbol_index
            ephemeris = cnav2.assemble_ephemeris(sf2, sat_id)
            return DecodedChannel(
                stream, result, ephemeris,
                anchor=observables.anchor_from_cnav2(
                    stream, symbol_index, tow_s, int(sf2["wn"]), sat_id
                ),
                anchor_uptime_ms=float(stream.uptime_ms[symbol_index]),
            )
    return DecodedChannel(
        stream, result,
        failure=f"{sat_id} ({passes} subframe 2 passed, but none follows a frame with a valid TOI)",
    )


def decode_cnav2_channel(outputs, sat_id: str) -> DecodedChannel:
    """`decode_cnav2_stream` for the symbols of one tracked L1C channel."""
    stream = nav_symbols.extract(outputs, L1C_SIGNAL_TYPE_ID)
    if not len(stream):
        return DecodedChannel(stream, failure=f"{sat_id} (never reached the state that makes epochs symbols)")
    return decode_cnav2_stream(stream, sat_id)


def decode_fnav_stream(stream, sat_id: str) -> DecodedChannel:
    """
    Pages, ephemeris and time anchor for one E5a stream (50 sps).

    The ephemeris needs one page each of types 1, 2, 3 and 4 with the same IOD_nav; the
    four pages are 40 s of transmission and can arrive in any rotation, so the first
    complete set can take up to 60 s of tracking after the pull-in.  The anchor is the
    first page 1: its time of week names the page's first symbol.
    """
    result = fnav.decode(stream.soft)
    if not result.synced:
        return DecodedChannel(
            stream, result, failure=f"{sat_id} ({len(stream):,} symbols, no page survived the CRC)"
        )
    ephemeris = fnav.assemble_ephemeris(result, sat_id)
    if ephemeris is None:
        return DecodedChannel(
            stream, result,
            failure=f"{sat_id} ({len(result.pages)} pages, but no set of pages 1-4 with one IOD_nav yet)",
        )
    page = result.of_type(1)[0]
    return DecodedChannel(
        stream, result, ephemeris,
        anchor=observables.anchor_from_fnav(stream, page, sat_id),
        anchor_uptime_ms=float(stream.uptime_ms[page.symbol_index]),
    )


def decode_fnav_channel(outputs, sat_id: str) -> DecodedChannel:
    """`decode_fnav_stream` for the symbols of one tracked E5a channel."""
    stream = nav_symbols.extract(outputs, E5A_SIGNAL_TYPE_ID)
    if not len(stream):
        return DecodedChannel(stream, failure=f"{sat_id} (never reached the state that makes epochs symbols)")
    return decode_fnav_stream(stream, sat_id)


def decode_beidou_channel(outputs, sat_id: str, signal_type_id: str) -> DecodedChannel:
    """`decode_bcnav2_channel` or `decode_bcnav1_channel`, by the signal the channel tracked."""
    if signal_type_id == SIGNAL_TYPE_ID:
        return decode_bcnav2_channel(outputs, sat_id)
    if signal_type_id == B1C_SIGNAL_TYPE_ID:
        return decode_bcnav1_channel(outputs, sat_id)
    if signal_type_id == E5A_SIGNAL_TYPE_ID:
        return decode_fnav_channel(outputs, sat_id)
    if signal_type_id == L1C_SIGNAL_TYPE_ID:
        # Not BeiDou, but the same shape: the signal carries its own ephemeris.
        return decode_cnav2_channel(outputs, sat_id)
    raise ValueError(f"{signal_type_id!r} is not a signal that carries its own ephemeris here")


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
