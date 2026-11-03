"""
GPS CNAV-2 -- the navigation message on L1C.

CNAV-2 is structured unlike the other three.  There is no repeating 300-bit
message; there is an 18-second **frame** of 1800 symbols split into three
subframes that use three different codes:

    subframe 1     52 symbols    9-bit TOI, BCH(51,8)          -> time
    subframe 2   1200 symbols    600 bits, LDPC(1200,600)      -> clock+ephemeris
    subframe 3    548 symbols    274 bits, LDPC(548,274)       -> paged data

Subframes 2 and 3 are then block-interleaved together as one 1748-symbol unit
(38 rows x 46 columns) before transmission, so neither can be read without
de-interleaving both.

**What this module does.**  Subframe 1 (the TOI) is decoded fully.  Subframes 2
and 3 are de-interleaved, then read as systematic codewords: hard decisions on
the information symbols, accepted only if CRC-24Q passes (`read_subframe`).  That
needs no parity-check matrices, but it has no error correction: one wrong symbol
loses the frame.  The LDPC decoder (IS-GPS-800 tables 6.2-2 to 6.2-13) is still to
be added; `read_subframe` is the one function it replaces.

Frame synchronisation has a shortcut worth knowing about.  L1C's pilot carries the
L1CO overlay, 1800 chips at one per 10 ms code period -- exactly one frame.  So a
channel that has synced its overlay already knows where the frame starts, and
`decode` accepts that offset directly.  The blind search is there for callers that
do not have it, and for testing.

Time semantics (IS-GPS-800J 3.5.2), which are unlike CNAV's:

    TOI counts 18-second frames since the start of a two-hour period, 0 to 399,
    and refers to the start of the *next* frame.  The two-hour period itself is
    numbered by ITOW, which lives in subframe 2 of that next frame.  So a full
    time of week needs both:  TOW = ITOW * 7200 + TOI * 18.

A TOI of 511 (all ones) is the default the SV broadcasts when message generation
failed, and must never be used as a time.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import primitives as prim

FRAME_SYMBOLS = 1800
SUBFRAME_1_SYMBOLS = prim.BCH_51_8_MESSAGE_LENGTH  # 52
SUBFRAME_2_SYMBOLS = 1200
SUBFRAME_3_SYMBOLS = 548
INTERLEAVED_SYMBOLS = SUBFRAME_2_SYMBOLS + SUBFRAME_3_SYMBOLS  # 1748

SUBFRAME_2_BITS = 600
SUBFRAME_3_BITS = 274

FRAME_DURATION_S = 18.0
TWO_HOUR_PERIOD_S = 7200.0
SYMBOL_PERIOD_MS = 10  # one L1CD code period

TOI_MAX = 399
TOI_DEFAULT = 511  # all ones: message generation failure


@dataclass(frozen=True)
class Cnav2Frame:
    """One 18-second CNAV-2 frame, with subframe 1 decoded and 2/3 de-interleaved."""

    toi: int
    toi_confidence: float
    subframe_2_symbols: np.ndarray
    subframe_3_symbols: np.ndarray
    symbol_index: int
    """Where this frame's first symbol sits in the supplied stream."""

    @property
    def toi_is_valid(self) -> bool:
        """
        A TOI outside 0-399 is not a time.

        511 is the documented failure default, but any out-of-range value means the
        same thing: subframe 1 did not carry usable timing this frame.
        """
        return 0 <= self.toi <= TOI_MAX

    def tow_at_next_frame_start_s(self, itow: int) -> float:
        """
        Time of week at the start of the next frame, given that frame's ITOW.

        `itow` must come from subframe 2 of the *next* frame, not this one -- the
        spec is explicit and the distinction is an 18-second error.  Since subframe
        2 needs LDPC, a caller without it can still supply an ITOW obtained some
        other way (a rough time, or another signal's decoded time of week).
        """
        if not self.toi_is_valid:
            raise ValueError(
                f"TOI {self.toi} is outside the valid range 0-{TOI_MAX}; "
                "subframe 1 reported a message generation failure"
            )
        return itow * TWO_HOUR_PERIOD_S + self.toi * FRAME_DURATION_S

    def tow_at_frame_start_s(self, itow: int) -> float:
        """Time of week at *this* frame's first symbol."""
        return self.tow_at_next_frame_start_s(itow) - FRAME_DURATION_S


