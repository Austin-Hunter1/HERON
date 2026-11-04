"""
Galileo F/NAV -- the navigation message on E5a-I.

Everything here follows the same method as the B2a decoder (`bcnav2.py`): find the
frame in a stream of soft symbols, correct the polarity, undo the channel code, check
the CRC, read the fields, and join the pages into one ephemeris.

    page      500 symbols at 50 sps = 10 s
              12 sync symbols (not coded) + 488 coded symbols
    coded     244 bits -> rate 1/2, K = 7 convolutional code (G2 inverted) -> 488 symbols,
              then a 61 x 8 block interleaver
    244 bits  6 page type + 208 data + 24 CRC-24Q + 6 tail (zeros)
    ephemeris pages 1, 2, 3 and 4 with the same IOD_nav (page 1 holds the clock and the
              time of week, pages 2 to 4 the orbit)

Sources: the project note `docs/icd/L1C_E5A_TRACKING_NOTES.md` (Galileo OS SIS ICD,
section 4.2 and 5.1), and the CU Multi-GNSS SDR Galileo E5a MATLAB receiver the user
supplied (`include/NAVdecoding.m` and `include/ephemeris.m`), whose bit positions agree
with this table for pages 1 to 4.  The coding steps are checked against the ICD
Annex D.1 example in `docs/icd/e5a_code_tables.json`.

Time: the time of week in a page is the transmit time of the page's first symbol (the
first sync symbol).  Galileo System Time runs with GPS time, and GST week 0 starts at
GPS week 1024, so the GPS week is ``WN + 1024``.  The few nanoseconds between the two
scales (GGTO, page 4) are not applied.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import primitives as prim

SYNC_PATTERN = "101101110000"
SYNC_SYMBOLS = len(SYNC_PATTERN)  # 12
CODED_SYMBOLS = 488
PAGE_SYMBOLS = SYNC_SYMBOLS + CODED_SYMBOLS  # 500
PAGE_DURATION_S = 10.0
SYMBOL_PERIOD_MS = 20  # 50 symbols per second

INFORMATION_BITS = 244  # before coding, tail included
CRC_COVERED_BITS = 214  # page type + data
PAGE_BITS = 238  # what the CRC check covers: 214 + 24 CRC
TAIL_BITS = INFORMATION_BITS - PAGE_BITS  # 6

INTERLEAVER_COLUMNS = 61
INTERLEAVER_ROWS = 8

GST_WEEK_TO_GPS_WEEK = 1024
"""GST week 0 starts at the start of GPS week 1024 (both 1999-08-21 23:59:47 UTC)."""

# Page types that carry the ephemeris and the clock.
EPHEMERIS_PAGES = (1, 2, 3, 4)

# The sync pattern as +/-1 (a 0 bit is +1, as everywhere in this package).
_SYNC_PM1 = np.array([1.0 - 2.0 * int(c) for c in SYNC_PATTERN])

# Pages repeat every 10 s; a sync hit must score at least this share of the largest
# score the pattern can give (the sum of the absolute symbols) before the decoder
# spends a Viterbi run on it.  The CRC is what decides; this only keeps the work small.
DEFAULT_SYNC_SCORE = 0.6


# ---------------------------------------------------------------------------
# Channel coding
# ---------------------------------------------------------------------------


def interleave(symbols: np.ndarray) -> np.ndarray:
    """Write 61 columns of 8 symbols, read row by row (ICD Table 27)."""
    symbols = np.asarray(symbols)
    if len(symbols) != CODED_SYMBOLS:
        raise ValueError(f"the interleaver takes {CODED_SYMBOLS} symbols, got {len(symbols)}")
    return symbols.reshape(INTERLEAVER_COLUMNS, INTERLEAVER_ROWS).T.reshape(-1).copy()


def deinterleave(symbols: np.ndarray) -> np.ndarray:
    """Inverse of `interleave`."""
    symbols = np.asarray(symbols)
    if len(symbols) != CODED_SYMBOLS:
        raise ValueError(f"the deinterleaver takes {CODED_SYMBOLS} symbols, got {len(symbols)}")
    return symbols.reshape(INTERLEAVER_ROWS, INTERLEAVER_COLUMNS).T.reshape(-1).copy()


def encode_page(information_bits: np.ndarray) -> np.ndarray:
    """
    The 500 transmitted symbols (0/1) for 244 information bits: the encoder side, for tests.

    Order: convolutional code (G1 first, G2 inverted), interleaver, and the sync
    pattern in front.  The register starts at zero for every page.
    """
    bits = np.asarray(information_bits, dtype=np.uint8)
    if len(bits) != INFORMATION_BITS:
        raise ValueError(f"a page is {INFORMATION_BITS} bits before coding, got {len(bits)}")
    coded = prim.convolutional_encode(bits, initial_state=0)
    coded[1::2] ^= 1  # F/NAV inverts the G2 output (ICD Table 26)
    sync = np.array([int(c) for c in SYNC_PATTERN], dtype=np.uint8)
    return np.concatenate([sync, interleave(coded)])


def soft_symbols(symbols01: np.ndarray) -> np.ndarray:
    """0/1 symbols to +/-1 soft values (0 is +1)."""
    return 1.0 - 2.0 * np.asarray(symbols01, dtype=float)


def decode_coded_symbols(coded_soft: np.ndarray) -> np.ndarray:
    """
    The 244 information bits from the 488 received coded symbols (soft, after the sync).

    Undoes the interleaver, un-inverts G2 (a sign flip of every second soft value), and
    runs the Viterbi decoder from the known zero state of a page start.  The CRC, not
    this function, says whether the result is right.
    """
    deinterleaved = deinterleave(np.asarray(coded_soft, dtype=float))
    deinterleaved[1::2] *= -1.0
    return prim.ViterbiDecoder().decode(deinterleaved, initial_state=0).bits


# ---------------------------------------------------------------------------
# Page layouts -- (start bit, width, signed, scale), 1-based within the 214 bits
# of page type + data, as `prim.unpack_field` reads them.
# ---------------------------------------------------------------------------

PAGE_TYPE_FIELD = (1, 6, False, 1.0)

FIELDS: dict[int, dict[str, tuple[int, int, bool, float]]] = {
    1: {
        "svid": (7, 6, False, 1.0),
        "iod_nav": (13, 10, False, 1.0),
        "t0c": (23, 14, False, 60.0),
        "af0": (37, 31, True, 2.0**-34),
        "af1": (68, 21, True, 2.0**-46),
        "af2": (89, 6, True, 2.0**-59),
        "sisa": (95, 8, False, 1.0),
        "ai0": (103, 11, False, 2.0**-2),
        "ai1": (114, 11, True, 2.0**-8),
        "ai2": (125, 14, True, 2.0**-15),
        "iono_flags": (139, 5, False, 1.0),
        "bgd_e1e5a": (144, 10, True, 2.0**-32),
        "e5a_hs": (154, 2, False, 1.0),
        "wn": (156, 12, False, 1.0),
        "tow": (168, 20, False, 1.0),
        "e5a_dvs": (188, 1, False, 1.0),
    },
    2: {
        "iod_nav": (7, 10, False, 1.0),
        "M0": (17, 32, True, 2.0**-31),
        "Omega_dot": (49, 24, True, 2.0**-43),
        "e": (73, 32, False, 2.0**-33),
        "sqrt_a": (105, 32, False, 2.0**-19),
        "Omega0": (137, 32, True, 2.0**-31),
        "i_dot": (169, 14, True, 2.0**-43),
    },
    3: {
        "iod_nav": (7, 10, False, 1.0),
        "i0": (17, 32, True, 2.0**-31),
        "omega": (49, 32, True, 2.0**-31),
        "deln": (81, 16, True, 2.0**-43),
        "Cuc": (97, 16, True, 2.0**-29),
        "Cus": (113, 16, True, 2.0**-29),
        "Crc": (129, 16, True, 2.0**-5),
        "Crs": (145, 16, True, 2.0**-5),
        "t0e": (161, 14, False, 60.0),
    },
    4: {
        "iod_nav": (7, 10, False, 1.0),
        "Cic": (17, 16, True, 2.0**-29),
        "Cis": (33, 16, True, 2.0**-29),
        "a0_utc": (49, 32, True, 2.0**-30),
        "a1_utc": (81, 24, True, 2.0**-50),
        "dt_ls": (105, 8, True, 1.0),
        "t0t": (113, 8, False, 3600.0),
        "wn0t": (121, 8, False, 1.0),
        "wn_lsf": (129, 8, False, 1.0),
        "dn": (137, 3, False, 1.0),
        "dt_lsf": (140, 8, True, 1.0),
        "t0g": (148, 8, False, 3600.0),
        "a0g": (156, 16, True, 2.0**-35),
        "a1g": (172, 12, True, 2.0**-51),
        "wn0g": (184, 6, False, 1.0),
    },
}


def parse_page(bits: np.ndarray) -> dict[str, float]:
    """The named fields of one page (types 1 to 4), scaled.  The CRC is not checked here."""
    page_type = int(prim.unpack_field(bits, *PAGE_TYPE_FIELD[:2]))
    if page_type not in FIELDS:
        return {}
    return {
        name: prim.unpack_field(bits, start, width, signed=signed, scale=scale)
        for name, (start, width, signed, scale) in FIELDS[page_type].items()
    }


def build_page(page_type: int, values: dict[str, float] | None = None) -> np.ndarray:
    """
    The 244 information bits of a page of `page_type` with `values`, CRC and tail included.

    The encoder side, for tests.  It reads the same `FIELDS` table as the parser, so a
    test cannot pass by agreeing with a second copy of the layout.  A value is divided
    by its scale and rounded; fields not given are zero.
    """
    bits = np.zeros(CRC_COVERED_BITS, dtype=np.uint8)
    bits[:6] = prim.bits_from_int(page_type, 6)
    for name, value in (values or {}).items():
        start, width, signed, scale = FIELDS[page_type][name]
        raw = int(round(value / scale))
        low, high = (-(1 << (width - 1)), (1 << (width - 1)) - 1) if signed else (0, (1 << width) - 1)
        if not low <= raw <= high:
            raise ValueError(f"{name}={value} does not fit in {width} bits")
        bits[start - 1 : start - 1 + width] = prim.bits_from_int(raw & ((1 << width) - 1), width)
    return np.concatenate([prim.crc24q_append(bits), np.zeros(TAIL_BITS, dtype=np.uint8)])


# ---------------------------------------------------------------------------
# Finding and decoding pages in a stream
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FnavPage:
    """One CRC-checked page and where it started."""

    page_type: int
    bits: np.ndarray
    """The 238 bits the CRC covers (type, data, CRC)."""

    symbol_index: int
    """Index in the stream of the page's first (sync) symbol."""

    inverted: bool
    fields: dict[str, float]


