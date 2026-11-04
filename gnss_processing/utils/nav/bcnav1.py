"""
BeiDou B-CNAV1 -- the navigation message on B1C (BDS-SIS-ICD-B1C-1.0, section 6).

One **frame** is 1800 symbols at 100 sps, so 18 seconds.  One symbol is one bit, and
the frame has three subframes (ICD Figure 6-1):

    subframe 1    72 symbols   14 bits before coding: PRN (6) and SOH (8)
    subframe 2  1200 symbols   600 bits before coding: time, clock, ephemeris, CRC
    subframe 3   528 symbols   264 bits before coding: one page of the pages 1 to 4

Subframe 1 is coded with two short BCH codes: the PRN with BCH(21,6) and the seconds
of hour with BCH(51,8).  Subframes 2 and 3 are each coded with a 64-ary LDPC code
(`utils.nav.ldpc`), and the two codewords are then written into a 36 x 48 block
interleaver, two rows of subframe 2 and one row of subframe 3 at a time.

This is different from B-CNAV2 in three ways that matter to a receiver:

* There is **no preamble**.  The frame start is found from subframe 1: the PRN
  field is a BCH codeword of a PRN that the receiver already knows, so a matched
  filter over that codeword, then the seconds-of-hour codeword, then the CRCs, find
  and confirm the start.
* The two LDPC codewords are interleaved, so a frame is decoded only as a whole.
* Time comes from two places.  SOH (subframe 1) is the second of the hour; HOW and
  WN (subframe 2) are the hour of the week and the week.  The frame start in BDT is
  ``HOW * 3600 + SOH * 18``.

The BCH codes.  Table 6-1 lists the polynomials ``x^6 + x^4 + x^2 + x + 1`` and
``x^8 + x^7 + x^4 + x^3 + x^2 + x + 1``, and Figure 6-2 shows a k-stage register, not
an (n-k)-stage division circuit.  So the polynomial is the **check** polynomial: the
codeword is the message followed by the continuation of the linear recurrence that the
polynomial defines.  The register in the figure reads, for BCH(21,6), as
``o[t+6] = o[t] + o[t+1] + o[t+2] + o[t+4]``.  This reading is checked here by the
minimum distance: 8 (t = 3) and 24 (t = 11), exactly the t of Table 6-1.  What the
ICD does not show is the direction of the register; the reverse reading gives an
equivalent code with the bits in the other order.  The test that decides it is a real
recording: the decoded PRN must equal the tracked PRN (see D-064).

CRC-24Q is the same as for B-CNAV2.  Subframe 2 has its CRC over its first 576 bits.
Subframe 3 has its CRC over its first 240 bits (the page id and the page data).

Soft symbols follow `utils.nav.primitives`: the sign carries the bit, positive for 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from . import ldpc
from . import primitives as prim
from .bcnav2 import (
    BDGIM_LAYOUT,
    CLOCK_LAYOUT,
    EPHEMERIS_I_LAYOUT,
    EPHEMERIS_II_LAYOUT,
    layout_bits,
    unpack_layout,
)
from .cnav import set_field
from .ephemeris import BdsEphemeris

# --- Frame geometry (ICD 6.2.1, 6.2.2) --------------------------------------
SYMBOL_PERIOD_MS = 10
FRAME_SYMBOLS = 1800
FRAME_DURATION_S = 18.0
SUBFRAME1_SYMBOLS = 72
PRN_CODE_SYMBOLS = 21  # BCH(21,6)
SOH_CODE_SYMBOLS = 51  # BCH(51,8)
PRN_BITS = 6
SOH_BITS = 8
SUBFRAME2_BITS = 600
SUBFRAME3_BITS = 264
SUBFRAME2_SYMBOLS = 1200  # LDPC(200,100): 200 six-bit symbols
SUBFRAME3_SYMBOLS = 528  # LDPC(88,44): 88 six-bit symbols
INTERLEAVED_SYMBOLS = SUBFRAME2_SYMBOLS + SUBFRAME3_SYMBOLS  # 1728
INTERLEAVER_ROWS = 36
INTERLEAVER_COLUMNS = 48

SOH_SCALE_S = 18.0
SOH_COUNT_MAX = 199  # 3582 s / 18 s: the last frame of an hour
SECONDS_PER_HOUR = 3600
HOURS_PER_WEEK = 168
SUBFRAME2_INFORMATION_BITS = 576  # the part the CRC covers
SUBFRAME3_INFORMATION_BITS = 240

PAGE_IDS = (1, 2, 3, 4)

assert SUBFRAME1_SYMBOLS + INTERLEAVED_SYMBOLS == FRAME_SYMBOLS
assert INTERLEAVER_ROWS * INTERLEAVER_COLUMNS == INTERLEAVED_SYMBOLS
assert SUBFRAME1_SYMBOLS == PRN_CODE_SYMBOLS + SOH_CODE_SYMBOLS

# --- Subframe 1: the BCH codes ----------------------------------------------
# (message bits, code length, exponents of the check polynomial below x^k).  The
# polynomial is x^k + sum(x^e for e in exponents), so the recurrence is
# o[t+k] = sum(o[t+e]).
_PRN_BCH = (PRN_BITS, PRN_CODE_SYMBOLS, (4, 2, 1, 0))
_SOH_BCH = (SOH_BITS, SOH_CODE_SYMBOLS, (7, 4, 3, 2, 1, 0))


def _bch_codeword(message_bits: list[int], k: int, n: int, exponents: tuple[int, ...]) -> list[int]:
    """The message, then the recurrence continued to `n` bits (ICD Figure 6-2)."""
    bits = list(message_bits)
    while len(bits) < n:
        bits.append(sum(bits[len(bits) - k + e] for e in exponents) % 2)
    return bits


@lru_cache(maxsize=2)
def _bch_codebook(k: int, n: int, exponents: tuple[int, ...]) -> np.ndarray:
    """All ``2**k`` codewords as an array of 0/1 bits, row ``m`` for message ``m``."""
    words = [
        _bch_codeword([(m >> (k - 1 - i)) & 1 for i in range(k)], k, n, exponents)
        for m in range(1 << k)
    ]
    return np.array(words, dtype=np.uint8)


def encode_prn_field(prn: int) -> np.ndarray:
    """The 21 transmitted bits of the PRN field (BCH(21,6))."""
    if not 0 <= prn < (1 << PRN_BITS):
        raise ValueError(f"the PRN field is {PRN_BITS} bits; {prn} does not fit")
    return _bch_codebook(*_PRN_BCH)[prn].copy()


def encode_soh_field(soh_count: int) -> np.ndarray:
    """The 51 transmitted bits of the SOH field (BCH(51,8))."""
    if not 0 <= soh_count < (1 << SOH_BITS):
        raise ValueError(f"the SOH field is {SOH_BITS} bits; {soh_count} does not fit")
    return _bch_codebook(*_SOH_BCH)[soh_count].copy()


def _correlate_codebook(soft: np.ndarray, codebook: np.ndarray) -> tuple[int, float]:
    """
    Best message and its normalised correlation, for one polarity.

    The score is ``sum(c * s) / sum(|s|)`` for the best codeword ``c``: 1 for a clean
    word, near 0 for noise.  A maximum-likelihood decode needs no more, because the
    code is tiny (64 and 256 words).
    """
    signs = 1.0 - 2.0 * codebook.astype(np.float64)
    scores = signs @ soft
    best = int(np.argmax(scores))
    total = float(np.abs(soft).sum())
    return best, (float(scores[best]) / total if total > 0 else 0.0)


def decode_prn_field(soft: np.ndarray) -> tuple[int, float]:
    """``(prn, score)`` from 21 soft symbols, for the polarity as received."""
    return _correlate_codebook(np.asarray(soft, dtype=np.float64), _bch_codebook(*_PRN_BCH))


def decode_soh_field(soft: np.ndarray) -> tuple[int, float]:
    """``(soh_count, score)`` from 51 soft symbols, for the polarity as received."""
    return _correlate_codebook(np.asarray(soft, dtype=np.float64), _bch_codebook(*_SOH_BCH))


# --- The block interleaver (ICD 6.2.2.4, Figure 6-4) ------------------------
# Rows are numbered from 0 here.  Subframe 3 takes every third row from row 2 on
# (ICD rows 3, 6, ..., 33); subframe 2 takes the pairs before them and then rows
# 33 to 35 (ICD rows 34 to 36).
_SUBFRAME3_ROWS = tuple(range(2, 33, 3))
_SUBFRAME2_ROWS = tuple(r for r in range(INTERLEAVER_ROWS) if r not in _SUBFRAME3_ROWS)
assert len(_SUBFRAME3_ROWS) * INTERLEAVER_COLUMNS == SUBFRAME3_SYMBOLS
assert len(_SUBFRAME2_ROWS) * INTERLEAVER_COLUMNS == SUBFRAME2_SYMBOLS


def interleave(subframe2: np.ndarray, subframe3: np.ndarray) -> np.ndarray:
    """
    The 1728 transmitted symbols from the two LDPC codewords.

    Both are written by rows (subframe 2 into two rows, subframe 3 into one row, and
    so on, the last three rows with the rest of subframe 2), then read by columns,
    top to bottom, column 1 first.
    """
    subframe2 = np.asarray(subframe2)
    subframe3 = np.asarray(subframe3)
    if len(subframe2) != SUBFRAME2_SYMBOLS or len(subframe3) != SUBFRAME3_SYMBOLS:
        raise ValueError(
            f"expected {SUBFRAME2_SYMBOLS} and {SUBFRAME3_SYMBOLS} symbols, "
            f"got {len(subframe2)} and {len(subframe3)}"
        )
    array = np.zeros((INTERLEAVER_ROWS, INTERLEAVER_COLUMNS), dtype=subframe2.dtype)
    array[list(_SUBFRAME2_ROWS)] = subframe2.reshape(-1, INTERLEAVER_COLUMNS)
    array[list(_SUBFRAME3_ROWS)] = subframe3.reshape(-1, INTERLEAVER_COLUMNS)
    return array.T.reshape(-1)


def deinterleave(received: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Inverse of `interleave`: ``(subframe2, subframe3)`` symbols from 1728 received."""
    received = np.asarray(received)
    if len(received) != INTERLEAVED_SYMBOLS:
        raise ValueError(f"expected {INTERLEAVED_SYMBOLS} symbols, got {len(received)}")
    array = received.reshape(INTERLEAVER_COLUMNS, INTERLEAVER_ROWS).T
    return (
        array[list(_SUBFRAME2_ROWS)].reshape(-1),
        array[list(_SUBFRAME3_ROWS)].reshape(-1),
    )