def split_frame(symbols: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    One frame's symbols into subframe 1, and de-interleaved subframes 2 and 3.

    The interleaver spans subframes 2 and 3 together, so they must be
    de-interleaved as one 1748-symbol block and only then split -- de-interleaving
    them separately produces two blocks of well-shuffled nonsense.
    """
    symbols = np.asarray(symbols)
    if len(symbols) != FRAME_SYMBOLS:
        raise ValueError(f"a CNAV-2 frame is {FRAME_SYMBOLS} symbols, got {len(symbols)}")
    subframe_1 = symbols[:SUBFRAME_1_SYMBOLS]
    deinterleaved = prim.block_deinterleave(symbols[SUBFRAME_1_SYMBOLS:])
    return (
        subframe_1,
        deinterleaved[:SUBFRAME_2_SYMBOLS],
        deinterleaved[SUBFRAME_2_SYMBOLS:],
    )


def build_frame(
    *, toi: int, subframe_2: np.ndarray | None = None, subframe_3: np.ndarray | None = None
) -> np.ndarray:
    """
    Assemble one transmittable frame: BCH-coded TOI, then interleaved 2 and 3.

    The encoder half of `split_frame`.  Subframe 2 and 3 contents default to the
    alternating pattern the spec prescribes for a default message, which is also
    what a test wants -- a pattern that survives interleaving visibly.
    """
    if subframe_2 is None:
        subframe_2 = np.array(
            [1 - 2 * (i % 2) for i in range(SUBFRAME_2_SYMBOLS)], dtype=float
        )
    if subframe_3 is None:
        subframe_3 = np.array(
            [1 - 2 * ((i + 1) % 2) for i in range(SUBFRAME_3_SYMBOLS)], dtype=float
        )
    subframe_2 = np.asarray(subframe_2, dtype=float)
    subframe_3 = np.asarray(subframe_3, dtype=float)
    if len(subframe_2) != SUBFRAME_2_SYMBOLS:
        raise ValueError(f"subframe 2 is {SUBFRAME_2_SYMBOLS} symbols, got {len(subframe_2)}")
    if len(subframe_3) != SUBFRAME_3_SYMBOLS:
        raise ValueError(f"subframe 3 is {SUBFRAME_3_SYMBOLS} symbols, got {len(subframe_3)}")

    head = 1.0 - 2.0 * prim.bch_51_8_encode(toi).astype(float)
    body = prim.block_interleave(np.concatenate([subframe_2, subframe_3]))
    return np.concatenate([head, body])


def _correlate_toi(candidate_blocks: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Best BCH codeword and its confidence for many candidate subframe-1 blocks.

    `candidate_blocks` is (N, 52).  Returns `(best_value, confidence)` per row.
    Vectorised because the blind frame search evaluates 1800 candidates at once and
    doing that one `bch_51_8_decode` call at a time is a hundred times slower for
    no benefit.
    """
    replicas = 1.0 - 2.0 * prim._BCH_51_8_TABLE  # (256, 51)
    correlations = candidate_blocks[:, 1:] @ replicas.T  # (N, 256)
    magnitudes = np.abs(correlations)
    order = np.argsort(magnitudes, axis=1)
    best_index = order[:, -1]
    rows = np.arange(len(candidate_blocks))
    best = magnitudes[rows, best_index]
    runner_up = magnitudes[rows, order[:, -2]]
    with np.errstate(divide="ignore", invalid="ignore"):
        confidence = np.where(runner_up > 0, best / runner_up, np.inf)
    signs = correlations[rows, best_index]
    values = np.where(signs > 0, best_index, best_index + 256)
    return values, confidence


def find_frame_offset(symbols: np.ndarray) -> tuple[int, float]:
    """
    Locate the frame boundary without help, by trying every one of 1800 offsets.

    At the true offset subframe 1 is a real BCH codeword and correlates strongly
    against exactly one of the 256 hypotheses; anywhere else it is data that has
    been through an interleaver and correlates against everything equally badly.

    Returns `(offset, confidence)`.  A caller whose channel has synced the L1CO
    overlay should skip this entirely and pass that counter to `decode` -- the
    overlay's period is one frame, so it gives the same answer for free and with
    no ambiguity.
    """
    symbols = np.asarray(symbols, dtype=np.float64)
    if len(symbols) < FRAME_SYMBOLS + SUBFRAME_1_SYMBOLS:
        raise ValueError(
            f"blind frame search needs at least {FRAME_SYMBOLS + SUBFRAME_1_SYMBOLS} "
            f"symbols to cover every offset, got {len(symbols)}"
        )
    offsets = np.arange(FRAME_SYMBOLS)
    blocks = np.stack([symbols[o : o + SUBFRAME_1_SYMBOLS] for o in offsets])
    _, confidence = _correlate_toi(blocks)
    best = int(np.argmax(confidence))
    return best, float(confidence[best])


def decode(
    symbols: np.ndarray,
    *,
    frame_offset: int | None = None,
    confidence_threshold: float = 1.5,
) -> list[Cnav2Frame]:
    """
    Decode every whole CNAV-2 frame in a stream of 100 sps soft symbols.

    `frame_offset` is where a frame begins.  Supply it from the channel's L1CO
    overlay counter when tracking; omit it to search blindly.

    Frames whose TOI decodes below `confidence_threshold` are still returned -- the
    caller can see `toi_confidence` and `toi_is_valid` and decide.  Dropping them
    here would hide the one diagnostic that says whether frame sync is real.
    """
    symbols = np.asarray(symbols, dtype=np.float64)
    if frame_offset is None:
        frame_offset, _ = find_frame_offset(symbols)
    if not 0 <= frame_offset < len(symbols):
        raise ValueError(f"frame_offset {frame_offset} is outside the symbol stream")

    frames: list[Cnav2Frame] = []
    cursor = frame_offset
    while cursor + FRAME_SYMBOLS <= len(symbols):
        window = symbols[cursor : cursor + FRAME_SYMBOLS]
        sf1, sf2, sf3 = split_frame(window)
        toi, confidence = prim.bch_51_8_decode(sf1)
        frames.append(
            Cnav2Frame(
                toi=toi,
                toi_confidence=confidence,
                subframe_2_symbols=sf2,
                subframe_3_symbols=sf3,
                symbol_index=cursor,
            )
        )
        cursor += FRAME_SYMBOLS
    return frames


def toi_is_consistent(frames: list[Cnav2Frame]) -> bool:
    """
    Successive frames must advance the TOI by exactly one, modulo 400.

    Worth checking even though BCH(51,8) has a minimum distance of 24: frame sync
    itself could be wrong by a whole frame, and a run of TOIs that steps by one is
    the cheapest evidence that it is not.
    """
    valid = [f for f in frames if f.toi_is_valid]
    if len(valid) < 2:
        return True
    for earlier, later in zip(valid, valid[1:]):
        steps = (later.symbol_index - earlier.symbol_index) // FRAME_SYMBOLS
        if (earlier.toi + steps) % (TOI_MAX + 1) != later.toi:
            return False
    return True


# ---------------------------------------------------------------------------
# Subframes 2 and 3 -- systematic read plus CRC
#
# Both LDPC codes are systematic: the broadcast word is (s; p1; p2), so the first
# 600 (or 274) symbols ARE the information bits, and the rest is parity (ICD
# 3.2.3.4).  A receiver without the parity-check matrices can therefore take hard
# decisions on those symbols and let the CRC-24Q say whether every bit is right.
# This is what the CU Multi-GNSS SDR does for BeiDou B1C ("No LDPC decoding is
# performed temporarily"), and it is safe for the reason in `read_subframe`.
# When the LDPC decoder lands, `read_subframe` is the one place to change.
# ---------------------------------------------------------------------------

SUBFRAME_2_PARITY_FIELD = 24
SUBFRAME_3_PARITY_FIELD = 24


@dataclass(frozen=True)
class SubframeRead:
    """The information bits of one subframe and whether its CRC-24Q passed."""

    bits: np.ndarray
    crc_ok: bool
    inverted: bool
    """True when the symbol polarity had to be flipped for the CRC to pass."""


def read_subframe(soft_symbols: np.ndarray, information_bits: int) -> SubframeRead | None:
    """
    Hard-decide the first `information_bits` symbols and check the CRC, in both polarities.

    Why both polarities: the data axis sign follows the carrier loop, which has a
    half-cycle ambiguity until something fixes it.  The CRC can tell the two
    apart on its own, because a wrong-polarity block is the complement of a valid
    block and fails the check with probability 1 - 2^-24.

    Why this is safe without error correction: a block that passes CRC-24Q is
    wrong with probability about 2^-24 (6e-8).  So a wrong ephemeris is not
    returned; a block with any bit error is returned as None.  The price is that
    one bit error costs the whole 18 s frame, where LDPC would repair it.  At
    100 symbols/s the symbol Es/N0 is C/N0 - 20 dB, so this is rarely a problem
    above about 30 dB-Hz.

    Returns None when neither polarity passes.
    """
    symbols = np.asarray(soft_symbols, dtype=float)[:information_bits]
    if len(symbols) != information_bits:
        raise ValueError(f"need {information_bits} symbols, got {len(symbols)}")
    bits = prim.hard_decision(symbols)
    if prim.crc24q_check(bits):
        return SubframeRead(bits=bits, crc_ok=True, inverted=False)
    flipped = 1 - bits
    if prim.crc24q_check(flipped):
        return SubframeRead(bits=flipped, crc_ok=True, inverted=True)
    return None


# Subframe 2 layout: (start bit, width, signed, scale), exactly as IS-GPS-800 Figure
# 3.5-1 and Table 3.5-1 state them, with 1-based bit numbers as `prim.unpack_field`
# reads them.  Bits 1-49 and 471-574 are the layout of the project note
# `docs/icd/L1C_E5A_TRACKING_NOTES.md`.  Bits 50-470 (the orbit) are the CNAV
# element widths and scales, in the order of Table 3.5-1; the widths add up to the
# note's anchors (toe ends at bit 49, a_f0 begins at bit 471), which is the only
# check made so far.  Compare with the ICD table before relying on the orbit.
SUBFRAME_2_FIELDS: dict[str, tuple[int, int, bool, float]] = {
    "wn": (1, 13, False, 1.0),
    "itow": (14, 8, False, 1.0),
    "top": (22, 11, False, 300.0),
    "health_l1c": (33, 1, False, 1.0),
    "ura_ed_index": (34, 5, True, 1.0),
    "toe": (39, 11, False, 300.0),
    "delta_a": (50, 26, True, 2.0**-9),
    "a_dot": (76, 25, True, 2.0**-21),
    "delta_n0": (101, 17, True, 2.0**-44),
    "delta_n0_dot": (118, 23, True, 2.0**-57),
    "M0": (141, 33, True, 2.0**-32),
    "e": (174, 33, False, 2.0**-34),
    "omega": (207, 33, True, 2.0**-32),
    "Omega0": (240, 33, True, 2.0**-32),
    "i0": (273, 33, True, 2.0**-32),
    "delta_Omega_dot": (306, 17, True, 2.0**-44),
    "i_dot": (323, 15, True, 2.0**-44),
    "Cis": (338, 16, True, 2.0**-30),
    "Cic": (354, 16, True, 2.0**-30),
    "Crs": (370, 24, True, 2.0**-8),
    "Crc": (394, 24, True, 2.0**-8),
    "Cus": (418, 21, True, 2.0**-30),
    "Cuc": (439, 21, True, 2.0**-30),
    "ura_ned0_index": (460, 5, True, 1.0),
    "ura_ned1_index": (465, 3, False, 1.0),
    "ura_ned2_index": (468, 3, False, 1.0),
    "af0": (471, 26, True, 2.0**-35),
    "af1": (497, 20, True, 2.0**-48),
    "af2": (517, 10, True, 2.0**-60),
    "tgd": (527, 13, True, 2.0**-35),
    "isc_l1cp": (540, 13, True, 2.0**-35),
    "isc_l1cd": (553, 13, True, 2.0**-35),
    "integrity_status_flag": (566, 1, False, 1.0),
    "wn_op": (567, 8, False, 1.0),
}


def parse_subframe_2(bits: np.ndarray) -> dict[str, float]:
    """Every named field of a 600-bit subframe 2, scaled.  The CRC is not checked here."""
    if len(bits) != SUBFRAME_2_BITS:
        raise ValueError(f"subframe 2 is {SUBFRAME_2_BITS} bits, got {len(bits)}")
    return {
        name: prim.unpack_field(bits, start, width, signed=signed, scale=scale)
        for name, (start, width, signed, scale) in SUBFRAME_2_FIELDS.items()
    }


def build_subframe_2(values: dict[str, float]) -> np.ndarray:
    """
    The 600 information bits for `values`, CRC included: the encoder side, for tests.

    Reads the same `SUBFRAME_2_FIELDS` table as the parser, so a test cannot pass
    by agreeing with a second, separate copy of the layout.  Fields not given are
    zero.  A value is divided by the scale and rounded to the nearest integer.
    """
    bits = np.zeros(SUBFRAME_2_BITS - SUBFRAME_2_PARITY_FIELD, dtype=np.uint8)
    for name, value in values.items():
        start, width, signed, scale = SUBFRAME_2_FIELDS[name]
        raw = int(round(value / scale))
        low, high = (-(1 << (width - 1)), (1 << (width - 1)) - 1) if signed else (0, (1 << width) - 1)
        if not low <= raw <= high:
            raise ValueError(f"{name}={value} does not fit in {width} bits")
        bits[start - 1 : start - 1 + width] = prim.bits_from_int(raw & ((1 << width) - 1), width)
    return prim.crc24q_append(bits)


@dataclass(frozen=True)
class Subframe2:
    """Subframe 2 of one frame, CRC-checked: time of week epoch, clock and ephemeris."""

    fields: dict[str, float]
    bits: np.ndarray
    inverted: bool

    def __getitem__(self, name: str) -> float:
        return self.fields[name]


def decode_subframe_2(frame: Cnav2Frame) -> Subframe2 | None:
    """
    Subframe 2 of `frame`, or None when its CRC does not pass.

    None, never a best guess: an ephemeris with one wrong bit puts the satellite
    kilometres away without any sign of it.  See `read_subframe`.
    """
    read = read_subframe(frame.subframe_2_symbols, SUBFRAME_2_BITS)
    if read is None:
        return None
    return Subframe2(fields=parse_subframe_2(read.bits), bits=read.bits, inverted=read.inverted)


@dataclass(frozen=True)
class Subframe3:
    """Subframe 3 of one frame, CRC-checked.  The page content is not parsed yet."""

    bits: np.ndarray
    inverted: bool


def decode_subframe_3(frame: Cnav2Frame) -> Subframe3 | None:
    """
    Subframe 3 of `frame`, or None when its CRC does not pass.

    The bits are returned as they are: the project note names the page number and
    says the page order is not fixed, but gives no bit positions.
    """
    read = read_subframe(frame.subframe_3_symbols, SUBFRAME_3_BITS)
    if read is None:
        return None
    return Subframe3(bits=read.bits, inverted=read.inverted)


def frame_start_tow_s(previous: Cnav2Frame, subframe_2_of_this: Subframe2) -> float:
    """
    Time of week at the first symbol of a frame.

    The TOI of the PREVIOUS frame counts 18 s epochs to the start of this one, and
    this frame's own subframe 2 gives the two-hour count (ITOW).  Using the same
    frame for both is an 18 s error.  This is the reading in the project note
    (section 1.6); check it on real data.
    """
    return previous.tow_at_next_frame_start_s(int(subframe_2_of_this["itow"]))


def assemble_ephemeris(subframe_2: Subframe2, sat_id: str):
    """
    A `CnavEphemeris` from one CRC-checked subframe 2.

    Subframe 2 holds the orbit and the clock together, so one frame is enough (CNAV
    needs three messages).  `toc` is set to `toe`: subframe 2 has no separate clock
    reference time.  That is this code's reading of the ICD, not yet checked in it.
    """
    from .ephemeris import CnavEphemeris

    f = subframe_2.fields
    return CnavEphemeris(
        sat_id=sat_id,
        week=int(f["wn"]),
        toe=f["toe"],
        top=f["top"],
        delta_a=f["delta_a"],
        a_dot=f["a_dot"],
        delta_n0=f["delta_n0"],
        delta_n0_dot=f["delta_n0_dot"],
        M0=f["M0"],
        e=f["e"],
        omega=f["omega"],
        Omega0=f["Omega0"],
        delta_Omega_dot=f["delta_Omega_dot"],
        i0=f["i0"],
        i_dot=f["i_dot"],
        Cis=f["Cis"],
        Cic=f["Cic"],
        Crs=f["Crs"],
        Crc=f["Crc"],
        Cus=f["Cus"],
        Cuc=f["Cuc"],
        toc=f["toe"],
        af0=f["af0"],
        af1=f["af1"],
        af2=f["af2"],
        tgd=f["tgd"],
        isc_l1cp=f["isc_l1cp"],
        isc_l1cd=f["isc_l1cd"],
        health_l1=int(f["health_l1c"]),
    )
