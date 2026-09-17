"""
Tiered (overlay) codes: synchronisation, wipe-off, and the gain it buys.

The headline claim of the overlay layer is that stripping the tiered code lets a
pilot integrate coherently across many primary code periods.  These tests hold it
to that: the counter must land on the right phase, the fold must actually be
coherent (N-fold magnitude, not sqrt(N)), and none of it may disturb a signal
that has no overlay at all.
"""

from __future__ import annotations

import numpy as np
import pytest

from utils import secondary_code
from utils.secondary_code import (
    OverlaySyncStatus,
    OverlaySynchroniser,
    brute_force_search,
    build_synchroniser,
    fft_search,
)

from . import synthetic


# --------------------------------------------------------------------------
# The search
# --------------------------------------------------------------------------

@pytest.mark.parametrize("true_offset", range(20))
def test_search_recovers_every_offset(true_offset):
    """Noise-free, the correlation peak must land exactly on the true offset."""
    _, nh_q = synthetic.get_l5_overlays()
    n = 4 * len(nh_q)
    prompts = (
        1000.0 * nh_q[(np.arange(n) + true_offset) % len(nh_q)]
    ).astype(complex)

    offset, confidence = brute_force_search(prompts, nh_q)
    assert offset == true_offset
    assert confidence > 2.0


def test_search_confidence_reflects_overlay_autocorrelation():
    """
    NH20's off-peak autocorrelation is 4 against a peak of 20, so a clean run of
    prompts should separate the true offset from the runner-up by about 5x.
    That ratio is what the confidence threshold is calibrated against.
    """
    _, nh_q = synthetic.get_l5_overlays()
    prompts = (1000.0 * nh_q[np.arange(len(nh_q))]).astype(complex)
    _, confidence = brute_force_search(prompts, nh_q)
    assert confidence == pytest.approx(5.0, rel=0.2)


def test_search_survives_a_carrier_phase_rotation():
    """
    The prompts carry whatever residual carrier phase the PLL is sitting at, and
    a Costas loop may sit at 180 degrees.  A constant rotation must not move the
    peak, since only the magnitude of the correlation is used.
    """
    _, nh_q = synthetic.get_l5_overlays()
    n = 4 * len(nh_q)
    for rotation in (1.0, -1.0, 1j, np.exp(0.7j)):
        prompts = 1000.0 * rotation * nh_q[(np.arange(n) + 7) % len(nh_q)]
        offset, _ = brute_force_search(prompts.astype(complex), nh_q)
        assert offset == 7, f"rotation {rotation} moved the peak"


# --------------------------------------------------------------------------
# The synchroniser
# --------------------------------------------------------------------------

def _feed(sync: OverlaySynchroniser, overlay: np.ndarray, true_offset: int, count: int):
    """Feed `count` consecutive prompts, returning the index that synced (or None)."""
    synced_at = None
    for n in range(count):
        if sync.observe(complex(1000.0 * overlay[(n + true_offset) % len(overlay)])):
            synced_at = n
        sync.advance()
    return synced_at


def test_synchroniser_leaves_counter_aligned_to_the_next_interval():
    """
    The contract: `signs()` describes the interval currently being folded and
    `advance()` steps to the next.  So after syncing on interval n, the counter
    must be the overlay index of interval n+1 -- an off-by-one here silently
    wipes off with the wrong chips and the fold cancels instead of adding.
    """
    _, nh_q = synthetic.get_l5_overlays()
    true_offset = 13
    sync = OverlaySynchroniser(reference_overlay=nh_q, period=len(nh_q))

    synced_at = _feed(sync, nh_q, true_offset, count=sync.observation_length)
    assert synced_at is not None, "should have synced once enough prompts arrived"
    assert sync.synced

    # The next interval to arrive is number synced_at + 1.
    expected = (true_offset + synced_at + 1) % len(nh_q)
    assert sync.counter == expected


def test_signs_are_unity_until_synced():
    """
    Before sync the fold must be a plain accumulation, so an un-synced channel
    behaves exactly like one with no overlay.
    """
    nh_i, nh_q = synthetic.get_l5_overlays()
    sync = OverlaySynchroniser(reference_overlay=nh_q, period=20)
    assert sync.status is OverlaySyncStatus.UNSYNCED
    np.testing.assert_array_equal(sync.signs([nh_i, nh_q]), [1, 1])