# --- Subframe layouts (ICD Figures 6-6 and 6-8, Table 6-2) -------------------
_SUBFRAME2_HEAD = [
    ("wn", 13, False, 1.0),
    ("how", 8, False, 1.0),
    ("iodc", 10, False, 1.0),
    ("iode", 8, False, 1.0),
]
_SUBFRAME2_DELAYS = [
    ("tgd_b2ap_s", 12, True, 2.0**-34),
    ("isc_b1cd_s", 12, True, 2.0**-34),
    ("tgd_b1cp_s", 12, True, 2.0**-34),
]
_SUBFRAME2_RESERVED_BITS = 7
_PAGE1_HEAD = [
    ("page_id", 6, False, 1.0),
    ("hs", 2, False, 1.0),
    ("dif_b1c", 1, False, 1.0),
    ("sif_b1c", 1, False, 1.0),
    ("aif_b1c", 1, False, 1.0),
    ("sismai", 4, False, 1.0),
    ("sisaioe", 5, False, 1.0),
    ("sisaioc", 22, False, 1.0),
]

assert (
    layout_bits(_SUBFRAME2_HEAD)
    + layout_bits(EPHEMERIS_I_LAYOUT)
    + layout_bits(EPHEMERIS_II_LAYOUT)
    + layout_bits(CLOCK_LAYOUT)
    + layout_bits(_SUBFRAME2_DELAYS)
    + _SUBFRAME2_RESERVED_BITS
    == SUBFRAME2_INFORMATION_BITS
)
assert layout_bits(EPHEMERIS_I_LAYOUT) == 203 and layout_bits(EPHEMERIS_II_LAYOUT) == 222
assert layout_bits(_PAGE1_HEAD) + layout_bits(BDGIM_LAYOUT) + 97 + 27 == SUBFRAME3_INFORMATION_BITS


