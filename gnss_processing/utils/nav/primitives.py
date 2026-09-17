"""
Channel coding primitives shared by every GPS navigation message format.

Nothing here knows what a navigation message *means* -- these are the error
detection and correction codes the four formats are built from, plus the field
extraction helper every message parser needs:

    LNAV (L1 C/A)   (32,26) Hamming word parity          `lnav_parity`
    CNAV (L2C, L5)  rate 1/2 K=7 convolutional + CRC-24Q `ViterbiDecoder`, `crc24q`
    CNAV-2 (L1C)    BCH(51,8) on the TOI + interleaving  `bch_51_8_decode`, `block_deinterleave`

Every function has an encoder next to its decoder.  That is not symmetry for its
own sake: no real L1/L2 collect ships with this repository, so the only way to
test these decoders is to encode a known frame, push it through
`tests/synthetic.py`, and decode it back out.  An encoder that is only used by
tests is still load-bearing.

Bit conventions
---------------
Bits are `np.uint8` arrays of 0/1, MSB first, in transmission order.  That is the
order the interface specs number them in ("bit 1" is the first transmitted), so a
field at spec bits 39-46 is `bits[38:46]` and the off-by-one lives in one place --
`unpack_field` -- rather than in every parser.

Soft symbols are floats whose *sign* carries the bit: positive means 0, negative
means 1, i.e. the usual `1 - 2*b` BPSK mapping.  Magnitude is confidence, and the
Viterbi decoder uses it; the hard-decision paths only look at the sign.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# ---------------------------------------------------------------------------
# Bit plumbing
# ---------------------------------------------------------------------------


def bits_from_int(value: int, length: int) -> np.ndarray:
    """`value` as `length` bits, MSB first."""
    if value < 0 or value >= (1 << length):
        raise ValueError(f"{value} does not fit in {length} unsigned bits")
    return np.array([(value >> (length - 1 - i)) & 1 for i in range(length)], dtype=np.uint8)


def int_from_bits(bits: np.ndarray) -> int:
    """Inverse of `bits_from_int`.  MSB first, unsigned."""
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value


def unpack_field(
    bits: np.ndarray,
    start: int,
    length: int,
    *,
    signed: bool = False,
    scale: float = 1.0,
) -> float | int:
    """
    Pull one field out of a message, addressed the way the interface specs do.

    `start` is the spec's 1-based bit number of the field's first (most
    significant) bit, so IS-GPS-200's "bits 39 through 46" is
    `unpack_field(bits, 39, 8)`.  Getting that convention wrong is the single
    easiest way to produce an ephemeris that is subtly, plausibly wrong, so it is
    spelled once here and never repeated in a parser.

    `signed` reads the field as two's complement, which is how every scaled
    parameter in every GPS message is stated.  `scale` is the spec's LSB weight;
    it is applied last, and returning a float only when it matters keeps counters
    and flags as exact ints.
    """
    if start < 1:
        raise ValueError(f"bit numbering is 1-based; got start={start}")
    if start - 1 + length > len(bits):
        raise ValueError(
            f"field at bits {start}..{start + length - 1} runs past the "
            f"{len(bits)}-bit message"
        )
    raw = int_from_bits(bits[start - 1 : start - 1 + length])
    if signed and raw >= (1 << (length - 1)):
        raw -= 1 << length
    if scale == 1.0:
        return raw
    return raw * scale


def hard_decision(symbols: np.ndarray) -> np.ndarray:
    """Soft symbols to bits.  Negative means 1, per the module's sign convention."""
    return (np.asarray(symbols) < 0).astype(np.uint8)


# ---------------------------------------------------------------------------
# CRC-24Q -- CNAV and CNAV-2 (IS-GPS-200N 30.3.5.1)
# ---------------------------------------------------------------------------

# g(X) has g_i = 1 for i in {0,1,3,4,5,6,7,10,11,14,17,18,23,24} and 0 otherwise.
# Written as an integer with X^i in bit i, that is exactly 0x1864CFB.
CRC24Q_POLYNOMIAL = 0x1864CFB