def test_one_counter_drives_every_overlay():
    """
    NH10 and NH20 advance on the same boundary, so a single counter mod
    lcm(10, 20) = 20 fixes both.  Locking the pilot therefore also locks the data
    component -- and NH10's period is the 10 ms CNAV symbol, so that is symbol
    synchronisation for free.
    """
    nh_i, nh_q = synthetic.get_l5_overlays()
    sync = build_synchroniser([nh_i, nh_q], reference_index=1)
    assert sync is not None
    assert sync.period == 20

    sync.status = OverlaySyncStatus.SYNCED
    for counter in range(40):
        sync.counter = counter % sync.period
        signs = sync.signs([nh_i, nh_q])
        assert signs[0] == nh_i[counter % len(nh_i)]
        assert signs[1] == nh_q[counter % len(nh_q)]


def test_no_overlays_means_no_synchroniser():
    """Signals without a tiered code must not pay for the machinery."""
    assert build_synchroniser([None], reference_index=0) is None
    assert build_synchroniser([None, None], reference_index=0) is None


def test_reference_must_have_an_overlay():
    nh_i, _ = synthetic.get_l5_overlays()
    with pytest.raises(ValueError, match="no overlay"):
        build_synchroniser([nh_i, None], reference_index=1)


def test_period_must_be_a_multiple_of_the_reference():
    _, nh_q = synthetic.get_l5_overlays()
    with pytest.raises(ValueError, match="multiple"):
        OverlaySynchroniser(reference_overlay=nh_q, period=30)


def test_ambiguous_run_keeps_trying_rather_than_restarting():
    """
    On a low-confidence result the buffer drops one overlay period instead of
    clearing, so a marginal signal converges as more prompts arrive rather than
    starting from empty every time.
    """
    _, nh_q = synthetic.get_l5_overlays()
    sync = OverlaySynchroniser(
        reference_overlay=nh_q, period=len(nh_q), confidence_threshold=1e9
    )
    for n in range(sync.observation_length * 2):
        sync.observe(complex(nh_q[n % len(nh_q)]))
        sync.advance()
    assert not sync.synced, "threshold was unreachable, so it must stay unsynced"
    # Still holding a partial run rather than nothing.
    assert 0 < len(sync._prompts) <= sync.observation_length


def test_search_strategy_is_pluggable():
    """
    Brute force is O(len * period), fine for NH10/NH20.  L1C's 1800-symbol
    overlay needs `fft_search` instead; either must drop in without touching the
    state machine.
    """
    _, nh_q = synthetic.get_l5_overlays()
    calls = []

    def fake_search(prompts, overlay):
        calls.append(len(prompts))
        return 3, 99.0

    sync = OverlaySynchroniser(
        reference_overlay=nh_q, period=len(nh_q), search=fake_search
    )
    _feed(sync, nh_q, true_offset=0, count=sync.observation_length)
    assert calls, "custom search was never consulted"
    assert sync.synced


# --------------------------------------------------------------------------
# The FFT search
# --------------------------------------------------------------------------