# ---------------------------------------------------------------------------
# Frames and decoding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Bcnav1Frame:
    """One B-CNAV1 frame whose subframe 2 passed its CRC."""

    prn: int
    soh_count: int
    subframe2_bits: np.ndarray
    """The 600 decoded bits of subframe 2 (the CRC passed)."""

    subframe3_bits: np.ndarray | None
    """The 264 decoded bits of subframe 3, or None when its CRC failed."""

    symbol_index: int
    """Where this frame's first symbol (subframe 1) sits in the symbol stream.  This
    is what ties a decoded time back to a tracking epoch."""

    inverted: bool
    """True when the symbols arrived with their sign flipped.  The carrier loop has a
    180 degree ambiguity, and the PRN codeword resolves it."""

    prn_score: float
    soh_score: float
    """Normalised BCH correlations (1 for a clean word); a low value marks a frame
    that was found with little margin."""

    ldpc_iterations: int

    @property
    def wn(self) -> int:
        """BDT week number, from subframe 2."""
        return int(prim.unpack_field(self.subframe2_bits, 1, 13))

    @property
    def how(self) -> int:
        """Hours of the BDT week, from subframe 2."""
        return int(prim.unpack_field(self.subframe2_bits, 14, 8))

    @property
    def tow_at_frame_start_s(self) -> float:
        """Seconds of BDT week at the first symbol of subframe 1 (ICD 7.3)."""
        return self.how * SECONDS_PER_HOUR + self.soh_count * SOH_SCALE_S

    @property
    def page_id(self) -> int | None:
        return None if self.subframe3_bits is None else int(prim.unpack_field(self.subframe3_bits, 1, 6))