def crc24q(bits: np.ndarray) -> int:
    """
    The 24-bit CRC of `bits`, computed forward from a seed of 0.

    A CNAV message is 300 bits: 276 of information followed by these 24, so
    `crc24q(message[:276])` should equal `int_from_bits(message[276:])`.  Prefer
    `crc24q_check`, which says that in one call and cannot get the split wrong.
    """
    register = 0
    for bit in np.asarray(bits, dtype=np.uint8):
        register ^= int(bit) << 23
        register <<= 1
        if register & (1 << 24):
            register ^= CRC24Q_POLYNOMIAL
        register &= 0xFFFFFF
    return register


def crc24q_check(message: np.ndarray, *, parity_length: int = 24) -> bool:
    """True when a message's trailing CRC matches its information bits."""
    return crc24q(message[:-parity_length]) == int_from_bits(message[-parity_length:])


def crc24q_append(information: np.ndarray) -> np.ndarray:
    """`information` with its CRC-24Q appended -- the encoder side, for tests."""
    return np.concatenate([information, bits_from_int(crc24q(information), 24)])


# ---------------------------------------------------------------------------
# Rate 1/2, K=7 convolutional code -- CNAV on L2C and L5
# (IS-GPS-200N Figure 3-14, IS-GPS-705J Figure 3-5)
# ---------------------------------------------------------------------------

# G1 = 171 octal, G2 = 133 octal, over a 7-bit register whose bit 6 is the current
# input and whose bits 5..0 are the six previous inputs, most recent first.  G1 is
# transmitted first.  Neither output is inverted -- some other systems using the
# same generators invert G2, and doing that here silently costs the decoder every
# frame it would otherwise find.
CONV_G1 = 0o171
CONV_G2 = 0o133
CONV_CONSTRAINT_LENGTH = 7
CONV_NUM_STATES = 1 << (CONV_CONSTRAINT_LENGTH - 1)


def _parity_of(value: int) -> int:
    return bin(value).count("1") & 1


def _build_trellis() -> tuple[np.ndarray, np.ndarray]:
    """
    Precompute the code's structure once, as lookup tables indexed [state, input].

    `outputs[s, u]` packs the two output bits as `(g1 << 1) | g2`; `next_state[s, u]`
    is where the encoder lands.  Both the encoder and the decoder read these, which
    is what guarantees they cannot drift apart.
    """
    outputs = np.zeros((CONV_NUM_STATES, 2), dtype=np.uint8)
    next_state = np.zeros((CONV_NUM_STATES, 2), dtype=np.uint8)
    for state in range(CONV_NUM_STATES):
        for u in (0, 1):
            register = (u << 6) | state
            g1 = _parity_of(register & CONV_G1)
            g2 = _parity_of(register & CONV_G2)
            outputs[state, u] = (g1 << 1) | g2
            next_state[state, u] = (state >> 1) | (u << 5)
    return outputs, next_state


_TRELLIS_OUTPUTS, _TRELLIS_NEXT = _build_trellis()


def convolutional_encode(bits: np.ndarray, initial_state: int = 0) -> np.ndarray:
    """
    Encode `bits` to twice as many symbols, G1 first, as 0/1 values.

    CNAV encodes continuously across message boundaries -- the register holds the
    last six bits of the previous message when a new one starts (IS-GPS-200N
    3.3.3.1.1).  `initial_state` is how a caller reproduces that; leave it 0 only
    when encoding a stream from its very beginning.
    """
    bits = np.asarray(bits, dtype=np.uint8)
    symbols = np.zeros(2 * len(bits), dtype=np.uint8)
    state = initial_state
    for i, u in enumerate(bits):
        packed = _TRELLIS_OUTPUTS[state, u]
        symbols[2 * i] = packed >> 1
        symbols[2 * i + 1] = packed & 1
        state = _TRELLIS_NEXT[state, u]
    return symbols