@dataclass
class FnavDecodeResult:
    """The pages found in one symbol stream."""

    pages: list[FnavPage]
    inverted: bool

    @property
    def synced(self) -> bool:
        return bool(self.pages)

    @property
    def frames(self) -> list[FnavPage]:
        """The pages, under the name notebook 02 uses for the units of every format."""
        return self.pages

    @property
    def page_types(self) -> list[int]:
        return sorted({p.page_type for p in self.pages})

    def of_type(self, page_type: int) -> list[FnavPage]:
        return [p for p in self.pages if p.page_type == page_type]

    def sow_is_consistent(self) -> bool:
        """
        Do the times of week of the page-1 pages agree with the symbol spacing?

        Two pages of type 1 that are `n` symbols apart must differ by `n * 20 ms` in
        time of week.  A page of the wrong place, or symbols lost in tracking, breaks it.
        """
        ones = self.of_type(1)
        for earlier, later in zip(ones, ones[1:]):
            expected = (later.symbol_index - earlier.symbol_index) * SYMBOL_PERIOD_MS * 1e-3
            if abs((later.fields["tow"] - earlier.fields["tow"]) - expected) > 1e-6:
                return False
        return True


def _decode_page_at(soft: np.ndarray, start: int, sign: float) -> FnavPage | None:
    window = sign * soft[start : start + PAGE_SYMBOLS]
    bits = decode_coded_symbols(window[SYNC_SYMBOLS:])
    if not prim.crc24q_check(bits[:PAGE_BITS]):
        return None
    page_bits = bits[:PAGE_BITS]
    page_type = int(prim.unpack_field(page_bits, 1, 6))
    return FnavPage(
        page_type=page_type,
        bits=page_bits,
        symbol_index=start,
        inverted=sign < 0,
        fields=parse_page(page_bits),
    )


