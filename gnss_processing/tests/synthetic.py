"""
Synthetic GNSS signal generators for regression and unit tests.

The repository's sample collects live in `local-data/`, which is gitignored and not
guaranteed to be present.  These generators produce deterministic, noise-controlled
signals so the correlator and tracking loops can be exercised without any data
dependency, and so a golden baseline can be diffed across refactors.

Conventions match the tracking code:
  - Code phase is expressed in *ms of code*; the correlator converts via
    `code_phase_ms * 1e-3 * nominal_code_rate_chips_per_sec`.
  - Code rate is slaved to carrier Doppler by `1 + doppler / carrier_freq`.
  - Samples are complex64 baseband (carrier already mixed to near-DC).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

import gnss_tools.signals.gps_l1c as gps_l1c
import gnss_tools.signals.gps_l1ca as gps_l1ca
import gnss_tools.signals.gps_l2c as gps_l2c
import gnss_tools.signals.gps_l5 as gps_l5


@dataclass(frozen=True)
class SyntheticTruth:
    """Ground-truth parameters used to generate a signal, for assertions."""

    doppler_hz: float
    code_phase_ms: float
    samp_rate: float
    carrier_freq_hz: float
    nominal_code_rate_chips_per_sec: float


def get_l1ca_code(prn: int) -> np.ndarray:
    """+/-1 int8 L1 C/A primary code."""
    return (1 - 2 * gps_l1ca.get_GPS_L1CA_code_sequence(prn)).astype(np.int8)


def get_l2c_codes(prn: int) -> tuple[np.ndarray, np.ndarray]:
    """+/-1 int8 (L2CM, L2CL) primary codes."""
    cm = (1 - 2 * gps_l2c.get_GPS_L2CM_code_sequence(prn)).astype(np.int8)
    cl = (1 - 2 * gps_l2c.get_GPS_L2CL_code_sequence(prn)).astype(np.int8)
    return cm, cl


def get_l5_codes(prn: int) -> tuple[np.ndarray, np.ndarray]:
    """+/-1 int8 (L5I, L5Q) primary codes."""
    code_i = (1 - 2 * gps_l5.get_GPS_L5I_code_sequence(prn)).astype(np.int8)
    code_q = (1 - 2 * gps_l5.get_GPS_L5Q_code_sequence(prn)).astype(np.int8)
    return code_i, code_q


def get_l5_overlays() -> tuple[np.ndarray, np.ndarray]:
    """+/-1 int8 Neuman-Hofman overlays (NH10 on I, NH20 on Q)."""
    nh_i = (1 - 2 * gps_l5.NEUMAN_HOFFMAN_SEQ_L5I).astype(np.int8)
    nh_q = (1 - 2 * gps_l5.NEUMAN_HOFFMAN_SEQ_L5Q).astype(np.int8)
    return nh_i, nh_q


def get_l1c_codes(prn: int) -> tuple[np.ndarray, np.ndarray]:
    """+/-1 int8 (L1CD, L1CP) ranging codes, 10230 chips each."""
    code_d = (1 - 2 * gps_l1c.get_GPS_L1CD_code_sequence(prn)).astype(np.int8)
    code_p = (1 - 2 * gps_l1c.get_GPS_L1CP_code_sequence(prn)).astype(np.int8)
    return code_d, code_p


def get_l1c_overlay(prn: int) -> np.ndarray:
    """+/-1 int8 L1CO overlay, 1800 bits, carried on L1CP."""
    return (1 - 2 * gps_l1c.get_GPS_L1CO_overlay_sequence(prn)).astype(np.int8)


def _nav_bits(
    code_period_index: np.ndarray,
    periods_per_symbol: int,
    symbols: np.ndarray | None = None,
    phase_periods: int = 0,
) -> np.ndarray:
    """
    Data modulation, keyed to the code period rather than to absolute time.

    The data symbol is synchronous with the primary code in every signal here
    (IS-GPS-200 for L1 C/A and L2 CNAV, IS-GPS-705 for L5), so a flip falls exactly
    on a code period boundary and can never land inside a correlation interval.
    Keying it to `t` instead put the flip `code_phase_ms` into each period -- a
    fixture artefact that made a symbol-length coherent accumulation look worse
    than it is.

    With `symbols` omitted the pattern is a deterministic alternation, which
    exercises bit flips without meaning anything.  Passing a real encoded message
    as +/-1 values instead is what lets a test drive an actual navigation-message
    decoder end to end; the sequence repeats if the capture outlasts it.

    `phase_periods` slides the symbol grid along the code periods.  It exists for
    one signal and one question: on L1 C/A the 20 ms bit boundary need not fall on
    a multiple of 20 ms of code phase, because acquisition pins the code phase only
    modulo one 1 ms period.  Leaving it at the default makes the two coincide,
    which is the one alignment a receiver must NOT be allowed to assume.
    """
    symbol_index = (code_period_index - phase_periods) // periods_per_symbol
    if symbols is None:
        return 1 - 2 * (symbol_index % 2 == 1)
    symbols = np.asarray(symbols)
    return symbols[symbol_index % len(symbols)]


def _symbol_sequence(nav_bits) -> np.ndarray | None:
    """Interpret the `nav_bits` argument the generators share.

    `True` keeps the historical alternating pattern, `False` means no modulation
    at all, and an array is used as the symbol sequence itself.
    """
    if nav_bits is True or nav_bits is False or nav_bits is None:
        return None
    return np.asarray(nav_bits, dtype=float)


def generate_l1ca_samples(
    *,
    prn: int,
    start_sec: float,
    duration_sec: float,
    samp_rate: float,
    doppler_hz: float,
    code_phase_ms: float,
    noise_sigma: float = 0.0,
    nav_bits: bool | np.ndarray = True,
    bit_phase_periods: int = 0,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """
    Baseband GPS L1 C/A: a single BPSK code with optional 50 bps data.

    `bit_phase_periods` puts the data bit boundary that many code periods away
    from a multiple of 20 ms of code phase -- see `_nav_bits`.
    """
    code = get_l1ca_code(prn)
    n = int(round(samp_rate * duration_sec))
    t = start_sec + np.arange(n) / samp_rate

    code_rate = gps_l1ca.CODE_RATE * (1.0 + doppler_hz / gps_l1ca.CARRIER_FREQ)
    chips = code_phase_ms * 1e-3 * gps_l1ca.CODE_RATE + t * code_rate
    chip_index = chips.astype(np.int64)
    # 20 ms nav bit = 20 code periods of 1 ms.
    data = (
        _nav_bits(
            chip_index // gps_l1ca.CODE_LENGTH,
            20,
            _symbol_sequence(nav_bits),
            phase_periods=bit_phase_periods,
        )
        if nav_bits is not False
        else 1.0
    )

    samples = code[chip_index % len(code)] * data
    samples = (samples * np.exp(2j * np.pi * doppler_hz * t)).astype(np.complex64)
    return _add_noise(samples, noise_sigma, rng)


def generate_l2c_samples(
    *,
    prn: int,
    start_sec: float,
    duration_sec: float,
    samp_rate: float,
    doppler_hz: float,
    code_phase_ms: float,
    noise_sigma: float = 0.0,
    nav_bits: bool | np.ndarray = True,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """
    Baseband GPS L2C: CM and CL chip-interleaved on a 1.023 Mcps combined stream.

    Even combined chips carry CM (with CNAV data), odd ones carry the
    dataless CL pilot.  This is the topology the tracking correlator must invert.
    """
    cm, cl = get_l2c_codes(prn)
    n = int(round(samp_rate * duration_sec))
    t = start_sec + np.arange(n) / samp_rate

    combined_rate = gps_l2c.CODE_RATE_L2CLM * (1.0 + doppler_hz / gps_l2c.CARRIER_FREQ)
    k = (code_phase_ms * 1e-3 * gps_l2c.CODE_RATE_L2CLM + t * combined_rate).astype(np.int64)
    # One CNAV symbol is exactly one CM period: 10230 CM chips = 20460 combined.
    data = (
        _nav_bits(k // (2 * gps_l2c.CODE_LENGTH_L2CM), 1, _symbol_sequence(nav_bits))
        if nav_bits is not False
        else 1.0
    )

    chips = np.where(k % 2 == 0, cm[(k // 2) % len(cm)] * data, cl[(k // 2) % len(cl)])
    samples = (chips * np.exp(2j * np.pi * doppler_hz * t)).astype(np.complex64)
    return _add_noise(samples, noise_sigma, rng)


def generate_l5_samples(
    *,
    prn: int,
    start_sec: float,
    duration_sec: float,
    samp_rate: float,
    doppler_hz: float,
    code_phase_ms: float,
    noise_sigma: float = 0.0,
    nav_bits: bool | np.ndarray = True,
    overlay: bool = True,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """
    Baseband GPS L5: I and Q in carrier quadrature, both at 10.23 Mcps.

    L5I carries CNAV symbols at 100 sps (10 ms each); L5Q is a dataless pilot.
    Both carry a Neuman-Hofman overlay that flips sign every primary code period
    (1 ms) -- NH10 on I, NH20 on Q -- which is why Q is not yet effectively
    dataless at 1 ms coherent integration.

    Note the chip rate demands a high sample rate: at least ~20 Msps to keep two
    samples per chip.
    """
    code_i, code_q = get_l5_codes(prn)
    nh_i, nh_q = get_l5_overlays()

    n = int(round(samp_rate * duration_sec))
    t = start_sec + np.arange(n) / samp_rate

    code_rate = gps_l5.CODE_RATE * (1.0 + doppler_hz / gps_l5.CARRIER_FREQ)
    chips = code_phase_ms * 1e-3 * gps_l5.CODE_RATE + t * code_rate
    chip_index = chips.astype(np.int64)

    # Overlay chips advance once per primary code period.
    period_index = chip_index // gps_l5.PRIMARY_CODE_LENGTH
    overlay_i = nh_i[period_index % len(nh_i)] if overlay else 1
    overlay_q = nh_q[period_index % len(nh_q)] if overlay else 1

    # One CNAV symbol is 10 ms = 10 primary code periods, which is also NH10's
    # period -- that alignment is what makes NH10 sync deliver symbol sync.
    data = (
        _nav_bits(period_index, 10, _symbol_sequence(nav_bits))
        if nav_bits is not False
        else 1
    )

    in_phase = code_i[chip_index % len(code_i)] * overlay_i * data
    quadrature = code_q[chip_index % len(code_q)] * overlay_q

    samples = (in_phase + 1j * quadrature) * np.exp(2j * np.pi * doppler_hz * t)
    return _add_noise(samples.astype(np.complex64), noise_sigma, rng)


def _add_noise(
    samples: np.ndarray, sigma: float, rng: np.random.Generator | None
) -> np.ndarray:
    if sigma <= 0.0:
        return samples
    if rng is None:
        raise ValueError("rng is required when noise_sigma > 0 (tests must be deterministic)")
    n = len(samples)
    noise = rng.normal(0.0, sigma, n) + 1j * rng.normal(0.0, sigma, n)
    return (samples + noise).astype(np.complex64)


def generate_l1c_samples(
    prn: int,
    start_sec: float,
    duration_sec: float,
    samp_rate: float,
    doppler_hz: float,
    code_phase_ms: float,
    noise_sigma: float = 0.0,
    nav_bits: bool | np.ndarray = True,
    overlay: bool = True,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """
    Baseband GPS L1C: L1CD and L1CP summed IN PHASE, both at 1.023 Mcps.

    Two things make this unlike the other generators here.

    First, the components are added rather than placed in quadrature.  IS-GPS-800J
    3.2.1.6.1 puts both L1C carriers in the same phase (and in phase with P(Y)), so
    they are separated by their codes and by a 25/75 power split -- amplitudes
    sqrt(0.25) and sqrt(0.75) -- and by nothing else.  Writing L1CP on the
    imaginary axis, as L5's pilot legitimately is, would make the pilot trivially
    separable and quietly flatter every discriminator downstream.

    Second, each chip carries a subcarrier.  It is built here from `sign(sin(...))`
    directly rather than from `code_components.subcarrier_signs`, so the fixture
    and the correlator are independent statements of the same convention: L1CD is
    BOC(1,1) throughout, and L1CP is BOC(6,1) on the 4 chips of every 33 named by
    IS-GPS-800J 3.3 and BOC(1,1) on the other 29.

    Note the sample rate this demands.  BOC(6,1) puts twelve sub-chips in a chip,
    so 12.276 Mcps of sub-chip rate needs ~25 Msps to stay above two samples per
    sub-chip; below ~14 Msps the BOC(6,1) lobes are gone entirely.
    """
    code_d, code_p = get_l1c_codes(prn)
    overlay_p = get_l1c_overlay(prn)

    n = int(round(samp_rate * duration_sec))
    t = start_sec + np.arange(n) / samp_rate

    code_rate = gps_l1c.CODE_RATE * (1.0 + doppler_hz / gps_l1c.CARRIER_FREQ)
    chips = code_phase_ms * 1e-3 * gps_l1c.CODE_RATE + t * code_rate
    chip_index = np.floor(chips).astype(np.int64)
    within_chip = chips - chip_index

    period_index = chip_index // gps_l1c.CODE_LENGTH
    chip_of_period = chip_index % gps_l1c.CODE_LENGTH

    def subcarrier(sub_chips_per_chip):
        """sign(sin(2*pi*f_s*t)) over the chip, stated as its half-cycle index."""
        return np.where(
            (within_chip * sub_chips_per_chip).astype(np.int64) % 2 == 1, -1.0, 1.0
        )

    # L1CD is BOC(1,1) everywhere; L1CP swaps in BOC(6,1) on the TMBOC chips.
    data_subcarrier = subcarrier(2)
    tmboc = np.isin(
        chip_of_period % gps_l1c.TMBOC_PATTERN_LENGTH,
        np.array(gps_l1c.TMBOC_PATTERN_INDICES),
    )
    pilot_subcarrier = np.where(tmboc, subcarrier(12), data_subcarrier)

    # One CNAV-2 symbol is exactly one 10 ms code period, and one L1CO bit is too.
    data = (
        _nav_bits(period_index, 1, _symbol_sequence(nav_bits))
        if nav_bits is not False
        else 1.0
    )
    overlay_sign = overlay_p[period_index % len(overlay_p)] if overlay else 1.0

    amplitude_d = np.sqrt(gps_l1c.L1CD_POWER_FRACTION)
    amplitude_p = np.sqrt(gps_l1c.L1CP_POWER_FRACTION)
    composite = (
        amplitude_d * code_d[chip_of_period] * data_subcarrier * data
        + amplitude_p * code_p[chip_of_period] * pilot_subcarrier * overlay_sign
    )

    samples = composite * np.exp(2j * np.pi * doppler_hz * t)
    return _add_noise(samples.astype(np.complex64), noise_sigma, rng)