@dataclass
class ViterbiResult:
    bits: np.ndarray
    """Decoded information bits, half as many as the symbols supplied."""

    final_state: int
    """Encoder state the winning path ended in."""

    path_metric: float
    """Correlation metric of the winning path.  Comparable only between decodes
    of the same length -- useful for choosing between two candidate polarities,
    not as an absolute quality measure."""


class ViterbiDecoder:
    """
    Soft-decision Viterbi decoder for the CNAV convolutional code.

    Decodes a whole block at once and tracebacks from the best-metric final state,
    rather than streaming with a fixed traceback depth.  For the block sizes here
    that is both simpler and strictly better: 60 s of L5 is 6000 symbols, so the
    survivor table is 3000x64 bytes, and a full traceback has no depth parameter
    to get wrong.

    The encoder's starting state is unknown mid-stream, so the decoder starts with
    all 64 states equally likely.  The cost is that the first handful of decoded
    bits are unreliable -- the trellis needs about five constraint lengths to
    forget a wrong start, which is the same 35-bit settling the spec mentions for
    the decoding delay.  `decode` therefore never promises its leading bits; frame
    sync and the CRC are what actually establish trust.
    """

    def __init__(self) -> None:
        self.outputs = _TRELLIS_OUTPUTS
        self.next_state = _TRELLIS_NEXT
        # Branch metric contribution per (packed output, symbol pair) is just a
        # sign pattern, so precompute the +/-1 mapping once.
        self._g1_sign = np.array(
            [1.0 - 2.0 * (packed >> 1) for packed in range(4)], dtype=np.float64
        )
        self._g2_sign = np.array(
            [1.0 - 2.0 * (packed & 1) for packed in range(4)], dtype=np.float64
        )

    def decode(self, symbols: np.ndarray) -> ViterbiResult:
        """
        `symbols` are soft values, G1 and G2 interleaved, two per information bit.

        Positive means the encoder emitted 0.  An odd-length input is an error
        rather than a truncation, because silently dropping a symbol shifts every
        subsequent bit and produces confident nonsense.
        """
        symbols = np.asarray(symbols, dtype=np.float64)
        if len(symbols) % 2:
            raise ValueError(
                f"{len(symbols)} symbols is not a whole number of G1/G2 pairs; "
                "the symbol stream is misaligned"
            )
        num_bits = len(symbols) // 2
        if num_bits == 0:
            return ViterbiResult(np.zeros(0, dtype=np.uint8), 0, 0.0)

        received = symbols.reshape(num_bits, 2)

        # Branch metric for taking input u from state s is the correlation of the
        # received pair with what that branch would have transmitted.  Larger is
        # better, so the survivor is an argmax.
        metrics = np.zeros(CONV_NUM_STATES, dtype=np.float64)
        # Survivors hold the winning *predecessor state*, not the winning input.
        # The input cannot stand in for it: next_state = (prev >> 1) | (u << 5)
        # discards prev's low bit, so two predecessors reach the same state on the
        # same input and knowing u alone leaves the traceback guessing.
        survivors = np.zeros((num_bits, CONV_NUM_STATES), dtype=np.uint8)

        states = np.arange(CONV_NUM_STATES)
        # For each *destination* state there are exactly two predecessors.  Build
        # that inverse map once so the inner loop is two vectorized adds.
        pred_state = np.zeros((CONV_NUM_STATES, 2), dtype=np.intp)
        pred_input = np.zeros((CONV_NUM_STATES, 2), dtype=np.uint8)
        seen = np.zeros(CONV_NUM_STATES, dtype=np.intp)
        for s in range(CONV_NUM_STATES):
            for u in (0, 1):
                dest = self.next_state[s, u]
                pred_state[dest, seen[dest]] = s
                pred_input[dest, seen[dest]] = u
                seen[dest] += 1
        pred_packed = self.outputs[pred_state, pred_input]

        for k in range(num_bits):
            r1, r2 = received[k, 0], received[k, 1]
            branch = self._g1_sign[pred_packed] * r1 + self._g2_sign[pred_packed] * r2
            candidates = metrics[pred_state] + branch  # (num_states, 2)
            choice = np.argmax(candidates, axis=1)
            metrics = candidates[states, choice]
            survivors[k] = pred_state[states, choice]
            # Renormalize so a long stream cannot drift toward overflow.  Only the
            # differences between metrics matter, never their absolute value.
            metrics -= metrics.max()

        # Traceback.  The input that produced a state is readable off the state
        # itself -- next_state = (prev >> 1) | (u << 5) puts u in bit 5 -- so the
        # survivor table only has to carry where each path came from.
        best = int(np.argmax(metrics))
        bits = np.zeros(num_bits, dtype=np.uint8)
        state = best
        for k in range(num_bits - 1, -1, -1):
            bits[k] = (state >> 5) & 1
            state = int(survivors[k, state])
        return ViterbiResult(bits, best, float(np.max(metrics)))