@dataclass
class Bcnav1DecodeResult:
    """Everything one pass over a symbol stream produced."""

    frames: list[Bcnav1Frame] = field(default_factory=list)
    candidates_tried: int = 0
    """Frame starts that went on to an LDPC decode."""

    @property
    def synced(self) -> bool:
        return len(self.frames) > 0

    @property
    def inverted(self) -> bool:
        """Whether the first frame arrived upside down.  False when nothing decoded."""
        return bool(self.frames and self.frames[0].inverted)

    def sow_is_consistent(self) -> bool:
        """
        Successive frames must advance the time of week by 18 s per 1800 symbols.

        One frame with two good CRCs is already strong evidence; a run that steps by
        exactly one frame duration catches a frame found at a wrong alignment whose
        CRC passed by chance.  The time of week wraps at the end of the week.
        """
        week_s = HOURS_PER_WEEK * SECONDS_PER_HOUR
        for earlier, later in zip(self.frames, self.frames[1:]):
            steps = round((later.symbol_index - earlier.symbol_index) / FRAME_SYMBOLS)
            expected = (earlier.tow_at_frame_start_s + steps * FRAME_DURATION_S) % week_s
            if expected != later.tow_at_frame_start_s:
                return False
        return True


def _sliding_codeword_scores(soft: np.ndarray, signs: np.ndarray) -> np.ndarray:
    """
    Normalised correlation of one codeword (as +1/-1) with every window of `soft`.

    ``score[o] = sum(signs * soft[o:o+n]) / sum(|soft[o:o+n]|)``.  Positive for the
    polarity as received, negative for an upside-down frame.
    """
    n = len(signs)
    numerator = np.correlate(soft, signs, mode="valid")
    absolute = np.concatenate(([0.0], np.cumsum(np.abs(soft))))
    denominator = absolute[n:] - absolute[:-n]
    return np.divide(numerator, denominator, out=np.zeros_like(numerator), where=denominator > 0)


