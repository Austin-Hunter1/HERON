"""
GPS LNAV -- the legacy navigation message on L1 C/A.

LNAV is the oldest of the four formats and the least like the others.  Where CNAV
gives you a self-describing 300-bit message with a CRC, LNAV gives you a 1500-bit
frame of five 300-bit subframes, each ten 30-bit words, each word carrying 24 data
bits and six parity bits of a (32,26) Hamming code that *chains across words*
(IS-GPS-200N 20.3.5).  Three consequences shape this module:

  * **Data must be recovered before parity can be checked.**  The SV transmits
    `D_n = d_n XOR D30*`, where D30* is the last bit of the previous word.  So the
    receiver undoes that inversion first, then recomputes parity over the
    recovered data.  `primitives.lnav_decode_word` does both.

  * **A wrong starting polarity is indistinguishable from a set D30*.**  Costas
    tracking leaves a 180 degree ambiguity, and the D30* inversion looks exactly
    the same.  There is no way to tell them apart from one word -- only parity over
    a whole subframe settles it, which is why `_try_subframe` checks all ten words
    rather than stopping at the first that passes.

  * **Fields are split around the parity bits.**  A 32-bit parameter cannot live in
    one 24-bit word, so the spec splits it: 8 MSBs at the end of one word, 24 LSBs
    in the next.  Those splits are transcribed literally below, with the spec's own
    bit numbers, so the layout can be checked against Figure 20-1 line by line.

Bit sync is *not* here.  LNAV has no overlay code to synchronise against, so
finding the 20 ms symbol boundary is a separate problem solved in
`utils.nav.bitsync`; this module starts from symbols that are already one per data
bit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import primitives as prim
from .ephemeris import LnavEphemeris

WORD_BITS = 30
DATA_BITS_PER_WORD = 24
WORDS_PER_SUBFRAME = 10
SUBFRAME_BITS = WORD_BITS * WORDS_PER_SUBFRAME  # 300
SUBFRAMES_PER_FRAME = 5
SUBFRAME_DURATION_S = 6.0
BIT_PERIOD_MS = 20  # 50 bps

PREAMBLE_BITS = prim.bits_from_int(0b10001011, 8)

# Klobuchar scaling, IS-GPS-200N Table 20-X.  Shared with CNAV message type 30.
_ALPHA_SCALES = (2.0**-30, 2.0**-27, 2.0**-24, 2.0**-24)
_BETA_SCALES = (2.0**11, 2.0**14, 2.0**16, 2.0**16)

# The eight coefficients are NOT evenly spaced, because the parity bits interrupt
# them.  Word 3 (bits 61-90) has room for alpha0 and alpha1 after the data and page
# IDs, then bits 85-90 are parity; word 4 (91-120) carries alpha2, alpha3 and beta0
# in bits 91-114; word 5 (121-150) carries beta1, beta2 and beta3 in bits 121-144.
# Writing these as `69 + 8*i` looks tidier and lands alpha2 squarely on word 3's
# parity field -- see Figure 20-1 sheet 8.
_ALPHA_STARTS = (69, 77, 91, 99)
_BETA_STARTS = (107, 121, 129, 137)

# Subframe 4 carries the ionospheric and UTC parameters on the page whose SV ID
# field reads 56 -- what the spec calls page 18 (IS-GPS-200N 20.3.3.5.1.1).
IONO_UTC_PAGE_SV_ID = 56


@dataclass(frozen=True)
class LnavSubframe:
    """One parity-checked 300-bit subframe, with its data bits recovered."""

    subframe_id: int
    tow_count: int
    bits: np.ndarray
    """All 300 bits, with data bits de-inverted in place.  Parity bits are left as
    transmitted; nothing reads them after validation."""

    symbol_index: int
    alert: bool
    anti_spoof: bool

    @property
    def tow_at_next_subframe_start_s(self) -> float:
        """What the HOW field literally means (IS-GPS-200N 20.3.3.2)."""
        return self.tow_count * 6.0

    @property
    def tow_at_subframe_start_s(self) -> float:
        """SV time at this subframe's first bit -- the epoch `symbol_index` marks."""
        return self.tow_at_next_subframe_start_s - SUBFRAME_DURATION_S

    def field(self, start: int, length: int, *, signed: bool = False, scale: float = 1.0):
        """Read a field by its subframe bit number, as Figure 20-1 prints it."""
        return prim.unpack_field(self.bits, start, length, signed=signed, scale=scale)

    def split_field(
        self,
        msb_start: int,
        msb_length: int,
        lsb_start: int,
        lsb_length: int,
        *,
        signed: bool = False,
        scale: float = 1.0,
    ):
        """
        Reassemble a parameter the parity bits split across two words.

        The spec always puts the MSBs first and in the earlier word, so this is a
        shift-and-or -- but doing it by hand at eleven call sites is eleven chances
        to shift by the wrong width.
        """
        high = int(prim.unpack_field(self.bits, msb_start, msb_length))
        low = int(prim.unpack_field(self.bits, lsb_start, lsb_length))
        raw = (high << lsb_length) | low
        width = msb_length + lsb_length
        if signed and raw >= (1 << (width - 1)):
            raw -= 1 << width
        return raw * scale if scale != 1.0 else raw