# ---------------------------------------------------------------------------
# LNAV word parity -- L1 C/A (IS-GPS-200N Table 20-XIV)
# ---------------------------------------------------------------------------

# Each computed parity bit is the XOR of D29* or D30* with a fixed subset of the
# 24 source data bits.  Transcribed directly from Table 20-XIV, as 1-based data
# bit numbers so they can be checked against the spec line by line.
_LNAV_PARITY_TAPS: tuple[tuple[int, ...], ...] = (
    (1, 2, 3, 5, 6, 10, 11, 12, 13, 14, 17, 18, 20, 23),      # D25, seeded by D29*
    (2, 3, 4, 6, 7, 11, 12, 13, 14, 15, 18, 19, 21, 24),      # D26, seeded by D30*
    (1, 3, 4, 5, 7, 8, 12, 13, 14, 15, 16, 19, 20, 22),       # D27, seeded by D29*
    (2, 4, 5, 6, 8, 9, 13, 14, 15, 16, 17, 20, 21, 23),       # D28, seeded by D30*
    (1, 3, 5, 6, 7, 9, 10, 14, 15, 16, 17, 18, 21, 22, 24),   # D29, seeded by D30*
    (3, 5, 6, 8, 9, 10, 11, 13, 15, 19, 22, 23, 24),          # D30, seeded by D29*
)
# Which of the previous word's last two bits seeds each equation: 29 or 30.
_LNAV_PARITY_SEED: tuple[int, ...] = (29, 30, 29, 30, 30, 29)


def lnav_parity(data_bits: np.ndarray, d29_star: int, d30_star: int) -> np.ndarray:
    """
    The six parity bits D25..D30 for one 30-bit LNAV word.

    `data_bits` is d1..d24, the word's *source* data before the D30* inversion --
    not the 24 bits as transmitted.  `d29_star`/`d30_star` are the last two bits of
    the previous transmitted word, which is what links words into a subframe and
    subframes into a frame.
    """
    if len(data_bits) != 24:
        raise ValueError(f"an LNAV word carries 24 data bits, got {len(data_bits)}")
    d = np.asarray(data_bits, dtype=np.uint8)
    parity = np.zeros(6, dtype=np.uint8)
    for i, (taps, seed) in enumerate(zip(_LNAV_PARITY_TAPS, _LNAV_PARITY_SEED)):
        value = d29_star if seed == 29 else d30_star
        for tap in taps:
            value ^= int(d[tap - 1])
        parity[i] = value & 1
    return parity


def lnav_encode_word(data_bits: np.ndarray, d29_star: int, d30_star: int) -> np.ndarray:
    """
    One transmitted 30-bit word: D1..D24 (data, inverted when D30* is 1) then D25..D30.

    The inversion is the SV-side half of the algorithm -- D_n = d_n XOR D30* for
    n in 1..24 -- and it is why a receiver must recover data before checking parity
    rather than the other way around.
    """
    d = np.asarray(data_bits, dtype=np.uint8)
    parity = lnav_parity(d, d29_star, d30_star)
    transmitted = d ^ (d30_star & 1)
    return np.concatenate([transmitted, parity])