def decode(
    symbols: np.ndarray,
    *,
    expected_prn: int | None = None,
    min_prn_score: float = 0.6,
    min_soh_score: float = 0.55,
    max_ldpc_iterations: int = 50,
) -> Bcnav1DecodeResult:
    """
    Decode every B-CNAV1 frame present in a stream of 100 sps soft symbols.

    `symbols` are soft values, positive for a transmitted 0, one per data symbol --
    exactly what `utils.nav.symbols.extract` returns as `SymbolStream.soft`.

    There is no preamble, so the search runs on subframe 1.  At every offset the first
    21 symbols are correlated with the PRN codeword (with `expected_prn` given: one
    codeword; without it: all 63 PRNs).  An offset whose normalised score is at least
    `min_prn_score`, in either polarity, goes on.  The 51 SOH symbols behind it must
    then decode to a valid second of hour (0 to 199) with a score of at least
    `min_soh_score`.  (On pure noise the best of the 256 SOH codewords scores about
    0.5, so this filter removes about half of the false starts, not all.)  Both thresholds are deliberately loose, as the B-CNAV2 preamble
    one is: the LDPC decoder and the CRC are the real test, and a tight threshold
    drops frames that would have decoded.

    A frame is kept when the CRC of subframe 2 passes.  Subframe 3 is kept when its
    CRC passes too; a frame with a bad subframe 3 still gives time and ephemeris.
    Candidates are tried best score first, and a frame that decodes removes the
    candidates that overlap it, so a good frame is not decoded again at a shifted
    offset.
    """
    soft_all = np.asarray(symbols, dtype=np.float64)
    result = Bcnav1DecodeResult()
    if len(soft_all) < FRAME_SYMBOLS:
        return result

    prns = [expected_prn] if expected_prn is not None else range(1, 64)
    best_score = np.zeros(len(soft_all) - PRN_CODE_SYMBOLS + 1)
    best_prn = np.zeros(len(best_score), dtype=int)
    for prn in prns:
        signs = 1.0 - 2.0 * encode_prn_field(prn).astype(np.float64)
        scores = _sliding_codeword_scores(soft_all, signs)
        better = np.abs(scores) > np.abs(best_score)
        best_score = np.where(better, scores, best_score)
        best_prn = np.where(better, prn, best_prn)

    last_start = len(soft_all) - FRAME_SYMBOLS
    candidates = [
        offset for offset in np.nonzero(np.abs(best_score[: last_start + 1]) >= min_prn_score)[0]
    ]
    candidates.sort(key=lambda offset: -abs(best_score[offset]))

    taken: list[int] = []
    for offset in candidates:
        offset = int(offset)
        if any(abs(offset - other) < FRAME_SYMBOLS for other in taken):
            continue
        inverted = bool(best_score[offset] < 0)
        window = soft_all[offset : offset + FRAME_SYMBOLS]
        window = -window if inverted else window
        soh_count, soh_score = decode_soh_field(window[PRN_CODE_SYMBOLS:SUBFRAME1_SYMBOLS])
        if soh_score < min_soh_score or soh_count > SOH_COUNT_MAX:
            continue
        result.candidates_tried += 1
        frame = _decode_subframes(
            window, prn=int(best_prn[offset]), soh_count=soh_count,
            symbol_index=offset, inverted=inverted,
            prn_score=abs(float(best_score[offset])), soh_score=soh_score,
            max_ldpc_iterations=max_ldpc_iterations,
        )
        if frame is None:
            continue
        taken.append(offset)
        result.frames.append(frame)
    result.frames.sort(key=lambda frame: frame.symbol_index)
    return result


