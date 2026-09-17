"""C/N0 estimation by the variance summing method."""

import numpy as np
import pytest

from utils import tracking_channel
from utils.tracking_channel import CN0EstimatorParameters, estimate_cn0_vsm

INTERVAL_S = tracking_channel.CORRELATION_INTERVAL_MS * 1e-3


def _prompts(cn0_dbhz, count, rng, noise_power=1.0):
    """
    Prompts at a known C/N0.

    With E|n|^2 = noise_power, C/N0 = (A^2 / noise_power) / T, so the amplitude
    that realises a given C/N0 is fixed.  Phase is arbitrary and constant -- VSM
    reads magnitudes only, which is exactly what the estimator's independence from
    data bits and carrier error rests on.
    """
    amplitude = np.sqrt(10 ** (cn0_dbhz / 10) * INTERVAL_S * noise_power)
    noise = (rng.standard_normal(count) + 1j * rng.standard_normal(count))
    noise *= np.sqrt(noise_power / 2)
    return amplitude * np.exp(1j * rng.uniform(0, 2 * np.pi)) + noise


@pytest.mark.parametrize("cn0_dbhz", [30.0, 40.0, 45.0, 50.0])
def test_vsm_recovers_a_known_cn0(cn0_dbhz):
    """
    The check no end-to-end run can make: a wrong moment formula still produces a
    plausible-looking number, and only a known answer catches it.
    """
    rng = np.random.default_rng(1)
    errors = [
        estimate_cn0_vsm(np.abs(_prompts(cn0_dbhz, 20000, rng)) ** 2, INTERVAL_S) - cn0_dbhz
        for _ in range(5)
    ]
    assert np.max(np.abs(errors)) < 0.5, f"errors {errors}"


def test_vsm_is_indifferent_to_phase_and_sign():
    """
    Magnitudes only, so data bits, carrier phase error and an un-stripped overlay
    must not move the estimate.  This is why C/N0 is available before the
    secondary code syncs.
    """
    rng = np.random.default_rng(2)
    clean = _prompts(45.0, 20000, rng)
    flipped = clean * rng.choice([-1.0, 1.0], size=clean.size)          # data / overlay
    rotated = flipped * np.exp(1j * rng.uniform(-0.4, 0.4, clean.size))  # phase error

    base = estimate_cn0_vsm(np.abs(clean) ** 2, INTERVAL_S)
    assert estimate_cn0_vsm(np.abs(flipped) ** 2, INTERVAL_S) == pytest.approx(base)
    assert estimate_cn0_vsm(np.abs(rotated) ** 2, INTERVAL_S) == pytest.approx(base)


def test_vsm_scales_with_the_stated_integration_time():
    """C/N0 is a density, so halving the stated integration time adds 3 dB."""
    rng = np.random.default_rng(3)
    power = np.abs(_prompts(45.0, 20000, rng)) ** 2
    assert estimate_cn0_vsm(power, INTERVAL_S / 2) == pytest.approx(
        estimate_cn0_vsm(power, INTERVAL_S) + 10 * np.log10(2), abs=1e-9
    )


def test_degenerate_inputs_return_nan_rather_than_raising():
    """
    One bad window must not take down a tracking run, so the estimator reports a
    gap instead of an exception.
    """
    rng = np.random.default_rng(4)
    noise_only = (rng.standard_normal(4000) + 1j * rng.standard_normal(4000)) / np.sqrt(2)
    # Noise alone: the discriminant goes negative about as often as not, and either
    # way the answer must not be an exception.
    assert not np.isinf(estimate_cn0_vsm(np.abs(noise_only) ** 2, INTERVAL_S))

    # No noise at all: Pn collapses to zero and C/N0 is unbounded.
    constant = np.full(4000, 4.0)
    assert np.isnan(estimate_cn0_vsm(constant, INTERVAL_S))

    assert np.isnan(estimate_cn0_vsm(np.array([]), INTERVAL_S))


def test_estimator_parameters_reject_unusable_windows():
    """
    Below ~100 samples VSM's fourth moment stops being trustworthy, and a quietly
    wrong C/N0 is worse than a refusal.
    """
    with pytest.raises(ValueError, match="at least 100"):
        CN0EstimatorParameters(period_ms=50)
    with pytest.raises(ValueError, match="overlap_fraction"):
        CN0EstimatorParameters(period_ms=1000, overlap_fraction=1.0)

    assert CN0EstimatorParameters(period_ms=1000, overlap_fraction=0.5).hop_ms == 500
    assert CN0EstimatorParameters(period_ms=200, overlap_fraction=0.0).hop_ms == 200