def decode(
    soft: np.ndarray,
    *,
    min_sync_score: float = DEFAULT_SYNC_SCORE,
) -> FnavDecodeResult:
    """
    Every CRC-valid page in a stream of 50 sps soft symbols.

    Candidates are the offsets where the 12-symbol sync pattern correlates strongly in
    either polarity.  Each is decoded with the polarity its sign says and kept only if
    the CRC passes.  Pages are not allowed to overlap.  Where no page passes, the result
    is empty: nothing is guessed.
    """
    soft = np.asarray(soft, dtype=float)
    if len(soft) < PAGE_SYMBOLS:
        return FnavDecodeResult([], False)
    last_start = len(soft) - PAGE_SYMBOLS
    window = np.lib.stride_tricks.sliding_window_view(soft, SYNC_SYMBOLS)[: last_start + 1]
    correlation = window @ _SYNC_PM1
    scale = np.abs(window).sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        score = np.where(scale > 0, correlation / scale, 0.0)
    pages: list[FnavPage] = []
    next_free = 0
    for start in np.flatnonzero(np.abs(score) >= min_sync_score):
        if start < next_free:
            continue
        page = _decode_page_at(soft, int(start), 1.0 if score[start] > 0 else -1.0)
        if page is not None:
            pages.append(page)
            next_free = int(start) + PAGE_SYMBOLS
    inverted = sum(p.inverted for p in pages) > len(pages) / 2
    return FnavDecodeResult(pages, inverted)