@dataclass
class LnavDecodeResult:
    subframes: list[LnavSubframe] = field(default_factory=list)
    inverted: bool = False
    """True when the whole stream arrived with its sign flipped."""

    bit_offset: int = 0
    """Where in the supplied symbol stream the first subframe began."""

    @property
    def synced(self) -> bool:
        return len(self.subframes) > 0

    def of_id(self, subframe_id: int) -> list[LnavSubframe]:
        return [s for s in self.subframes if s.subframe_id == subframe_id]


def _recover_subframe(
    bits: np.ndarray, d29_star: int, d30_star: int
) -> tuple[np.ndarray, bool, int, int]:
    """
    De-invert and parity-check all ten words of one subframe.

    Returns `(recovered_bits, all_words_ok, last_d29, last_d30)`.  The trailing
    parity state is returned because it seeds the next subframe -- the chain runs
    across subframe boundaries, not just within one.
    """
    recovered = bits.copy()
    ok = True
    for w in range(WORDS_PER_SUBFRAME):
        start = w * WORD_BITS
        word = bits[start : start + WORD_BITS]
        data, word_ok = prim.lnav_decode_word(word, d29_star, d30_star)
        recovered[start : start + DATA_BITS_PER_WORD] = data
        ok = ok and word_ok
        d29_star, d30_star = int(word[28]), int(word[29])
    return recovered, ok, d29_star, d30_star


def _try_subframe(
    bits: np.ndarray, d29_star: int, d30_star: int
) -> LnavSubframe | None:
    """Build a subframe if every word's parity checks and the header is sane."""
    recovered, ok, _, _ = _recover_subframe(bits, d29_star, d30_star)
    if not ok:
        return None
    if not np.array_equal(recovered[:8], PREAMBLE_BITS):
        return None
    subframe_id = int(prim.unpack_field(recovered, 50, 3))
    if not 1 <= subframe_id <= 5:
        return None
    return LnavSubframe(
        subframe_id=subframe_id,
        tow_count=int(prim.unpack_field(recovered, 31, 17)),
        bits=recovered,
        symbol_index=0,  # filled in by the caller, which knows the offset
        alert=bool(recovered[47]),
        anti_spoof=bool(recovered[48]),
    )


def decode(symbols: np.ndarray, *, max_subframes: int | None = None) -> LnavDecodeResult:
    """
    Find and decode every LNAV subframe in a stream of soft symbols.

    `symbols` are one per data bit -- 50 per second, so the caller has already
    solved bit sync (see `utils.nav.bitsync`).  Positive means a transmitted 0.

    Synchronisation works outward from a single anchor: scan for a bit offset whose
    300 bits parity-check as a whole subframe, then walk forwards in 300-bit steps
    carrying the parity chain, which is far cheaper than searching every offset and
    is also the only way to get the chain right.

    Both polarities are tried.  Note that the preamble alone cannot distinguish
    polarity from a set D30* -- only whole-subframe parity can -- so the search
    tries each candidate offset under both hypotheses rather than trusting a
    preamble match.
    """
    symbols = np.asarray(symbols, dtype=np.float64)
    best = LnavDecodeResult()

    for inverted in (False, True):
        bits = prim.hard_decision(-symbols if inverted else symbols)
        if len(bits) < SUBFRAME_BITS:
            continue

        for offset in range(len(bits) - SUBFRAME_BITS + 1):
            # The preamble is `d1..d8 XOR D30*`, so it arrives either upright or
            # complemented.  Checking both is a cheap gate before parity.
            head = bits[offset : offset + 8]
            upright = np.array_equal(head, PREAMBLE_BITS)
            flipped = np.array_equal(head, PREAMBLE_BITS ^ 1)
            if not (upright or flipped):
                continue

            # D29*/D30* precede this subframe.  D30* is fixed by whether the
            # preamble came through inverted; D29* only affects parity, so try both.
            d30_star = 0 if upright else 1
            for d29_star in (0, 1):
                first = _try_subframe(
                    bits[offset : offset + SUBFRAME_BITS], d29_star, d30_star
                )
                if first is None:
                    continue

                subframes = [_with_index(first, offset)]
                _, _, d29, d30 = _recover_subframe(
                    bits[offset : offset + SUBFRAME_BITS], d29_star, d30_star
                )
                cursor = offset + SUBFRAME_BITS
                while cursor + SUBFRAME_BITS <= len(bits):
                    if max_subframes is not None and len(subframes) >= max_subframes:
                        break
                    window = bits[cursor : cursor + SUBFRAME_BITS]
                    nxt = _try_subframe(window, d29, d30)
                    if nxt is None:
                        break
                    subframes.append(_with_index(nxt, cursor))
                    _, _, d29, d30 = _recover_subframe(window, d29, d30)
                    cursor += SUBFRAME_BITS

                if len(subframes) > len(best.subframes):
                    best = LnavDecodeResult(
                        subframes=subframes, inverted=inverted, bit_offset=offset
                    )
                break  # this offset produced a valid subframe; no need for d29=1
            if best.subframes and best.bit_offset == offset:
                # Found a run from here.  Later offsets can only find the same run
                # shifted, so stop scanning under this polarity.
                break
    return best