def lnav_decode_word(
    word_bits: np.ndarray, d29_star: int, d30_star: int
) -> tuple[np.ndarray, bool]:
    """
    Recover d1..d24 from a transmitted word and say whether its parity checks.

    Returns `(data_bits, ok)`.  This is the "user parity algorithm" of
    IS-GPS-200N 20.3.5.2 and Figure 20-5: undo the D30* inversion first, then
    recompute D25..D30 from the recovered data and compare against what arrived.
    """
    if len(word_bits) != 30:
        raise ValueError(f"an LNAV word is 30 bits, got {len(word_bits)}")
    word = np.asarray(word_bits, dtype=np.uint8)
    data = word[:24] ^ (d30_star & 1)
    expected = lnav_parity(data, d29_star, d30_star)
    return data, bool(np.array_equal(expected, word[24:]))


# ---------------------------------------------------------------------------
# CNAV-2 subframe 1 -- BCH(51,8) on the time of interval (IS-GPS-800J 3.5.3.1)
# ---------------------------------------------------------------------------

# Subframe 1 carries a 9-bit TOI as a 52-symbol block.  The eight LSBs seed an
# 8-stage LFSR (IS-GPS-800J Figure 3.2-4) which is then shifted 51 times; the ninth
# bit (the MSB) is modulo-2 added to all 51 symbols and also prepended as the
# 52-symbol message's own MSB.
#
# The figure fixes every detail that the prose leaves open, and all of them matter:
#   * polynomial 1 + X + X^4 + X^5 + X^6 + X^7 + X^8, i.e. 763 octal -- feedback
#     taps on stages 1, 4, 5, 6, 7 and 8;
#   * OUTPUT is taken from stage 8, with the shift running stage 1 -> stage 8;
#   * "initial conditions are the 8 LSBs of TOI data (MSB is shifted in first)",
#     so the data MSB has travelled furthest and sits in stage 8, and the LSB in
#     stage 1.
# Getting the tap set or the seed order wrong still yields 256 distinct codewords,
# so a round-trip test cannot catch it.  What does catch it is the distance
# spectrum: this construction has d_min = 24, which is BCH(51,8)'s, while wrong
# arrangements fall to 19 or worse.  `tests/test_nav_primitives.py` asserts it.
BCH_51_8_POLYNOMIAL = 0o763
BCH_51_8_LENGTH = 51
BCH_51_8_DATA_BITS = 8
BCH_51_8_MESSAGE_LENGTH = 52
BCH_51_8_MINIMUM_DISTANCE = 24

# Stage indices (0-based, so stage k is index k-1) feeding the modulo-2 adder.
_BCH_51_8_TAPS = (0, 3, 4, 5, 6, 7)


def _bch_51_8_codeword(value: int) -> np.ndarray:
    """The 51 symbols an 8-bit seed shifts out of the generator."""
    stages = [(value >> i) & 1 for i in range(8)]  # index 0 = stage 1 = data LSB
    out = np.zeros(BCH_51_8_LENGTH, dtype=np.uint8)
    for i in range(BCH_51_8_LENGTH):
        out[i] = stages[7]  # OUTPUT, from stage 8
        feedback = 0
        for tap in _BCH_51_8_TAPS:
            feedback ^= stages[tap]
        stages = [feedback] + stages[:7]
    return out


_BCH_51_8_TABLE = np.array(
    [_bch_51_8_codeword(v) for v in range(1 << BCH_51_8_DATA_BITS)], dtype=np.uint8
)


