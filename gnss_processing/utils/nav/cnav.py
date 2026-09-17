"""
GPS CNAV -- the modernised navigation message on L2C and L5.

One decoder serves both signals.  They differ only in how fast the symbols arrive
and therefore how long a message lasts:

    L2C   50 sps, 600 symbols per message, 12 seconds  (IS-GPS-200N 3.3.3.1)
    L5    100 sps, 600 symbols per message, 6 seconds  (IS-GPS-705J 20.3.2)

Everything else is shared: a 300-bit message, rate 1/2 convolutionally encoded and
protected by CRC-24Q, with a fixed 38-bit header.  The header is what makes CNAV
so much easier to synchronise than LNAV -- a preamble, the transmitting PRN, the
message type, and the time of week are all in the first 38 bits of *every*
message, so one valid CRC anywhere in the stream establishes time.

Header layout (IS-GPS-200N 30.3.3, identical in IS-GPS-705J 20.3.3):

    bits 1-8    preamble, always 10001011
    bits 9-14   PRN of the transmitting SV
    bits 15-20  message type, 0-63
    bits 21-37  message TOW count
    bit  38     alert flag
    bits 39-276 payload, per message type
    bits 277-300 CRC-24Q over bits 1-276

The TOW count needs care.  It is the 17 MSBs of the actual TOW count, and
multiplying it by 6 gives SV time at the start of the *next* message, not this
one.  `CnavMessage.tow_at_message_start_s` does that arithmetic once, correctly,
so no caller has to remember it.

What this module does not do is decide *which* symbols to feed it.  Symbol
extraction from tracking outputs lives in `utils.nav.symbols`; the two are kept
apart because the decoder is equally happy with symbols from a file, a simulator,
or a tracking channel.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import primitives as prim
from .ephemeris import CnavEphemeris

MESSAGE_BITS = 300
INFORMATION_BITS = 276
PREAMBLE_BITS = prim.bits_from_int(0b10001011, 8)
PREAMBLE_VALUE = 0b10001011

# One message is always 600 symbols; only its wall-clock duration differs.
SYMBOLS_PER_MESSAGE = 2 * MESSAGE_BITS
L2C_MESSAGE_DURATION_S = 12.0
L5_MESSAGE_DURATION_S = 6.0

MESSAGE_DURATION_S: dict[str, float] = {
    "GPS_L2C": L2C_MESSAGE_DURATION_S,
    "GPS_L5": L5_MESSAGE_DURATION_S,
}

# Klobuchar ionospheric coefficients share LNAV's scaling (IS-GPS-200N 30.3.3.3.1.2
# defers to Table 20-X), so message type 30's iono block is the familiar one.
_ALPHA_SCALES = (2.0**-30, 2.0**-27, 2.0**-24, 2.0**-24)
_BETA_SCALES = (2.0**11, 2.0**14, 2.0**16, 2.0**16)


@dataclass(frozen=True)
class CnavMessage:
    """One CRC-validated 300-bit CNAV message."""

    prn: int
    message_type: int
    tow_count: int
    alert: bool
    bits: np.ndarray
    symbol_index: int
    """Index into the symbol stream where this message's first symbol sits.  This
    is what ties a decoded time of week back to a tracking epoch."""

    message_duration_s: float

    @property
    def tow_at_next_message_start_s(self) -> float:
        """SV time at the start of the *next* message -- what the field literally
        means (IS-GPS-200N 30.3.3)."""
        return self.tow_count * 6.0

    @property
    def tow_at_message_start_s(self) -> float:
        """
        SV time at the start of *this* message, which is what a receiver actually
        wants: it is the epoch that `symbol_index` corresponds to.
        """
        return self.tow_at_next_message_start_s - self.message_duration_s

    @property
    def payload(self) -> np.ndarray:
        """Bits 39-276, the type-specific body."""
        return self.bits[38:INFORMATION_BITS]


@dataclass
class CnavDecodeResult:
    """Everything one pass over a symbol stream produced."""

    messages: list[CnavMessage] = field(default_factory=list)
    symbol_phase: int = 0
    """0 or 1 -- which symbol the G1/G2 pairing started on.  A tracking channel has
    no way to know this, so the decoder determines it."""

    inverted: bool = False
    """True when the data component arrived with its sign flipped.  Costas tracking
    leaves a 180 degree ambiguity that no amount of correlation resolves; the
    preamble and CRC do."""

    candidates_tried: int = 0
    bits: np.ndarray | None = None
    """The winning Viterbi bit stream, kept for inspection."""

    @property
    def synced(self) -> bool:
        return len(self.messages) > 0

    def of_type(self, message_type: int) -> list[CnavMessage]:
        return [m for m in self.messages if m.message_type == message_type]

    def tow_is_consistent(self) -> bool:
        """
        Successive messages must advance by exactly one message duration.

        A single CRC-valid message is already strong evidence -- 24 bits of CRC on a
        300-bit message -- but a receiver that has decoded several can check they
        form an arithmetic sequence, which catches a message assembled from the
        wrong alignment even if its CRC happened to pass.
        """
        if len(self.messages) < 2:
            return True
        for earlier, later in zip(self.messages, self.messages[1:]):
            expected = earlier.tow_count + round(
                (later.symbol_index - earlier.symbol_index)
                / SYMBOLS_PER_MESSAGE
                * earlier.message_duration_s
                / 6.0
            )
            if later.tow_count != expected:
                return False
        return True


def _find_messages(
    bits: np.ndarray, symbol_phase: int, message_duration_s: float
) -> list[CnavMessage]:
    """
    Scan a decoded bit stream for preamble-plus-valid-CRC message boundaries.

    Every bit offset is tried rather than only multiples of 300.  That costs a
    little more but it is what lets the decoder pick up a stream that starts
    part-way through a message, which is the normal case when tracking began at an
    arbitrary moment.
    """
    messages: list[CnavMessage] = []
    offset = 0
    limit = len(bits) - MESSAGE_BITS
    while offset <= limit:
        window = bits[offset : offset + MESSAGE_BITS]
        # Preamble first: it is 8 comparisons and rejects 255 of every 256 offsets
        # before the much more expensive CRC runs.
        if window[0] == 1 and np.array_equal(window[:8], PREAMBLE_BITS):
            if prim.crc24q_check(window):
                messages.append(
                    CnavMessage(
                        prn=int(prim.unpack_field(window, 9, 6)),
                        message_type=int(prim.unpack_field(window, 15, 6)),
                        tow_count=int(prim.unpack_field(window, 21, 17)),
                        alert=bool(window[37]),
                        bits=window.copy(),
                        symbol_index=2 * offset + symbol_phase,
                        message_duration_s=message_duration_s,
                    )
                )
                # Messages are contiguous, so the next one starts exactly 300 bits
                # on.  Skipping there also prevents a spurious in-message match.
                offset += MESSAGE_BITS
                continue
        offset += 1
    return messages


def decode(
    symbols: np.ndarray,
    *,
    message_duration_s: float = L5_MESSAGE_DURATION_S,
    expected_prn: int | None = None,
) -> CnavDecodeResult:
    """
    Decode every CNAV message present in a stream of soft symbols.

    `symbols` are soft values with positive meaning a transmitted 0, at whatever
    rate the signal uses -- the decoder never needs the rate, only
    `message_duration_s`, to turn a TOW count into a time.

    Four candidate interpretations are tried, because a tracking channel resolves
    neither of the two ambiguities that matter here:

      * **symbol phase** -- which symbol is a G1.  Tracking locks to the code, not
        to the encoder's pair boundary, so the stream may start on a G2.
      * **polarity** -- Costas tracking is invariant to a 180 degree phase flip, so
        the data may arrive inverted.

    The winner is whichever produces valid CRCs, and `expected_prn` tightens that
    further when the caller knows which satellite it tracked: a CRC-valid message
    carrying the wrong PRN means the alignment is wrong, not that the satellite
    lied.
    """
    symbols = np.asarray(symbols, dtype=np.float64)
    decoder = prim.ViterbiDecoder()
    best = CnavDecodeResult()

    for phase in (0, 1):
        aligned = symbols[phase:]
        # Viterbi needs whole G1/G2 pairs.
        aligned = aligned[: len(aligned) - (len(aligned) % 2)]
        if len(aligned) < 2 * MESSAGE_BITS:
            continue
        for inverted in (False, True):
            best.candidates_tried += 1
            decoded = decoder.decode(-aligned if inverted else aligned)
            messages = _find_messages(decoded.bits, phase, message_duration_s)
            if expected_prn is not None:
                messages = [m for m in messages if m.prn == expected_prn]
            if len(messages) > len(best.messages):
                best = CnavDecodeResult(
                    messages=messages,
                    symbol_phase=phase,
                    inverted=inverted,
                    candidates_tried=best.candidates_tried,
                    bits=decoded.bits,
                )
    return best


# ---------------------------------------------------------------------------
# Encoding
#
# No L2C or L5 collect in this repository carries a message we know the contents
# of in advance, so the only way to test the decoder against a known truth is to
# build the message ourselves.  These are the encoder halves of everything above.
# ---------------------------------------------------------------------------


def build_message(
    *,
    prn: int,
    message_type: int,
    tow_count: int,
    payload: np.ndarray | None = None,
    alert: bool = False,
) -> np.ndarray:
    """
    Assemble one 300-bit CNAV message: header, payload, and a correct CRC.

    `payload` is bits 39-276 (238 bits).  Omitting it fills the body with the
    alternating ones and zeros the spec prescribes for the default message type 0
    (IS-GPS-200N 30.3.2), which is also a usefully non-degenerate test pattern.
    """
    if not 0 <= prn < 64:
        raise ValueError(f"the PRN field is 6 bits; {prn} does not fit")
    if not 0 <= message_type < 64:
        raise ValueError(f"the message type field is 6 bits; {message_type} does not fit")
    if not 0 <= tow_count < (1 << 17):
        raise ValueError(f"the TOW count field is 17 bits; {tow_count} does not fit")

    body_length = INFORMATION_BITS - 38
    if payload is None:
        payload = np.array([(i + 1) % 2 for i in range(body_length)], dtype=np.uint8)
    payload = np.asarray(payload, dtype=np.uint8)
    if len(payload) != body_length:
        raise ValueError(f"payload is bits 39-276, i.e. {body_length} bits, got {len(payload)}")

    information = np.concatenate(
        [
            PREAMBLE_BITS,
            prim.bits_from_int(prn, 6),
            prim.bits_from_int(message_type, 6),
            prim.bits_from_int(tow_count, 17),
            np.array([1 if alert else 0], dtype=np.uint8),
            payload,
        ]
    )
    assert len(information) == INFORMATION_BITS
    return prim.crc24q_append(information)


def encode_stream(messages: list[np.ndarray], initial_state: int = 0) -> np.ndarray:
    """
    Convolutionally encode a run of messages into one continuous symbol stream.

    Continuity matters and is easy to get wrong: the encoder does not reset between
    messages, so the register carries the last six bits of one message into the
    next (IS-GPS-200N 3.3.3.1.1).  Encoding each message independently produces a
    stream that decodes correctly everywhere except the six bits either side of
    each boundary -- which is exactly where the preamble is, so the decoder would
    find nothing at all.
    """
    return prim.convolutional_encode(np.concatenate(messages), initial_state=initial_state)


def set_field(
    payload: np.ndarray, start: int, length: int, value: int
) -> np.ndarray:
    """
    Write one field into a *message* bit array, using the spec's 1-based numbering.

    Deliberately addressed the same way `primitives.unpack_field` reads, so a test
    can set a field and read it back through the real parser at the same bit
    numbers the spec prints.  `value` is the raw integer the field carries, before
    any scale factor -- two's complement values should be passed already wrapped
    into their unsigned representation.
    """
    out = np.asarray(payload, dtype=np.uint8).copy()
    out[start - 1 : start - 1 + length] = prim.bits_from_int(value & ((1 << length) - 1), length)
    return out


# ---------------------------------------------------------------------------
# Message type parsers
#
# Bit positions come from Figures 30-1, 30-2 and 30-3 of IS-GPS-200N; scale
# factors from Tables 30-I, 30-III and 30-IV.  Each field is written as
# (start, length, signed, scale) exactly as the spec states it, so a reader can
# check this against the figure line by line.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Type10:
    """Ephemeris part 1 -- the CEI reference times and the in-plane elements."""

    week: int
    health_l1: int
    health_l2: int
    health_l5: int
    top: float
    ura_ed_index: int
    toe: float
    delta_a: float
    a_dot: float
    delta_n0: float
    delta_n0_dot: float
    M0: float
    e: float
    omega: float
    integrity_status_flag: bool


@dataclass(frozen=True)
class Type11:
    """Ephemeris part 2 -- the orientation elements and harmonic corrections."""

    toe: float
    Omega0: float
    i0: float
    delta_Omega_dot: float
    i_dot: float
    Cis: float
    Cic: float
    Crs: float
    Crc: float
    Cus: float
    Cuc: float


@dataclass(frozen=True)
class Type30:
    """Clock correction, group delay, and the Klobuchar ionospheric model."""

    top: float
    ura_ned0_index: int
    ura_ned1_index: int
    ura_ned2_index: int
    toc: float
    af0: float
    af1: float
    af2: float
    tgd: float
    isc_l1ca: float
    isc_l2c: float
    isc_l5i5: float
    isc_l5q5: float
    alpha: tuple[float, float, float, float]
    beta: tuple[float, float, float, float]
    week_op: int


def parse_type_10(message: CnavMessage) -> Type10:
    if message.message_type != 10:
        raise ValueError(f"expected message type 10, got {message.message_type}")
    b = message.bits
    return Type10(
        week=int(prim.unpack_field(b, 39, 13)),
        health_l1=int(b[51]),
        health_l2=int(b[52]),
        health_l5=int(b[53]),
        top=prim.unpack_field(b, 55, 11, scale=300.0),
        ura_ed_index=int(prim.unpack_field(b, 66, 5, signed=True)),
        toe=prim.unpack_field(b, 71, 11, scale=300.0),
        delta_a=prim.unpack_field(b, 82, 26, signed=True, scale=2.0**-9),
        a_dot=prim.unpack_field(b, 108, 25, signed=True, scale=2.0**-21),
        delta_n0=prim.unpack_field(b, 133, 17, signed=True, scale=2.0**-44),
        delta_n0_dot=prim.unpack_field(b, 150, 23, signed=True, scale=2.0**-57),
        M0=prim.unpack_field(b, 173, 33, signed=True, scale=2.0**-32),
        # Eccentricity is the one unsigned element, and its scale differs from the
        # other 33-bit fields.
        e=prim.unpack_field(b, 206, 33, scale=2.0**-34),
        omega=prim.unpack_field(b, 239, 33, signed=True, scale=2.0**-32),
        integrity_status_flag=bool(b[271]),
    )


def parse_type_11(message: CnavMessage) -> Type11:
    if message.message_type != 11:
        raise ValueError(f"expected message type 11, got {message.message_type}")
    b = message.bits
    return Type11(
        toe=prim.unpack_field(b, 39, 11, scale=300.0),
        Omega0=prim.unpack_field(b, 50, 33, signed=True, scale=2.0**-32),
        i0=prim.unpack_field(b, 83, 33, signed=True, scale=2.0**-32),
        delta_Omega_dot=prim.unpack_field(b, 116, 17, signed=True, scale=2.0**-44),
        i_dot=prim.unpack_field(b, 133, 15, signed=True, scale=2.0**-44),
        Cis=prim.unpack_field(b, 148, 16, signed=True, scale=2.0**-30),
        Cic=prim.unpack_field(b, 164, 16, signed=True, scale=2.0**-30),
        Crs=prim.unpack_field(b, 180, 24, signed=True, scale=2.0**-8),
        Crc=prim.unpack_field(b, 204, 24, signed=True, scale=2.0**-8),
        Cus=prim.unpack_field(b, 228, 21, signed=True, scale=2.0**-30),
        Cuc=prim.unpack_field(b, 249, 21, signed=True, scale=2.0**-30),
    )


def parse_type_30(message: CnavMessage) -> Type30:
    if not 30 <= message.message_type <= 37:
        raise ValueError(
            f"clock parameters live in message types 30-37, got {message.message_type}"
        )
    b = message.bits
    # Bits 128-192 are the five group delay terms, 13 bits each at 2^-35 s
    # (IS-GPS-705J Table 20-IV); bits 193-256 the eight Klobuchar coefficients.
    isc = [
        prim.unpack_field(b, start, 13, signed=True, scale=2.0**-35)
        for start in (141, 154, 167, 180)
    ]
    alpha = tuple(
        prim.unpack_field(b, 193 + 8 * i, 8, signed=True, scale=_ALPHA_SCALES[i])
        for i in range(4)
    )
    beta = tuple(
        prim.unpack_field(b, 225 + 8 * i, 8, signed=True, scale=_BETA_SCALES[i])
        for i in range(4)
    )
    return Type30(
        top=prim.unpack_field(b, 39, 11, scale=300.0),
        ura_ned0_index=int(prim.unpack_field(b, 50, 5, signed=True)),
        ura_ned1_index=int(prim.unpack_field(b, 55, 3)),
        ura_ned2_index=int(prim.unpack_field(b, 58, 3)),
        toc=prim.unpack_field(b, 61, 11, scale=300.0),
        af0=prim.unpack_field(b, 72, 26, signed=True, scale=2.0**-35),
        af1=prim.unpack_field(b, 98, 20, signed=True, scale=2.0**-48),
        af2=prim.unpack_field(b, 118, 10, signed=True, scale=2.0**-60),
        tgd=prim.unpack_field(b, 128, 13, signed=True, scale=2.0**-35),
        isc_l1ca=isc[0],
        isc_l2c=isc[1],
        isc_l5i5=isc[2],
        isc_l5q5=isc[3],
        alpha=alpha,
        beta=beta,
        week_op=int(prim.unpack_field(b, 257, 8)),
    )


def assemble_ephemeris(
    result: CnavDecodeResult, sat_id: str | None = None
) -> CnavEphemeris | None:
    """
    Combine messages 10, 11 and a 30-series clock message into one ephemeris.

    Returns None when the pieces are not all present, or when they do not belong to
    the same CEI data set.  The spec's own consistency rule is that `toe` in
    messages 10 and 11 and `toc` in the clock message must match (IS-GPS-200N
    30.3.3.1.1); mismatched values mean a data set cutover happened between them
    and mixing the halves would produce a plausible, wrong orbit.

    The clock message is optional in the sense that an ephemeris without it still
    positions the satellite -- but it cannot correct the satellite clock, and a
    pseudorange without that correction is wrong by tens of kilometres.  So it is
    required here, and its absence returns None rather than a half-usable object.
    """
    tens = result.of_type(10)
    elevens = result.of_type(11)
    clocks = [m for m in result.messages if 30 <= m.message_type <= 37]
    if not (tens and elevens and clocks):
        return None

    # Use the most recent of each -- a long track may carry a data set cutover.
    t10 = parse_type_10(tens[-1])
    t11 = parse_type_11(elevens[-1])
    t30 = parse_type_30(clocks[-1])

    if not (t10.toe == t11.toe == t30.toc):
        return None

    if sat_id is None:
        sat_id = f"G{tens[-1].prn:02d}"

    return CnavEphemeris(
        sat_id=sat_id,
        week=t10.week,
        toe=t10.toe,
        top=t10.top,
        delta_a=t10.delta_a,
        a_dot=t10.a_dot,
        delta_n0=t10.delta_n0,
        delta_n0_dot=t10.delta_n0_dot,
        M0=t10.M0,
        e=t10.e,
        omega=t10.omega,
        Omega0=t11.Omega0,
        delta_Omega_dot=t11.delta_Omega_dot,
        i0=t11.i0,
        i_dot=t11.i_dot,
        Cis=t11.Cis,
        Cic=t11.Cic,
        Crs=t11.Crs,
        Crc=t11.Crc,
        Cus=t11.Cus,
        Cuc=t11.Cuc,
        toc=t30.toc,
        af0=t30.af0,
        af1=t30.af1,
        af2=t30.af2,
        tgd=t30.tgd,
        isc_l5i5=t30.isc_l5i5,
        isc_l5q5=t30.isc_l5q5,
        isc_l1ca=t30.isc_l1ca,
        isc_l2c=t30.isc_l2c,
        health_l1=t10.health_l1,
        health_l2=t10.health_l2,
        health_l5=t10.health_l5,
    )