def _decode_subframes(
    window: np.ndarray, *, prn: int, soh_count: int, symbol_index: int, inverted: bool,
    prn_score: float, soh_score: float, max_ldpc_iterations: int,
) -> Bcnav1Frame | None:
    """LDPC-decode both subframes of one aligned, upright frame; None if subframe 2 fails."""
    soft2, soft3 = deinterleave(window[SUBFRAME1_SYMBOLS:])
    decoded2 = ldpc.BCNAV1_SUBFRAME2_CODE.decode(soft2, max_iterations=max_ldpc_iterations)
    if not decoded2.converged:
        return None
    bits2 = decoded2.message_bits
    if not prim.crc24q_check(bits2):
        return None
    subframe3_bits = None
    iterations = decoded2.iterations
    decoded3 = ldpc.BCNAV1_SUBFRAME3_CODE.decode(soft3, max_iterations=max_ldpc_iterations)
    if decoded3.converged and prim.crc24q_check(decoded3.message_bits):
        subframe3_bits = decoded3.message_bits
        iterations = max(iterations, decoded3.iterations)
    return Bcnav1Frame(
        prn=prn, soh_count=soh_count, subframe2_bits=bits2, subframe3_bits=subframe3_bits,
        symbol_index=symbol_index, inverted=inverted, prn_score=prn_score,
        soh_score=soh_score, ldpc_iterations=iterations,
    )


# ---------------------------------------------------------------------------
# Encoding
#
# No B1C recording in this repository carries a message whose contents we know in
# advance, so the only way to test the decoder against a known truth is to build the
# frame ourselves.  These are the encoder halves of everything above.
# ---------------------------------------------------------------------------


def build_subframe2(information_bits: np.ndarray | None = None) -> np.ndarray:
    """
    The 600 bits of subframe 2: 576 information bits and the CRC-24Q.

    Omitting `information_bits` gives alternating ones and zeros, a non-degenerate
    pattern.  Use `set_data_field` on the 576 bits to build a message with fields.
    """
    if information_bits is None:
        information_bits = np.array(
            [(i + 1) % 2 for i in range(SUBFRAME2_INFORMATION_BITS)], dtype=np.uint8
        )
    information_bits = np.asarray(information_bits, dtype=np.uint8)
    if len(information_bits) != SUBFRAME2_INFORMATION_BITS:
        raise ValueError(
            f"subframe 2 holds {SUBFRAME2_INFORMATION_BITS} information bits, "
            f"got {len(information_bits)}"
        )
    return prim.crc24q_append(information_bits)


def build_subframe3(information_bits: np.ndarray | None = None) -> np.ndarray:
    """The 264 bits of subframe 3: 240 information bits (page id first) and the CRC."""
    if information_bits is None:
        information_bits = np.concatenate(
            [prim.bits_from_int(1, 6), np.zeros(SUBFRAME3_INFORMATION_BITS - 6, dtype=np.uint8)]
        )
    information_bits = np.asarray(information_bits, dtype=np.uint8)
    if len(information_bits) != SUBFRAME3_INFORMATION_BITS:
        raise ValueError(
            f"subframe 3 holds {SUBFRAME3_INFORMATION_BITS} information bits, "
            f"got {len(information_bits)}"
        )
    return prim.crc24q_append(information_bits)


def encode_frame(
    *, prn: int, soh_count: int, subframe2_bits: np.ndarray, subframe3_bits: np.ndarray
) -> np.ndarray:
    """
    The 1800 transmitted bits of one frame, from the two 600- and 264-bit subframes.

    Use `soft_symbols` for the +1/-1 form the decoder takes.
    """
    subframe2_bits = np.asarray(subframe2_bits, dtype=np.uint8)
    subframe3_bits = np.asarray(subframe3_bits, dtype=np.uint8)
    if len(subframe2_bits) != SUBFRAME2_BITS or len(subframe3_bits) != SUBFRAME3_BITS:
        raise ValueError(
            f"expected {SUBFRAME2_BITS} and {SUBFRAME3_BITS} subframe bits, "
            f"got {len(subframe2_bits)} and {len(subframe3_bits)}"
        )
    codeword2 = ldpc.BCNAV1_SUBFRAME2_CODE.encode(ldpc.symbols_from_bits(subframe2_bits))
    codeword3 = ldpc.BCNAV1_SUBFRAME3_CODE.encode(ldpc.symbols_from_bits(subframe3_bits))
    return np.concatenate(
        [
            encode_prn_field(prn),
            encode_soh_field(soh_count),
            interleave(ldpc.bits_from_symbols(codeword2), ldpc.bits_from_symbols(codeword3)),
        ]
    )