def bch_51_8_encode(toi: int) -> np.ndarray:
    """
    The 52-symbol subframe 1 message carrying a 9-bit TOI.

    Symbol 0 is the TOI's MSB; symbols 1..51 are the BCH codeword of the eight LSBs
    with that same MSB added to every one of them.
    """
    if not 0 <= toi < 512:
        raise ValueError(f"TOI is 9 bits; {toi} does not fit")
    msb = (toi >> 8) & 1
    codeword = _BCH_51_8_TABLE[toi & 0xFF] ^ msb
    return np.concatenate([np.array([msb], dtype=np.uint8), codeword])


def bch_51_8_decode(symbols: np.ndarray) -> tuple[int, float]:
    """
    Maximum-likelihood decode of the 52-symbol subframe 1 block.

    Follows the decoding technique the spec itself suggests: correlate the 51 code
    symbols against all 256 hypotheses built for MSB = 0, take the largest
    *absolute* correlation as the eight LSBs, and read the MSB off that
    correlation's sign.  With a codebook this small, exhaustive correlation is both
    simpler than algebraic decoding and strictly better, because it uses the soft
    symbols that algebraic decoding would throw away.

    The message's own leading MSB symbol is deliberately not used for the decision.
    It is one symbol against fifty-one, so folding it in would barely move the
    result; it is returned to the caller's reach as a redundant cross-check instead.

    Returns `(toi, confidence)` where confidence is the winning correlation over the
    runner-up's -- 1.0 is a coin flip, larger is better.  That is the same shape of
    statistic `secondary_code.OverlaySynchroniser` reports, so the two sync
    mechanisms can be gated on comparable numbers.
    """
    symbols = np.asarray(symbols, dtype=np.float64)
    if len(symbols) != BCH_51_8_MESSAGE_LENGTH:
        raise ValueError(
            f"subframe 1 is {BCH_51_8_MESSAGE_LENGTH} symbols, got {len(symbols)}"
        )
    replicas = 1.0 - 2.0 * _BCH_51_8_TABLE  # +/-1 per codeword
    correlations = replicas @ symbols[1:]
    magnitudes = np.abs(correlations)
    order = np.argsort(magnitudes)[::-1]
    best, runner_up = magnitudes[order[0]], magnitudes[order[1]]
    confidence = float(best / runner_up) if runner_up > 0 else float("inf")
    msb = 0 if correlations[order[0]] > 0 else 1
    return (msb << 8) | int(order[0]), confidence


# ---------------------------------------------------------------------------
# CNAV-2 block interleaver (IS-GPS-800J 3.5.4)
# ---------------------------------------------------------------------------

CNAV2_INTERLEAVER_ROWS = 38
CNAV2_INTERLEAVER_COLUMNS = 46


def block_interleave(
    symbols: np.ndarray, rows: int = CNAV2_INTERLEAVER_ROWS, columns: int = CNAV2_INTERLEAVER_COLUMNS
) -> np.ndarray:
    """
    Write by rows, read by columns -- the CNAV-2 interleaver over subframes 2 and 3.

    Its job is to break up burst errors so the LDPC decoder sees something close to
    independent symbol errors.  38x46 is 1748 symbols, which is subframe 2's 1200
    plus subframe 3's 548.
    """
    symbols = np.asarray(symbols)
    if len(symbols) != rows * columns:
        raise ValueError(f"interleaver takes {rows * columns} symbols, got {len(symbols)}")
    return symbols.reshape(rows, columns).T.reshape(-1).copy()


def block_deinterleave(
    symbols: np.ndarray, rows: int = CNAV2_INTERLEAVER_ROWS, columns: int = CNAV2_INTERLEAVER_COLUMNS
) -> np.ndarray:
    """Inverse of `block_interleave`: write by columns, read by rows."""
    symbols = np.asarray(symbols)
    if len(symbols) != rows * columns:
        raise ValueError(f"deinterleaver takes {rows * columns} symbols, got {len(symbols)}")
    return symbols.reshape(columns, rows).T.reshape(-1).copy()