def _with_index(subframe: LnavSubframe, symbol_index: int) -> LnavSubframe:
    return LnavSubframe(
        subframe_id=subframe.subframe_id,
        tow_count=subframe.tow_count,
        bits=subframe.bits,
        symbol_index=symbol_index,
        alert=subframe.alert,
        anti_spoof=subframe.anti_spoof,
    )


# ---------------------------------------------------------------------------
# Subframe content
#
# Bit numbers are Figure 20-1's, scale factors are Table 20-I (clock) and
# Table 20-III (ephemeris).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Subframe1:
    """Week number, accuracy, health, and the SV clock correction."""

    week: int
    ura_index: int
    health: int
    iodc: int
    l2_codes: int
    l2_p_data_flag: int
    tgd: float
    toc: float
    af0: float
    af1: float
    af2: float


@dataclass(frozen=True)
class Subframe2:
    """First half of the ephemeris."""

    iode: int
    Crs: float
    deln: float
    M0: float
    Cuc: float
    e: float
    Cus: float
    sqrt_a: float
    toe: float
    fit_interval_flag: int
    aodo: int


@dataclass(frozen=True)
class Subframe3:
    """Second half of the ephemeris."""

    Cic: float
    Omega0: float
    Cis: float
    i0: float
    Crc: float
    omega: float
    Omega_dot: float
    iode: int
    i_dot: float


@dataclass(frozen=True)
class IonoUtcParameters:
    """Klobuchar coefficients and the GPS-UTC offset, from subframe 4 page 18."""

    alpha: tuple[float, float, float, float]
    beta: tuple[float, float, float, float]
    A0: float
    A1: float
    delta_t_ls: int
    t_ot: float
    week_t: int
    week_lsf: int
    day_number: int
    delta_t_lsf: int


def parse_subframe_1(subframe: LnavSubframe) -> Subframe1:
    if subframe.subframe_id != 1:
        raise ValueError(f"expected subframe 1, got {subframe.subframe_id}")
    return Subframe1(
        week=int(subframe.field(61, 10)),
        l2_codes=int(subframe.field(71, 2)),
        ura_index=int(subframe.field(73, 4)),
        health=int(subframe.field(77, 6)),
        # IODC is split: 2 MSBs at the end of word 3, 8 LSBs at the start of word 8.
        iodc=int(subframe.split_field(83, 2, 211, 8)),
        l2_p_data_flag=int(subframe.field(91, 1)),
        tgd=subframe.field(197, 8, signed=True, scale=2.0**-31),
        toc=subframe.field(219, 16, scale=2.0**4),
        af2=subframe.field(241, 8, signed=True, scale=2.0**-55),
        af1=subframe.field(249, 16, signed=True, scale=2.0**-43),
        af0=subframe.field(271, 22, signed=True, scale=2.0**-31),
    )