@pytest.mark.parametrize("true_offset", range(20))
def test_fft_search_agrees_with_brute_force_at_every_offset(true_offset):
    """
    The two must be interchangeable, not merely both plausible.  Brute force is
    the definition -- an explicit sum per shift -- so it is what the FFT form is
    checked against, offset by offset, on the overlay small enough to do both.
    """
    _, nh_q = synthetic.get_l5_overlays()
    rng = np.random.default_rng(true_offset)
    n = 4 * len(nh_q)
    # Complex prompts with a rotating phase and noise: the search must not depend
    # on the prompts being real or the carrier being perfectly tracked.
    clean = nh_q[(np.arange(n) + true_offset) % len(nh_q)] * np.exp(1j * 0.7)
    prompts = 1000.0 * clean + (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * 60.0

    assert fft_search(prompts, nh_q)[0] == brute_force_search(prompts, nh_q)[0] == true_offset
    assert fft_search(prompts, nh_q)[1] == pytest.approx(
        brute_force_search(prompts, nh_q)[1], rel=1e-9
    )


def test_fft_search_finds_a_long_overlay_from_a_fraction_of_one_period():
    """
    This is the case the FFT form exists for.  L1C's overlay is 1800 symbols of
    10 ms, so one period is 18 seconds; the search has to work on a couple of
    seconds of it.  A matched filter over 1800 hypotheses separates the true
    offset from every other long before the period is out.
    """
    rng = np.random.default_rng(0)
    overlay = rng.choice(np.array([-1, 1], dtype=np.int8), size=1800)

    true_offset = 1234
    window = 200  # 2 s at L1C's 10 ms primary period, against an 18 s period
    clean = overlay[(np.arange(window) + true_offset) % len(overlay)]
    prompts = 1000.0 * clean + (
        rng.standard_normal(window) + 1j * rng.standard_normal(window)
    ) * 250.0

    offset, confidence = fft_search(prompts, overlay)
    assert offset == true_offset
    assert confidence > 2.0
    assert (offset, confidence) == pytest.approx(brute_force_search(prompts, overlay))


def test_a_short_window_is_what_makes_a_long_overlay_tractable():
    """
    `periods_to_observe` is the wrong unit for L1C: four periods is 72 seconds of
    tracking before the first attempt.  `prompts_to_observe` sets the window
    directly, and the synchroniser must otherwise behave identically.
    """
    rng = np.random.default_rng(1)
    overlay = rng.choice(np.array([-1, 1], dtype=np.int8), size=1800)

    default = OverlaySynchroniser(reference_overlay=overlay, period=1800)
    assert default.observation_length == 4 * 1800

    sync = OverlaySynchroniser(
        reference_overlay=overlay, period=1800, prompts_to_observe=200, search=fft_search
    )
    assert sync.observation_length == 200

    true_offset = 97
    synced_at = _feed(sync, overlay, true_offset, count=sync.observation_length)
    assert synced_at == 199, "should sync on the 200th prompt, which fills the window"
    assert sync.synced
    # Same convention as the short overlays: the counter is left on the next
    # interval to arrive.
    assert sync.counter == (true_offset + synced_at + 1) % 1800


def test_the_fold_is_exact_for_a_window_longer_than_the_overlay():
    """
    A window of several whole overlay periods -- L5's default -- folds several
    prompts into each bin.  That has to give the same answer as summing after the
    shift, not merely a similar one, or the two searches would disagree exactly
    where L5 uses them.
    """
    _, nh_q = synthetic.get_l5_overlays()
    rng = np.random.default_rng(7)
    n = 7 * len(nh_q) + 3  # deliberately not a whole number of periods
    clean = nh_q[(np.arange(n) + 11) % len(nh_q)]
    prompts = 1000.0 * clean + (
        rng.standard_normal(n) + 1j * rng.standard_normal(n)
    ) * 80.0

    assert fft_search(prompts, nh_q)[0] == brute_force_search(prompts, nh_q)[0] == 11
    assert fft_search(prompts, nh_q)[1] == pytest.approx(
        brute_force_search(prompts, nh_q)[1], rel=1e-9
    )


def test_a_low_confidence_retry_keeps_most_of_a_short_window():
    """
    The retry drops the oldest slice rather than everything.  One overlay period
    is the natural slice, but L1C's window is a ninth of its overlay, so an
    uncapped drop would clear the buffer on every attempt and the synchroniser
    would never accumulate enough to decide.
    """
    rng = np.random.default_rng(2)
    overlay = rng.choice(np.array([-1, 1], dtype=np.int8), size=1800)
    sync = OverlaySynchroniser(
        reference_overlay=overlay, period=1800, prompts_to_observe=200,
        search=fft_search, confidence_threshold=1e9,
    )
    for n in range(400):
        sync.observe(complex(overlay[n % len(overlay)]))
        sync.advance()
    assert not sync.synced
    assert 0 < len(sync._prompts) <= sync.observation_length
    # Half the window survives each failed attempt, rather than none of it.
    assert len(sync._prompts) >= 100