# ---------------------------------------------------------------------------
# One ephemeris from pages 1 to 4
# ---------------------------------------------------------------------------


def complete_sets(result: FnavDecodeResult) -> list[dict[int, FnavPage]]:
    """
    Every set of pages 1, 2, 3 and 4 that share one IOD_nav, oldest first.

    A set mixes pages from different uploads if the IODs differ, and an orbit built
    from such a mix is wrong without any sign of it (the same rule as B2a, D-055).
    """
    by_iod: dict[int, dict[int, FnavPage]] = {}
    for page in result.pages:
        if page.page_type in EPHEMERIS_PAGES:
            iod = int(page.fields["iod_nav"])
            by_iod.setdefault(iod, {})[page.page_type] = page
    return [s for s in by_iod.values() if all(t in s for t in EPHEMERIS_PAGES)]


def assemble_ephemeris(result: FnavDecodeResult, sat_id: str):
    """
    A `GalEphemeris` from the latest complete set of pages, or None.

    None when no set of pages 1 to 4 with one IOD_nav exists.
    """
    from .ephemeris import GalEphemeris

    sets = complete_sets(result)
    if not sets:
        return None
    chosen = max(sets, key=lambda s: s[1].symbol_index)
    f1, f2, f3, f4 = (chosen[t].fields for t in EPHEMERIS_PAGES)
    return GalEphemeris(
        sat_id=sat_id,
        week=int(f1["wn"]) + GST_WEEK_TO_GPS_WEEK,
        toe=f3["t0e"],
        sqrt_a=f2["sqrt_a"],
        e=f2["e"],
        i0=f3["i0"],
        i_dot=f2["i_dot"],
        Omega0=f2["Omega0"],
        Omega_dot=f2["Omega_dot"],
        omega=f3["omega"],
        M0=f2["M0"],
        deln=f3["deln"],
        Cuc=f3["Cuc"],
        Cus=f3["Cus"],
        Crc=f3["Crc"],
        Crs=f3["Crs"],
        Cic=f4["Cic"],
        Cis=f4["Cis"],
        toc=f1["t0c"],
        af0=f1["af0"],
        af1=f1["af1"],
        af2=f1["af2"],
        iode=int(f1["iod_nav"]),
        health=int(f1["e5a_hs"]),
        bgd_e1e5a=f1["bgd_e1e5a"],
        data_valid=int(f1["e5a_dvs"]) == 0,
    )