def parse_subframe_2(subframe: LnavSubframe) -> Subframe2:
    if subframe.subframe_id != 2:
        raise ValueError(f"expected subframe 2, got {subframe.subframe_id}")
    return Subframe2(
        iode=int(subframe.field(61, 8)),
        Crs=subframe.field(69, 16, signed=True, scale=2.0**-5),
        deln=subframe.field(91, 16, signed=True, scale=2.0**-43),
        M0=subframe.split_field(107, 8, 121, 24, signed=True, scale=2.0**-31),
        Cuc=subframe.field(151, 16, signed=True, scale=2.0**-29),
        e=subframe.split_field(167, 8, 181, 24, scale=2.0**-33),
        Cus=subframe.field(211, 16, signed=True, scale=2.0**-29),
        sqrt_a=subframe.split_field(227, 8, 241, 24, scale=2.0**-19),
        toe=subframe.field(271, 16, scale=2.0**4),
        fit_interval_flag=int(subframe.field(287, 1)),
        aodo=int(subframe.field(288, 5)),
    )


def parse_subframe_3(subframe: LnavSubframe) -> Subframe3:
    if subframe.subframe_id != 3:
        raise ValueError(f"expected subframe 3, got {subframe.subframe_id}")
    return Subframe3(
        Cic=subframe.field(61, 16, signed=True, scale=2.0**-29),
        Omega0=subframe.split_field(77, 8, 91, 24, signed=True, scale=2.0**-31),
        Cis=subframe.field(121, 16, signed=True, scale=2.0**-29),
        i0=subframe.split_field(137, 8, 151, 24, signed=True, scale=2.0**-31),
        Crc=subframe.field(181, 16, signed=True, scale=2.0**-5),
        omega=subframe.split_field(197, 8, 211, 24, signed=True, scale=2.0**-31),
        Omega_dot=subframe.field(241, 24, signed=True, scale=2.0**-43),
        iode=int(subframe.field(271, 8)),
        i_dot=subframe.field(279, 14, signed=True, scale=2.0**-43),
    )


def parse_iono_utc(subframe: LnavSubframe) -> IonoUtcParameters | None:
    """
    Ionospheric and UTC parameters, if this is subframe 4 page 18.

    Returns None for any other page rather than raising: a caller sweeping every
    decoded subframe looking for this one should not have to pre-filter.
    """
    if subframe.subframe_id != 4:
        return None
    if int(subframe.field(63, 6)) != IONO_UTC_PAGE_SV_ID:
        return None
    alpha = tuple(
        subframe.field(start, 8, signed=True, scale=_ALPHA_SCALES[i])
        for i, start in enumerate(_ALPHA_STARTS)
    )
    beta = tuple(
        subframe.field(start, 8, signed=True, scale=_BETA_SCALES[i])
        for i, start in enumerate(_BETA_STARTS)
    )
    return IonoUtcParameters(
        alpha=alpha,
        beta=beta,
        A1=subframe.field(151, 24, signed=True, scale=2.0**-50),
        A0=subframe.split_field(181, 24, 211, 8, signed=True, scale=2.0**-30),
        t_ot=subframe.field(219, 8, scale=2.0**12),
        week_t=int(subframe.field(227, 8)),
        delta_t_ls=int(subframe.field(241, 8, signed=True)),
        week_lsf=int(subframe.field(249, 8)),
        day_number=int(subframe.field(257, 8)),
        delta_t_lsf=int(subframe.field(271, 8, signed=True)),
    )


def assemble_ephemeris(
    result: LnavDecodeResult, sat_id: str
) -> LnavEphemeris | None:
    """
    Combine subframes 1, 2 and 3 into one ephemeris.

    Returns None unless all three are present and their issue-of-data fields agree.
    That agreement check is not optional bookkeeping: IODE appears in both subframe
    2 and 3 and must match the eight LSBs of subframe 1's IODC (IS-GPS-200N
    20.3.4.4).  When it does not, an upload landed mid-frame and the halves describe
    different orbits.
    """
    ones, twos, threes = result.of_id(1), result.of_id(2), result.of_id(3)
    if not (ones and twos and threes):
        return None

    s1 = parse_subframe_1(ones[-1])
    s2 = parse_subframe_2(twos[-1])
    s3 = parse_subframe_3(threes[-1])

    if s2.iode != s3.iode:
        return None
    if s2.iode != (s1.iodc & 0xFF):
        return None

    return LnavEphemeris(
        sat_id=sat_id,
        week=s1.week,
        toe=s2.toe,
        sqrt_a=s2.sqrt_a,
        e=s2.e,
        i0=s3.i0,
        i_dot=s3.i_dot,
        Omega0=s3.Omega0,
        Omega_dot=s3.Omega_dot,
        omega=s3.omega,
        M0=s2.M0,
        deln=s2.deln,
        Cuc=s2.Cuc,
        Cus=s2.Cus,
        Crc=s3.Crc,
        Crs=s2.Crs,
        Cic=s3.Cic,
        Cis=s3.Cis,
        toc=s1.toc,
        af0=s1.af0,
        af1=s1.af1,
        af2=s1.af2,
        tgd=s1.tgd,
        iode=s2.iode,
        health=s1.health,
    )