def soft_symbols(bits: np.ndarray) -> np.ndarray:
    """Bits as noiseless soft symbols: 0 becomes +1 and 1 becomes -1."""
    return 1.0 - 2.0 * np.asarray(bits, dtype=np.float64)


def set_data_field(bits: np.ndarray, start: int, length: int, value: int) -> np.ndarray:
    """
    Write one field into a subframe bit array with the ICD's 1-based numbering.

    For tests: set a field here, then read it back through the real parser.  `value`
    is the raw integer before the scale factor, already wrapped for two's complement.
    The CRC is not updated; rebuild the subframe with `build_subframe2` or
    `build_subframe3` for that.
    """
    return set_field(bits, start, length, value)


# ---------------------------------------------------------------------------
# Message parsers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Subframe2:
    """Time, issue numbers, ephemeris, clock and group delays (ICD Figure 6-6)."""

    wn: int
    how: int
    iodc: int
    iode: int
    toe_s: float
    sat_type: int
    """01 GEO, 10 IGSO, 11 MEO."""
    delta_a_m: float
    a_dot_mps: float
    delta_n0_pi_per_s: float
    delta_n0_dot_pi_per_s2: float
    m0_pi: float
    e: float
    omega_pi: float
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
    toc_s: float
    a0_s: float
    a1_s_per_s: float
    a2_s_per_s2: float
    tgd_b2ap_s: float
    isc_b1cd_s: float
    tgd_b1cp_s: float


def parse_subframe2(frame: Bcnav1Frame) -> Subframe2:
    """Read subframe 2 of a decoded frame."""
    bits = frame.subframe2_bits
    cursor = 1
    head = unpack_layout(bits, _SUBFRAME2_HEAD, cursor)
    cursor += layout_bits(_SUBFRAME2_HEAD)
    ephemeris_1 = unpack_layout(bits, EPHEMERIS_I_LAYOUT, cursor)
    cursor += layout_bits(EPHEMERIS_I_LAYOUT)
    ephemeris_2 = unpack_layout(bits, EPHEMERIS_II_LAYOUT, cursor)
    cursor += layout_bits(EPHEMERIS_II_LAYOUT)
    clock = unpack_layout(bits, CLOCK_LAYOUT, cursor)
    cursor += layout_bits(CLOCK_LAYOUT)
    delays = unpack_layout(bits, _SUBFRAME2_DELAYS, cursor)
    return Subframe2(
        wn=int(head["wn"]), how=int(head["how"]), iodc=int(head["iodc"]), iode=int(head["iode"]),
        toe_s=ephemeris_1["toe_s"], sat_type=int(ephemeris_1["sat_type"]),
        delta_a_m=ephemeris_1["delta_a_m"], a_dot_mps=ephemeris_1["a_dot_mps"],
        delta_n0_pi_per_s=ephemeris_1["delta_n0_pi_per_s"],
        delta_n0_dot_pi_per_s2=ephemeris_1["delta_n0_dot_pi_per_s2"],
        m0_pi=ephemeris_1["m0_pi"], e=ephemeris_1["e"], omega_pi=ephemeris_1["omega_pi"],
        omega0_pi=ephemeris_2["omega0_pi"], i0_pi=ephemeris_2["i0_pi"],
        omega_dot_pi_per_s=ephemeris_2["omega_dot_pi_per_s"],
        i_dot_pi_per_s=ephemeris_2["i_dot_pi_per_s"],
        cis_rad=ephemeris_2["cis_rad"], cic_rad=ephemeris_2["cic_rad"],
        crs_m=ephemeris_2["crs_m"], crc_m=ephemeris_2["crc_m"],
        cus_rad=ephemeris_2["cus_rad"], cuc_rad=ephemeris_2["cuc_rad"],
        toc_s=clock["toc_s"], a0_s=clock["a0_s"], a1_s_per_s=clock["a1_s_per_s"],
        a2_s_per_s2=clock["a2_s_per_s2"],
        tgd_b2ap_s=delays["tgd_b2ap_s"], isc_b1cd_s=delays["isc_b1cd_s"],
        tgd_b1cp_s=delays["tgd_b1cp_s"],
    )


