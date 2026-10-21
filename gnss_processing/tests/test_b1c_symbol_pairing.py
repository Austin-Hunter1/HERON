"""
B1C: which 5 ms epoch opens a 10 ms symbol is read from the data, not from the code phase.

On the real L1 recording (D-070) the B1C channels run 5 ms epochs under a 10 ms symbol, and
every satellite has its symbol edge at code phase 5 ms (mod 10).  The code-phase pairing in
`symbols.extract` then sits half a symbol out: each "symbol" is half of two neighbours, so
about a third of them cancel and no B-CNAV1 frame decodes.  The synthetic B1C tests did not
see it because they track with 10 ms epochs, where an epoch is a symbol and there is nothing
to pair.

These tests build the failing situation directly, with known symbols, so that the old rule
picks the wrong phase and the data-driven rule has to pick the right one.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from utils.nav import symbols

EPOCH_MS = 5.0
SYMBOL_MS = 10.0


def _outputs(symbols_true, first_epoch_is_second_half, code_phase_start_ms, n_epochs=240,
             amplitude=1000.0, noise=60.0, seed=3):
    """
    Locked, overlay-synced epochs of the B1C data component (component 0), 5 ms each.

    The data sits on the imaginary axis (it is in quadrature with the carrier reference).
    `first_epoch_is_second_half` says whether epoch 0 is the second half of a symbol.
    `code_phase_start_ms` is what the channel logged as code phase for epoch 0, which is
    what the old pairing trusted.
    """
    rng = np.random.default_rng(seed)
    shift = 1 if first_epoch_is_second_half else 0
    symbol_of_epoch = (np.arange(n_epochs) + shift) // 2
    data = np.asarray(symbols_true, float)[symbol_of_epoch]
    prompt = np.zeros((n_epochs, 2), dtype=complex)
    prompt[:, 0] = rng.normal(0.0, noise, n_epochs) + 1j * (amplitude * data + rng.normal(0.0, noise, n_epochs))
    prompt[:, 1] = amplitude * 2.0 + 1j * rng.normal(0.0, noise, n_epochs)  # the pilot, on the carrier axis
    return SimpleNamespace(
        valid=slice(0, n_epochs),
        pll_mode=np.ones(n_epochs, dtype=bool),
        overlay_synced=np.ones(n_epochs, dtype=bool),
        epoch_duration_ms=np.full(n_epochs, EPOCH_MS),
        prompt_corr=prompt,
        uptime_epoch_ms=np.arange(n_epochs) * EPOCH_MS,
        code_phase_ms=code_phase_start_ms + np.arange(n_epochs) * EPOCH_MS,
        bit_synced=np.zeros(n_epochs, dtype=bool),
        output_index=n_epochs,
    )


def _old_rule_phase(code_phase_start_ms):
    offset = code_phase_start_ms % SYMBOL_MS
    return int(round((SYMBOL_MS - offset) % SYMBOL_MS / EPOCH_MS))


def _truth(n=200, seed=11):
    return np.where(np.random.default_rng(seed).integers(0, 2, n) == 1, -1.0, 1.0)


@pytest.mark.parametrize(
    "first_epoch_is_second_half, code_phase_start_ms",
    [
        (False, 5.0),   # the real B1C case: the data says "symbol start", the code phase says "mid-symbol"
        (True, 10.0),   # and the opposite
        (False, 10.0),  # both agree
        (True, 5.0),    # both agree
    ],
)
def test_the_symbols_are_the_true_symbols_whatever_the_code_phase_says(
    first_epoch_is_second_half, code_phase_start_ms
):
    truth = _truth()
    stream = symbols.extract(_outputs(truth, first_epoch_is_second_half, code_phase_start_ms), "BDS_B1C")

    assert stream.epochs_per_symbol == 2
    first = 1 if first_epoch_is_second_half else 0
    # The channel's first epoch opens symbol 0, or closes it (then symbol 1 is the first whole one).
    first_whole_symbol = first
    expected = truth[first_whole_symbol:first_whole_symbol + len(stream.values)]
    assert len(stream.values) > 100
    soft = np.asarray(stream.soft)
    # Carrier phase ambiguity cannot matter here (the data axis is fixed): the signs match exactly.
    assert np.array_equal(np.sign(soft), expected)
    # No symbol is a half-cancelled mixture of two: every one has the full two-epoch amplitude.
    assert np.abs(soft).min() > 1.5 * 1000.0


def test_the_scenario_really_defeats_the_old_rule():
    """Guard against a test that passes for the wrong reason: the old rule must pick the wrong phase here."""
    truth_phase = 0  # epoch 0 opens a symbol
    assert _old_rule_phase(5.0) != truth_phase


def test_without_the_switch_the_old_pairing_is_kept():
    """A signal not in `SYMBOL_PHASE_FROM_DATA` still uses the code phase (GPS L5, B2a, ...)."""
    assert symbols.SYMBOL_PHASE_FROM_DATA.get("GPS_L5", False) is False
    assert symbols.SYMBOL_PHASE_FROM_DATA["BDS_B1C"] is True


def test_the_pairing_is_read_from_the_data_alone():
    truth = _truth()
    outputs = _outputs(truth, first_epoch_is_second_half=True, code_phase_start_ms=0.0)
    assert symbols.symbol_phase_from_data(outputs.prompt_corr[:, 0], 2, in_quadrature=True) == 1
    outputs = _outputs(truth, first_epoch_is_second_half=False, code_phase_start_ms=0.0)
    assert symbols.symbol_phase_from_data(outputs.prompt_corr[:, 0], 2, in_quadrature=True) == 0


def test_noise_gives_no_pairing_so_the_code_phase_is_used_instead():
    rng = np.random.default_rng(5)
    noise = rng.normal(0, 1, 400) + 1j * rng.normal(0, 1, 400)
    assert symbols.symbol_phase_from_data(noise, 2, in_quadrature=True) is None


def test_a_short_stretch_gives_no_pairing():
    truth = _truth()
    outputs = _outputs(truth, False, 0.0, n_epochs=20)
    assert symbols.symbol_phase_from_data(outputs.prompt_corr[:, 0], 2, in_quadrature=True) is None


def test_one_epoch_per_symbol_has_nothing_to_pair():
    assert symbols.symbol_phase_from_data(np.ones(100, dtype=complex), 1, in_quadrature=True) is None


def test_noisy_data_falls_back_to_the_code_phase_rule():
    """With the data axis buried in noise the extraction still runs, on the code phase as before."""
    truth = _truth()
    outputs = _outputs(truth, False, 5.0, amplitude=1.0, noise=5000.0)
    stream = symbols.extract(outputs, "BDS_B1C")
    assert len(stream.values) > 0
    assert stream.code_phase_ms[0] % SYMBOL_MS == pytest.approx(0.0, abs=1e-9) or stream.code_phase_ms[0] % SYMBOL_MS > 9.999