# ---------------------------------------------------------------------------
# Encoding -- for tests and for building synthetic signals
# ---------------------------------------------------------------------------


def build_subframe(
    *,
    subframe_id: int,
    tow_count: int,
    data: np.ndarray | None = None,
    d29_star: int = 0,
    d30_star: int = 0,
    alert: bool = False,
    anti_spoof: bool = False,
) -> tuple[np.ndarray, int, int]:
    """
    Assemble one transmittable 300-bit subframe with a correct parity chain.

    `data` is the full 300-bit template whose *data* positions carry the content;
    the TLM and HOW words and every parity field are overwritten here.  Returns the
    subframe together with the trailing `(d29_star, d30_star)` so a caller building
    a run of them can chain correctly -- getting that chain wrong is the one
    mistake that makes a synthetic LNAV stream undecodable in a way that looks like
    a decoder bug.
    """
    if not 1 <= subframe_id <= 5:
        raise ValueError(f"subframe id must be 1-5, got {subframe_id}")
    if not 0 <= tow_count < (1 << 17):
        raise ValueError(f"the TOW count field is 17 bits; {tow_count} does not fit")

    source = np.zeros(SUBFRAME_BITS, dtype=np.uint8) if data is None else np.asarray(
        data, dtype=np.uint8
    ).copy()
    if len(source) != SUBFRAME_BITS:
        raise ValueError(f"a subframe is {SUBFRAME_BITS} bits, got {len(source)}")

    # Word 1: preamble, then a TLM message we are free to choose.
    source[0:8] = PREAMBLE_BITS
    # Word 2: TOW count, alert, anti-spoof, subframe id, then two bits chosen so
    # the word's last two parity bits come out zero.  Real SVs do that; here it
    # only has to be consistent, and zeros are consistent.
    source[30:47] = prim.bits_from_int(tow_count, 17)
    source[47] = 1 if alert else 0
    source[48] = 1 if anti_spoof else 0
    source[49:52] = prim.bits_from_int(subframe_id, 3)

    out = np.zeros(SUBFRAME_BITS, dtype=np.uint8)
    for w in range(WORDS_PER_SUBFRAME):
        start = w * WORD_BITS
        word = prim.lnav_encode_word(
            source[start : start + DATA_BITS_PER_WORD], d29_star, d30_star
        )
        out[start : start + WORD_BITS] = word
        d29_star, d30_star = int(word[28]), int(word[29])
    return out, d29_star, d30_star


def build_frame(
    *, first_tow_count: int, subframe_ids=(1, 2, 3, 4, 5), templates=None
) -> np.ndarray:
    """A run of subframes with the parity chain carried correctly across all of them."""
    d29, d30 = 0, 0
    pieces = []
    for i, sid in enumerate(subframe_ids):
        template = None if templates is None else templates.get(sid)
        bits, d29, d30 = build_subframe(
            subframe_id=sid,
            tow_count=first_tow_count + i,
            data=template,
            d29_star=d29,
            d30_star=d30,
        )
        pieces.append(bits)
    return np.concatenate(pieces)


def set_field(bits: np.ndarray, start: int, length: int, value: int) -> np.ndarray:
    """Write a field into a subframe template, using Figure 20-1's bit numbering."""
    out = np.asarray(bits, dtype=np.uint8).copy()
    out[start - 1 : start - 1 + length] = prim.bits_from_int(
        value & ((1 << length) - 1), length
    )
    return out


def set_split_field(
    bits: np.ndarray,
    msb_start: int,
    msb_length: int,
    lsb_start: int,
    lsb_length: int,
    value: int,
) -> np.ndarray:
    """The encoder half of `LnavSubframe.split_field`."""
    raw = value & ((1 << (msb_length + lsb_length)) - 1)
    out = set_field(bits, msb_start, msb_length, raw >> lsb_length)
    return set_field(out, lsb_start, lsb_length, raw & ((1 << lsb_length) - 1))
