"""
BeiDou B-CNAV2 -- the navigation message on B2a.

One **frame** is 600 symbols at 200 sps, so 3 seconds (ICD 6.2.1):

    symbols   1-24    preamble 0xE24DE8, MSB first
    symbols  25-600   576 symbols = one 64-ary LDPC(96,48) codeword

The codeword holds 288 bits, which are, before coding:

    bits   1-6    PRN of the transmitting satellite
    bits   7-12   message type (10, 11, 30, ...)
    bits  13-30   SOW count: seconds of week / 3
    bits  31-264  message data, per message type (234 bits)
    bits 265-288  CRC-24Q over bits 1-264

Compared with GPS CNAV this module has two jobs that CNAV does not.  The frame
boundary is found from the **preamble**, which is outside the error-correcting
code, so it is a plain correlation on the soft symbols, and polarity comes out of
it.  The LDPC decode (`utils.nav.ldpc`) then takes the 576 soft symbols behind it.
A frame is accepted only when the CRC is good -- the LDPC decoder reaching a
codeword is not enough, because it can settle on the wrong one.

Time (ICD 7.3).  SOW names the rising edge of the first preamble chip of its own
frame -- unlike GPS CNAV, whose TOW count names the *next* message.  So the time at
`symbol_index` is simply ``sow_count * 3`` s.  The time system is BDT, which has no
leap seconds and runs 14 s behind GPST; the week number is in message type 10 only.

Type 10 and 11 together are the ephemeris.  The ICD (7.4.3) pairs the ephemeris with
the clock of type 30 by ``IODE == IODC & 0xFF``; `iode_matches_iodc` does that
check.  The ICD gives no key for pairing a type 11 frame with its type 10 frame; the
rule used here is the user's (D-055): the two frames are next to each other, 600
symbols apart, in either order.  `assemble_ephemeris` applies both rules.

Soft symbols follow `utils.nav.primitives`: the sign carries the bit, positive for 0.
Only message types 10, 11 and 30 are parsed so far.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import ldpc
from . import primitives as prim
from .cnav import set_field
from .ephemeris import BdsEphemeris

PREAMBLE_VALUE = 0xE24DE8
PREAMBLE_SYMBOLS = 24
PREAMBLE_BITS = prim.bits_from_int(PREAMBLE_VALUE, PREAMBLE_SYMBOLS)

CODEWORD_SYMBOLS = 576  # LDPC symbols after the preamble, one bit each
FRAME_SYMBOLS = PREAMBLE_SYMBOLS + CODEWORD_SYMBOLS  # 600
MESSAGE_BITS = 288  # before LDPC coding
INFORMATION_BITS = MESSAGE_BITS - 24  # the part the CRC covers: 264
DATA_BITS = INFORMATION_BITS - 30  # after PRN, type and SOW: 234

SYMBOL_PERIOD_MS = 5
FRAME_DURATION_S = 3.0
SOW_SCALE_S = 3.0
SOW_COUNT_MAX = 201_599  # 604797 s / 3: the last valid count in a week

MESSAGE_TYPES = (10, 11, 30, 31, 32, 33, 34, 40)

# Bit numbers below are 1-based positions in the 288-bit message, the way the ICD
# numbers them -- see `primitives.unpack_field`.
_PRN = (1, 6)
_MESSAGE_TYPE = (7, 6)
_SOW = (13, 18)


# ---------------------------------------------------------------------------
# Frames and decoding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Bcnav2Frame:
    """One CRC-validated B-CNAV2 frame."""

    prn: int
    message_type: int
    sow_count: int
    bits: np.ndarray
    """The 288 decoded message bits, in order."""

    symbol_index: int
    """Where this frame's first preamble symbol sits in the symbol stream.  This is
    what ties a decoded time back to a tracking epoch."""

    inverted: bool
    """True when the symbols arrived with their sign flipped (a 180 degree carrier
    ambiguity that only the preamble can resolve)."""

    ldpc_iterations: int
    symbols_corrected: int
    """LDPC symbols that the decoder changed from the plain hard decision."""

    @property
    def sow_is_valid(self) -> bool:
        return 0 <= self.sow_count <= SOW_COUNT_MAX

    @property
    def sow_at_frame_start_s(self) -> float:
        """Seconds of BDT week at the first preamble chip, which is `symbol_index`."""
        return self.sow_count * SOW_SCALE_S

    @property
    def payload(self) -> np.ndarray:
        """The 234 message-data bits (bits 31-264)."""
        return self.bits[30:INFORMATION_BITS]


@dataclass
class Bcnav2DecodeResult:
    """Everything one pass over a symbol stream produced."""

    frames: list[Bcnav2Frame] = field(default_factory=list)
    candidates_tried: int = 0
    """Preamble candidates that went on to an LDPC decode."""

    @property
    def synced(self) -> bool:
        return len(self.frames) > 0

    @property
    def inverted(self) -> bool:
        """Whether the first frame arrived upside down (the same for every frame of a
        run, since one carrier loop produced them).  False when nothing decoded."""
        return bool(self.frames and self.frames[0].inverted)

    def of_type(self, message_type: int) -> list[Bcnav2Frame]:
        return [f for f in self.frames if f.message_type == message_type]

    def sow_is_consistent(self) -> bool:
        """
        Successive frames must advance the SOW count by one per 600 symbols.

        One CRC-valid frame is already strong evidence (24 CRC bits on top of the
        LDPC code), but a run of frames that steps by exactly one frame duration
        catches a frame taken from the wrong alignment whose CRC passed by chance.
        The count wraps at the end of the week.
        """
        week_counts = SOW_COUNT_MAX + 1
        for earlier, later in zip(self.frames, self.frames[1:]):
            steps = round((later.symbol_index - earlier.symbol_index) / FRAME_SYMBOLS)
            if (earlier.sow_count + steps) % week_counts != later.sow_count:
                return False
        return True


def _preamble_candidates(symbols: np.ndarray, min_agreement: int) -> list[tuple[int, bool]]:
    """
    Offsets where the hard symbols look like the preamble, in either polarity.

    Returns ``(offset, inverted)`` pairs in stream order.  `min_agreement` is how
    many of the 24 symbols must match (normal) or mismatch (inverted).  It is
    deliberately below 24: a noisy preamble should still be tried, because the
    LDPC and the CRC are the real test, and a threshold that is too tight drops
    frames that would have decoded.
    """
    if len(symbols) < PREAMBLE_SYMBOLS:
        return []
    hard = (symbols < 0).astype(np.uint8)
    windows = np.lib.stride_tricks.sliding_window_view(hard, PREAMBLE_SYMBOLS)
    agreement = (windows == PREAMBLE_BITS[None, :]).sum(axis=1)
    candidates = []
    for offset in np.nonzero((agreement >= min_agreement) | (agreement <= PREAMBLE_SYMBOLS - min_agreement))[0]:
        candidates.append((int(offset), bool(agreement[offset] <= PREAMBLE_SYMBOLS - min_agreement)))
    return candidates


def decode(
    symbols: np.ndarray,
    *,
    expected_prn: int | None = None,
    preamble_min_agreement: int = 20,
    max_ldpc_iterations: int = 50,
) -> Bcnav2DecodeResult:
    """
    Decode every B-CNAV2 frame present in a stream of 200 sps soft symbols.

    `symbols` are soft values, positive for a transmitted 0, one per data symbol --
    exactly what `utils.nav.symbols.extract` returns as `SymbolStream.soft`.

    For each preamble candidate the 576 symbols behind it are made the right way up
    and LDPC-decoded, and the frame is kept only if the CRC passes.  A frame that
    fails is not skipped over: the search moves on one symbol, because the failure
    may be a false preamble inside a good frame.  A frame that succeeds is skipped
    in full, since frames are contiguous.

    `expected_prn` drops a CRC-valid frame that carries another satellite's PRN.
    With it, a frame that passes the CRC by chance is still rejected, and so is a
    channel that locked onto the wrong satellite.
    """
    symbols = np.asarray(symbols, dtype=np.float64)
    result = Bcnav2DecodeResult()
    next_allowed = 0
    for offset, inverted in _preamble_candidates(symbols, preamble_min_agreement):
        if offset < next_allowed or offset + FRAME_SYMBOLS > len(symbols):
            continue
        result.candidates_tried += 1
        codeword = symbols[offset + PREAMBLE_SYMBOLS : offset + FRAME_SYMBOLS]
        decoded = ldpc.decode(-codeword if inverted else codeword, max_iterations=max_ldpc_iterations)
        if not decoded.converged:
            continue
        bits = decoded.message_bits
        if not prim.crc24q_check(bits):
            continue
        frame = Bcnav2Frame(
            prn=int(prim.unpack_field(bits, *_PRN)),
            message_type=int(prim.unpack_field(bits, *_MESSAGE_TYPE)),
            sow_count=int(prim.unpack_field(bits, *_SOW)),
            bits=bits,
            symbol_index=offset,
            inverted=inverted,
            ldpc_iterations=decoded.iterations,
            symbols_corrected=decoded.symbols_changed,
        )
        if expected_prn is not None and frame.prn != expected_prn:
            continue
        result.frames.append(frame)
        next_allowed = offset + FRAME_SYMBOLS
    return result


# ---------------------------------------------------------------------------
# Encoding
#
# No B2a recording in this repository carries a message whose contents we know in
# advance, so the only way to test the decoder against a known truth is to build
# the frame ourselves.  These are the encoder halves of everything above.
# ---------------------------------------------------------------------------


def build_message(
    *,
    prn: int,
    message_type: int,
    sow_count: int,
    payload: np.ndarray | None = None,
) -> np.ndarray:
    """
    Assemble the 288 message bits: PRN, type, SOW, 234 data bits, and the CRC.

    `payload` is bits 31-264.  Omitting it fills the data with alternating ones and
    zeros, which is a usefully non-degenerate test pattern.
    """
    if not 1 <= prn <= 63:
        raise ValueError(f"the PRN is 1 to 63; {prn} is not valid")
    if not 0 <= message_type < 64:
        raise ValueError(f"the message type field is 6 bits; {message_type} does not fit")
    if not 0 <= sow_count < (1 << 18):
        raise ValueError(f"the SOW field is 18 bits; {sow_count} does not fit")
    if payload is None:
        payload = np.array([(i + 1) % 2 for i in range(DATA_BITS)], dtype=np.uint8)
    payload = np.asarray(payload, dtype=np.uint8)
    if len(payload) != DATA_BITS:
        raise ValueError(f"payload is bits 31-264, i.e. {DATA_BITS} bits, got {len(payload)}")
    information = np.concatenate(
        [
            prim.bits_from_int(prn, 6),
            prim.bits_from_int(message_type, 6),
            prim.bits_from_int(sow_count, 18),
            payload,
        ]
    )
    return prim.crc24q_append(information)


def encode_frame(message_bits: np.ndarray) -> np.ndarray:
    """
    The 600 transmitted bits of one frame: the preamble and the LDPC codeword.

    The result is bits 0/1; use `soft_symbols` for the +1/-1 form the decoder takes.
    """
    message_bits = np.asarray(message_bits, dtype=np.uint8)
    if len(message_bits) != MESSAGE_BITS:
        raise ValueError(f"a message is {MESSAGE_BITS} bits, got {len(message_bits)}")
    codeword = ldpc.encode(ldpc.symbols_from_bits(message_bits))
    return np.concatenate([PREAMBLE_BITS, ldpc.bits_from_symbols(codeword)])


def soft_symbols(bits: np.ndarray) -> np.ndarray:
    """Bits as noiseless soft symbols: 0 becomes +1 and 1 becomes -1."""
    return 1.0 - 2.0 * np.asarray(bits, dtype=np.float64)


# ---------------------------------------------------------------------------
# Message parsers
#
# Each layout is a list of (name, length, signed, scale) in transmission order,
# transcribed from ICD Figures 6-3 to 6-5 and 6-11 to 6-13, with scale factors from
# Tables 7-2, 7-5, 7-6, 7-8 and 7-10 (checked against the page images).  Start bits
# are summed from the lengths, and the total is checked against the 234 data bits,
# so a missing or extra bit cannot go unnoticed.
# ---------------------------------------------------------------------------

_Field = tuple[str, int, bool, float]


def _layout_fields(layout: list[_Field], first_bit: int) -> list[tuple[str, int, int, bool, float]]:
    fields, start = [], first_bit
    for name, length, signed, scale in layout:
        fields.append((name, start, length, signed, scale))
        start += length
    return fields


def _unpack_layout(bits: np.ndarray, layout: list[_Field], first_bit: int) -> dict[str, float | int]:
    return {
        name: prim.unpack_field(bits, start, length, signed=signed, scale=scale)
        for name, start, length, signed, scale in _layout_fields(layout, first_bit)
    }


def _layout_bits(layout: list[_Field]) -> int:
    return sum(length for _, length, _, _ in layout)


# Integrity flags: DIF, SIF, AIF for B2a, then SISMAI (4 bits), then the same three
# flags for B1C.  Types 11 and 30 put the 2-bit health status in front of them.
_FLAGS: list[_Field] = [
    ("dif_b2a", 1, False, 1.0), ("sif_b2a", 1, False, 1.0), ("aif_b2a", 1, False, 1.0),
    ("sismai", 4, False, 1.0),
    ("dif_b1c", 1, False, 1.0), ("sif_b1c", 1, False, 1.0), ("aif_b1c", 1, False, 1.0),
]

_TYPE_10_HEAD: list[_Field] = [("wn", 13, False, 1.0)] + _FLAGS + [("iode", 8, False, 1.0)]
_EPHEMERIS_I: list[_Field] = [
    ("toe_s", 11, False, 300.0),
    ("sat_type", 2, False, 1.0),
    ("delta_a_m", 26, True, 2.0**-9),
    ("a_dot_mps", 25, True, 2.0**-21),
    ("delta_n0_pi_per_s", 17, True, 2.0**-44),
    ("delta_n0_dot_pi_per_s2", 23, True, 2.0**-57),
    ("m0_pi", 33, True, 2.0**-32),
    # Eccentricity is the one unsigned 33-bit element, with its own scale.
    ("e", 33, False, 2.0**-34),
    ("omega_pi", 33, True, 2.0**-32),
]
_TYPE_11_HEAD: list[_Field] = [("hs", 2, False, 1.0)] + _FLAGS
_EPHEMERIS_II: list[_Field] = [
    ("omega0_pi", 33, True, 2.0**-32),
    ("i0_pi", 33, True, 2.0**-32),
    ("omega_dot_pi_per_s", 19, True, 2.0**-44),
    ("i_dot_pi_per_s", 15, True, 2.0**-44),
    ("cis_rad", 16, True, 2.0**-30),
    ("cic_rad", 16, True, 2.0**-30),
    ("crs_m", 24, True, 2.0**-8),
    ("crc_m", 24, True, 2.0**-8),
    ("cus_rad", 21, True, 2.0**-30),
    ("cuc_rad", 21, True, 2.0**-30),
]
_TYPE_30_HEAD: list[_Field] = [("hs", 2, False, 1.0)] + _FLAGS
_CLOCK: list[_Field] = [
    ("toc_s", 11, False, 300.0),
    ("a0_s", 25, True, 2.0**-34),
    ("a1_s_per_s", 22, True, 2.0**-50),
    ("a2_s_per_s2", 11, True, 2.0**-66),
]
_TYPE_30_GROUP_DELAY: list[_Field] = [
    ("iodc", 10, False, 1.0),
    ("tgd_b2ap_s", 12, True, 2.0**-34),
    ("isc_b2ad_s", 12, True, 2.0**-34),
]
# BDGIM coefficients alpha1..alpha9 (ICD Table 7-10): alpha1 has 10 bits, the rest 8;
# alpha2 and alpha6-9 are two's complement.  The table prints alpha5's scale factor
# as -2^-3, a negative sign that no other row has; it is applied as printed.
_BDGIM: list[_Field] = [
    ("alpha1", 10, False, 2.0**-3),
    ("alpha2", 8, True, 2.0**-3),
    ("alpha3", 8, False, 2.0**-3),
    ("alpha4", 8, False, 2.0**-3),
    ("alpha5", 8, False, -(2.0**-3)),
    ("alpha6", 8, True, 2.0**-3),
    ("alpha7", 8, True, 2.0**-3),
    ("alpha8", 8, True, 2.0**-3),
    ("alpha9", 8, True, 2.0**-3),
]
_TYPE_30_TGD_B1CP: list[_Field] = [("tgd_b1cp_s", 12, True, 2.0**-34)]
_TYPE_30_RESERVED_BITS = 33

assert _layout_bits(_TYPE_10_HEAD) + _layout_bits(_EPHEMERIS_I) == DATA_BITS
assert _layout_bits(_TYPE_11_HEAD) + _layout_bits(_EPHEMERIS_II) == DATA_BITS
assert (
    _layout_bits(_TYPE_30_HEAD) + _layout_bits(_CLOCK) + _layout_bits(_TYPE_30_GROUP_DELAY)
    + _layout_bits(_BDGIM) + _layout_bits(_TYPE_30_TGD_B1CP) + _TYPE_30_RESERVED_BITS
    == DATA_BITS
)
assert _layout_bits(_EPHEMERIS_I) == 203 and _layout_bits(_EPHEMERIS_II) == 222
assert _layout_bits(_CLOCK) == 69 and _layout_bits(_BDGIM) == 74

DATA_FIRST_BIT = 31  # the first message-data bit, in 1-based message numbering

# B-CNAV1 subframe 2 carries the same ephemeris and clock blocks (ICD Tables 7-5, 7-7
# and 7-8 of both ICDs), so `utils.nav.bcnav1` reads them with these layouts.  The
# names are public so that the two modules cannot drift apart.
EPHEMERIS_I_LAYOUT = _EPHEMERIS_I
EPHEMERIS_II_LAYOUT = _EPHEMERIS_II
CLOCK_LAYOUT = _CLOCK
BDGIM_LAYOUT = _BDGIM
unpack_layout = _unpack_layout
layout_bits = _layout_bits


@dataclass(frozen=True)
class Type10:
    """Ephemeris I with the week number and the IODE."""

    wn: int
    iode: int
    dif_b2a: int
    sif_b2a: int
    aif_b2a: int
    sismai: int
    dif_b1c: int
    sif_b1c: int
    aif_b1c: int
    toe_s: float
    sat_type: int
    """01 GEO, 10 IGSO, 11 MEO.  GEO satellites do not send B2a."""
    delta_a_m: float
    a_dot_mps: float
    delta_n0_pi_per_s: float
    delta_n0_dot_pi_per_s2: float
    m0_pi: float
    e: float
    omega_pi: float


@dataclass(frozen=True)
class Type11:
    """Ephemeris II, with the health status."""

    hs: int
    dif_b2a: int
    sif_b2a: int
    aif_b2a: int
    sismai: int
    dif_b1c: int
    sif_b1c: int
    aif_b1c: int
    omega0_pi: float
    i0_pi: float
    omega_dot_pi_per_s: float
    i_dot_pi_per_s: float
    cis_rad: float
    cic_rad: float
    crs_m: float
    crc_m: float
    cus_rad: float
    cuc_rad: float


@dataclass(frozen=True)
class Type30:
    """Clock correction, group delays, and the nine BDGIM ionosphere coefficients."""

    hs: int
    dif_b2a: int
    sif_b2a: int
    aif_b2a: int
    sismai: int
    dif_b1c: int
    sif_b1c: int
    aif_b1c: int
    toc_s: float
    a0_s: float
    a1_s_per_s: float
    a2_s_per_s2: float
    iodc: int
    tgd_b2ap_s: float
    isc_b2ad_s: float
    tgd_b1cp_s: float
    bdgim_alpha: tuple[float, ...]
    """alpha1..alpha9 in TECu."""


def _require_type(frame: Bcnav2Frame, message_type: int) -> None:
    if frame.message_type != message_type:
        raise ValueError(f"expected message type {message_type}, got {frame.message_type}")


def _ints(values: dict, names: list[str]) -> dict[str, int]:
    return {name: int(values[name]) for name in names}


def parse_type_10(frame: Bcnav2Frame) -> Type10:
    _require_type(frame, 10)
    head = _unpack_layout(frame.bits, _TYPE_10_HEAD, DATA_FIRST_BIT)
    first = DATA_FIRST_BIT + _layout_bits(_TYPE_10_HEAD)
    eph = _unpack_layout(frame.bits, _EPHEMERIS_I, first)
    head_names = [name for name, *_ in _TYPE_10_HEAD]
    return Type10(
        **_ints(head, head_names),
        toe_s=eph["toe_s"], sat_type=int(eph["sat_type"]), delta_a_m=eph["delta_a_m"],
        a_dot_mps=eph["a_dot_mps"], delta_n0_pi_per_s=eph["delta_n0_pi_per_s"],
        delta_n0_dot_pi_per_s2=eph["delta_n0_dot_pi_per_s2"], m0_pi=eph["m0_pi"],
        e=eph["e"], omega_pi=eph["omega_pi"],
    )


def parse_type_11(frame: Bcnav2Frame) -> Type11:
    _require_type(frame, 11)
    head = _unpack_layout(frame.bits, _TYPE_11_HEAD, DATA_FIRST_BIT)
    first = DATA_FIRST_BIT + _layout_bits(_TYPE_11_HEAD)
    eph = _unpack_layout(frame.bits, _EPHEMERIS_II, first)
    head_names = [name for name, *_ in _TYPE_11_HEAD]
    return Type11(**_ints(head, head_names), **eph)


def parse_type_30(frame: Bcnav2Frame) -> Type30:
    _require_type(frame, 30)
    head = _unpack_layout(frame.bits, _TYPE_30_HEAD, DATA_FIRST_BIT)
    cursor = DATA_FIRST_BIT + _layout_bits(_TYPE_30_HEAD)
    clock = _unpack_layout(frame.bits, _CLOCK, cursor)
    cursor += _layout_bits(_CLOCK)
    delays = _unpack_layout(frame.bits, _TYPE_30_GROUP_DELAY, cursor)
    cursor += _layout_bits(_TYPE_30_GROUP_DELAY)
    iono = _unpack_layout(frame.bits, _BDGIM, cursor)
    cursor += _layout_bits(_BDGIM)
    tgd_b1cp = _unpack_layout(frame.bits, _TYPE_30_TGD_B1CP, cursor)
    head_names = [name for name, *_ in _TYPE_30_HEAD]
    return Type30(
        **_ints(head, head_names),
        **clock,
        iodc=int(delays["iodc"]), tgd_b2ap_s=delays["tgd_b2ap_s"], isc_b2ad_s=delays["isc_b2ad_s"],
        tgd_b1cp_s=tgd_b1cp["tgd_b1cp_s"],
        bdgim_alpha=tuple(iono[f"alpha{i}"] for i in range(1, 10)),
    )


def iode_matches_iodc(type_10: Type10, type_30: Type30) -> bool:
    """
    Whether this ephemeris and this clock are a matched pair (ICD 7.4.3).

    They are when IODE equals the 8 LSBs of IODC.  During an update the two can
    differ for a while; the user then keeps the previous matched pair.
    """
    return type_10.iode == (type_30.iodc & 0xFF)


def set_data_field(bits: np.ndarray, start: int, length: int, value: int) -> np.ndarray:
    """
    Write one field into a *message* bit array with the ICD's 1-based numbering.

    For tests: set a field here, then read it back through the real parser.  `value`
    is the raw integer before the scale factor, already wrapped for two's complement.
    The CRC is not updated; rebuild the message with `build_message` for that.
    """
    return set_field(bits, start, length, value)


def assemble_ephemeris(
    result: Bcnav2DecodeResult, sat_id: str | None = None
) -> BdsEphemeris | None:
    """
    The newest complete ephemeris in a decode result, or None.

    Complete means three things, and all must hold:

    * a type 10 frame and a type 11 frame **next to each other** -- 600 symbols
      apart, in either order (D-055).  A pair that is not adjacent could mix two
      ephemeris issues across an update, which gives a plausible wrong orbit;
    * a type 30 frame whose IODC low byte equals the type 10 IODE (ICD 7.4.3);
    * all three from the same satellite.

    During an update the IODE and IODC differ for a while, and the ICD tells the user
    to keep the previous matched set.  This function does the same by looking at
    every pair in the result and taking the newest one that has a matching clock.

    Raises ValueError for a GEO or reserved SatType, which B2a never carries.
    """
    by_index = {frame.symbol_index: frame for frame in result.frames}
    pairs: list[tuple[Bcnav2Frame, Bcnav2Frame]] = []
    for frame in result.frames:
        if frame.message_type != 10:
            continue
        for neighbour_index in (frame.symbol_index - FRAME_SYMBOLS, frame.symbol_index + FRAME_SYMBOLS):
            neighbour = by_index.get(neighbour_index)
            if neighbour is not None and neighbour.message_type == 11 and neighbour.prn == frame.prn:
                pairs.append((frame, neighbour))
    # Newest pair first, judged by the later of its two frames.
    pairs.sort(key=lambda pair: max(pair[0].symbol_index, pair[1].symbol_index), reverse=True)

    for frame_10, frame_11 in pairs:
        type_10 = parse_type_10(frame_10)
        for frame_30 in reversed(result.of_type(30)):
            if frame_30.prn != frame_10.prn:
                continue
            type_30 = parse_type_30(frame_30)
            if not iode_matches_iodc(type_10, type_30):
                continue
            type_11 = parse_type_11(frame_11)
            return BdsEphemeris(
                sat_id=sat_id or f"C{frame_10.prn:02d}",
                week=type_10.wn,
                toe=type_10.toe_s,
                sat_type=type_10.sat_type,
                delta_a=type_10.delta_a_m,
                a_dot=type_10.a_dot_mps,
                delta_n0=type_10.delta_n0_pi_per_s,
                delta_n0_dot=type_10.delta_n0_dot_pi_per_s2,
                M0=type_10.m0_pi,
                e=type_10.e,
                omega=type_10.omega_pi,
                Omega0=type_11.omega0_pi,
                Omega_dot=type_11.omega_dot_pi_per_s,
                i0=type_11.i0_pi,
                i_dot=type_11.i_dot_pi_per_s,
                Cis=type_11.cis_rad, Cic=type_11.cic_rad,
                Crs=type_11.crs_m, Crc=type_11.crc_m,
                Cus=type_11.cus_rad, Cuc=type_11.cuc_rad,
                toc=type_30.toc_s,
                a0=type_30.a0_s, a1=type_30.a1_s_per_s, a2=type_30.a2_s_per_s2,
                tgd_b2ap=type_30.tgd_b2ap_s,
                isc_b2ad=type_30.isc_b2ad_s,
                tgd_b1cp=type_30.tgd_b1cp_s,
                iode=type_10.iode,
                iodc=type_30.iodc,
                # Unhealthy if either message says so.
                health=max(type_11.hs, type_30.hs),
            )
    return None

