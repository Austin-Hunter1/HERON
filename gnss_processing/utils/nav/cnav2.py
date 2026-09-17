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

**What this module does today is time.**  Subframe 1 is decoded fully -- and
subframe 1 is where the clock lives, so this is enough for a navigation solution
to use L1C.  Subframes 2 and 3 are de-interleaved and handed back as symbol
blocks, but not LDPC-decoded; `decode_subframe_2` raises rather than returning a
half-trustworthy ephemeris.  Adding the two belief-propagation decoders from
IS-GPS-800J's prototype matrices is the natural next step and nothing else has to
change when it lands.

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


def decode_subframe_2(frame: Cnav2Frame):
    """
    Not implemented: subframe 2 needs the LDPC(1200,600) decoder.

    Deliberately a hard failure rather than a best-effort hard-decision read.  The
    600 information bits carry the ephemeris, and an ephemeris that is wrong in one
    bit is not a slightly worse ephemeris -- it puts the satellite kilometres from
    where it is, silently.  Until the belief-propagation decoder and IS-GPS-800J's
    parity-check matrices are in place, `frame.subframe_2_symbols` holds the
    de-interleaved soft symbols and the caller can take them elsewhere.
    """
    raise NotImplementedError(
        "CNAV-2 subframe 2 carries the ephemeris behind an LDPC(1200,600) code, "
        "which is not implemented yet. Subframe 1 (time) decodes fully -- see "
        "Cnav2Frame.toi. The de-interleaved subframe 2 symbols are available as "
        "frame.subframe_2_symbols."
    )


def decode_subframe_3(frame: Cnav2Frame):
    """Not implemented: subframe 3 needs the LDPC(548,274) decoder.  See
    `decode_subframe_2`."""
    raise NotImplementedError(
        "CNAV-2 subframe 3 is protected by an LDPC(548,274) code, which is not "
        "implemented yet. The de-interleaved symbols are available as "
        "frame.subframe_3_symbols."
    )