@dataclass(frozen=True)
class Page1:
    """Health, integrity flags and the BDGIM ionosphere terms (ICD Figure 6-8)."""

    hs: int
    dif_b1c: int
    sif_b1c: int
    aif_b1c: int
    sismai: int
    sisaioe: int
    sisaioc: int
    bdgim_alpha: tuple[float, ...]
    """alpha1..alpha9 in TECu."""


def parse_page_1(frame: Bcnav1Frame) -> Page1 | None:
    """Read page type 1 of subframe 3, or None when this frame carries another page."""
    if frame.subframe3_bits is None or frame.page_id != 1:
        return None
    bits = frame.subframe3_bits
    head = unpack_layout(bits, _PAGE1_HEAD, 1)
    iono = unpack_layout(bits, BDGIM_LAYOUT, 1 + layout_bits(_PAGE1_HEAD))
    return Page1(
        hs=int(head["hs"]), dif_b1c=int(head["dif_b1c"]), sif_b1c=int(head["sif_b1c"]),
        aif_b1c=int(head["aif_b1c"]), sismai=int(head["sismai"]),
        sisaioe=int(head["sisaioe"]), sisaioc=int(head["sisaioc"]),
        bdgim_alpha=tuple(iono[f"alpha{i}"] for i in range(1, 10)),
    )


def iode_matches_iodc(subframe2: Subframe2) -> bool:
    """
    Whether the ephemeris and the clock of one subframe 2 are a matched pair (ICD 7.4.3).

    They are when IODE equals the 8 LSBs of IODC.  Both come in the same subframe, but
    during an update they can differ for a while; the user then keeps the previous
    matched pair.
    """
    return subframe2.iode == (subframe2.iodc & 0xFF)


def assemble_ephemeris(
    result: Bcnav1DecodeResult, sat_id: str | None = None
) -> BdsEphemeris | None:
    """
    The newest matched ephemeris in a decode result, or None.

    One subframe 2 holds the whole ephemeris and the clock, so no pairing of frames is
    needed (unlike B-CNAV2).  The newest frame whose IODE equals the IODC low byte is
    used.  The health comes from the newest page type 1 in the result, if any; without
    one the satellite is taken as healthy, because the ephemeris itself says nothing
    about health.

    Raises ValueError for a GEO or reserved SatType, which B1C never carries.
    """
    for frame in reversed(result.frames):
        subframe2 = parse_subframe2(frame)
        if not iode_matches_iodc(subframe2):
            continue
        health = 0
        for later in reversed(result.frames):
            page = parse_page_1(later)
            if page is not None:
                health = page.hs
                break
        return BdsEphemeris(
            sat_id=sat_id or f"C{frame.prn:02d}",
            week=subframe2.wn,
            toe=subframe2.toe_s,
            sat_type=subframe2.sat_type,
            delta_a=subframe2.delta_a_m,
            a_dot=subframe2.a_dot_mps,
            delta_n0=subframe2.delta_n0_pi_per_s,
            delta_n0_dot=subframe2.delta_n0_dot_pi_per_s2,
            M0=subframe2.m0_pi,
            e=subframe2.e,
            omega=subframe2.omega_pi,
            Omega0=subframe2.omega0_pi,
            Omega_dot=subframe2.omega_dot_pi_per_s,
            i0=subframe2.i0_pi,
            i_dot=subframe2.i_dot_pi_per_s,
            Cis=subframe2.cis_rad, Cic=subframe2.cic_rad,
            Crs=subframe2.crs_m, Crc=subframe2.crc_m,
            Cus=subframe2.cus_rad, Cuc=subframe2.cuc_rad,
            toc=subframe2.toc_s,
            a0=subframe2.a0_s, a1=subframe2.a1_s_per_s, a2=subframe2.a2_s_per_s2,
            tgd_b2ap=subframe2.tgd_b2ap_s,
            isc_b1cd=subframe2.isc_b1cd_s,
            tgd_b1cp=subframe2.tgd_b1cp_s,
            iode=subframe2.iode,
            iodc=subframe2.iodc,
            health=health,
        )
    return None
