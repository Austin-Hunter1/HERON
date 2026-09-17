"""
`SymbolStream.symbol_snr_db` -- SNR read off the axis the signal is not on.

The estimator is three lines, and every one of them is a place to be subtly wrong:
forgetting that the noise axis measures noise per axis rather than in total,
reading the data axis as signal instead of signal-plus-noise, or reporting a
confident number when the signal is not on the axis the decoder is about to read.
These tests pin all three.
"""

from __future__ import annotations

import numpy as np
import pytest

from utils.nav.symbols import SymbolStream


def stream(values: np.ndarray, *, data_in_quadrature: bool = False) -> SymbolStream:
    n = len(values)
    return SymbolStream(
        values=values,
        uptime_ms=np.arange(n, dtype=float) * 20.0,
        code_phase_ms=np.arange(n, dtype=float) * 20.0,
        symbol_period_ms=20,
        signal_type_id="GPS_L1CA",
        epochs_per_symbol=20,
        dropped_epochs=0,
        data_in_quadrature=data_in_quadrature,
    )


def synthetic(snr_db: float, *, n: int = 20_000, seed: int = 0, quadrature: bool = False):
    """BPSK symbols at a known SNR, noise unit-variance on each axis."""
    rng = np.random.default_rng(seed)
    amplitude = 10.0 ** (snr_db / 20.0)
    signal = amplitude * rng.choice([-1.0, 1.0], n)
    noise_data, noise_other = rng.normal(0, 1, n), rng.normal(0, 1, n)
    values = (
        (noise_other + 1j * (signal + noise_data))
        if quadrature
        else ((signal + noise_data) + 1j * noise_other)
    )
    return stream(values, data_in_quadrature=quadrature)


@pytest.mark.parametrize("snr_db", [0.0, 3.0, 10.0, 20.0, 30.0])
def test_it_recovers_a_known_snr(snr_db):
    assert stream is not None
    assert synthetic(snr_db).symbol_snr_db == pytest.approx(snr_db, abs=0.3)


def test_it_recovers_the_same_snr_when_the_data_is_in_quadrature():
    """L5 puts its data on the imaginary axis; the estimator follows the data, so
    the answer must not depend on which axis that is."""
    upright = synthetic(15.0, seed=7).symbol_snr_db
    quadrature = synthetic(15.0, seed=7, quadrature=True).symbol_snr_db
    assert upright == pytest.approx(quadrature, abs=1e-9)


def test_reading_the_data_axis_as_signal_would_overstate_a_weak_one():
    """At 0 dB the data axis holds twice the noise axis, so the naive ratio reads
    +3 dB.  Subtracting the noise is what makes the estimate right, and the gap is
    widest exactly where the number matters."""
    s = synthetic(0.0)
    naive_db = 10 * np.log10(np.mean(s.soft**2) / np.mean(s.quadrature_axis**2))
    assert naive_db == pytest.approx(3.0, abs=0.3)
    assert s.symbol_snr_db == pytest.approx(0.0, abs=0.3)


def test_a_signal_on_both_axes_reports_no_signal_rather_than_a_number():
    """The wrong-axis case: a carrier not locked the way DATA_IN_QUADRATURE says
    splits power across both axes, and there is then nothing on the data axis that
    the noise axis does not also have."""
    rng = np.random.default_rng(3)
    n = 20_000
    split = 10.0 * rng.choice([-1.0, 1.0], n) / np.sqrt(2)
    values = (split + rng.normal(0, 1, n)) + 1j * (split + rng.normal(0, 1, n))
    assert stream(values).symbol_snr_db == -np.inf


def test_an_empty_stream_has_no_snr():
    assert stream(np.array([], dtype=complex)).symbol_snr_db == -np.inf
