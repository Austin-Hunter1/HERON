"""
Golden regression: tracking output must not drift.

A failure here means numerical behaviour changed.  Investigate before regenerating
the goldens -- that is the whole point of the file.

The baseline was regenerated once, during the channel unification, for two
deliberate and verified changes:

  1. Replica chip indices are now floored rather than truncated toward zero.
     Correlation intervals are aligned to integer ms of code phase and L1 C/A's
     code period is 1 ms, so every interval begins at code phase ~ 0 chips and the late
     bin at -0.5 chips is negative *on every epoch*.  Truncation read chip 0
     instead of the previous period's last chip, biasing the late correlator and
     the DLL systematically.  See `test_aligned_epoch_late_bin_reads_the_previous_code_period`.

  2. The delay discriminator combines magnitudes in float64.  Previously
     `np.abs()` on a complex64 correlator returned float32, so the discriminator
     carried float32 precision into a float64 state update.

Both were validated against explicitly constructed replicas in
`tests/test_correlator.py` before the baseline was replaced.

The L5 baselines were regenerated again when the tiered-code layer landed: those
channels now synchronise their Neuman-Hofman overlay part-way through the run and
switch to 20 ms coherent integration on the pilot.  L1 C/A and L2C were verified
unchanged at that point -- with no overlay the fold uses unit signs and an epoch
of one interval, which is arithmetically the previous behaviour.

Regenerated for every family when the loop filter stopped taking the correlation
interval's code phase for the epoch's stream time.  Those are different quantities
that merely start out numerically close, and the correlator propagates carrier
phase over the difference, so a `dt_sec` wrong by the acquired code phase `d`
injects `d * doppler` cycles -- which then *moves* as the loops correct Doppler and
comes back through the FLL amplified by `d / CORRELATION_INTERVAL_MS`.  Code phase hid it by
cancelling.  L1 C/A and L5 acquire with `d < 1 ms` and were stable; L2C acquires on
CM's 20 ms period and diverged above about 4 ms, i.e. most real code phases --
`l2c_late_code_phase` exists to cover exactly that and fails without the fix.

Every epoch's carrier phase moved, so all eight baselines changed, but no
convergence did: final Doppler errors are unchanged to the last printed digit for
L1 C/A and L2C (-0.000, -0.063, -0.000, -0.024 Hz) and within 5 mHz for L5.
`outputs.uptime_epoch_ms` now records real stream time rather than code phase, so
anything plotting against it shifts by the acquired code phase.

All nine baselines were regenerated when the prior, the posterior and the NCO were
separated into three stages.  `signal_state` is now the PRIOR at the start of the
epoch about to be processed -- the constructor carries the acquired state onto the
first interval boundary, and each loop-filter run leaves it on the next one -- so
every interval in an epoch propagates by dt = 0, 1, ... N-1 ms instead of a full
epoch more.  `outputs` is the POSTERIOR: that prior plus the filtered corrections,
still at the epoch's own start, describing the epoch just measured.  The propagation
to the next epoch happens last, using the corrected rate, which is the rate the NCO
actually runs at over that span.

L1 C/A and L2C move only in the last bits (one interval per epoch, so there was
never anything to stage).  L5 moves materially because its epochs span ten intervals
and the old code propagated across them with the rate estimated at the END rather
than the start.  Convergence is unaffected everywhere.

Two details the split forces.  The FLL divides a phase difference by a time, and
that time is the epoch span rather than the propagation step -- they stopped being
the same quantity.  And `outputs.code_phase_ms` has to read the posterior local
rather than `signal_state`, which now points at the next epoch.

Regenerated once more, for float reassociation only, when time stopped being
derived from code phase.  Epoch boundaries are defined in CODE PHASE -- that is the
alignment requirement -- so `compute_start_and_stop_uptime_ms` is now the single
place that converts one into a stream time.  Every `dt` elsewhere is a difference of
uptimes, and `propagate_to_uptime_ms` carries code phase and carrier phase forward by
rate x dt rather than asserting the code phase to its boundary value.  Both are
estimated states, like carrier phase; neither is a clock.  Doppler moved by <= 5e-13
against a scale of 1e3 and code phase by <= 6e-14 against 2e3; only `delta_omega`
exceeds the 1e-9 tolerance, at ~1e-8 relative, because its divisor takes the new
route.

Every nav-bit-carrying baseline was regenerated when the synthetic generator
started keying data symbols to the code period rather than to absolute time.  The
symbol is synchronous with the primary code in all three signals, so a flip falls
exactly on a code period boundary and can never land inside a correlation interval;
keying it to `t` put the flip `code_phase_ms` into each period.  That fixture
artefact was making a symbol-length coherent accumulation look worse than it is --
L5's I/Q ratio went from 0.94 / 0.86 to exactly 1.00 once corrected.

Epochs are also now labelled by the stream time at which they STARTED rather than by
their final interval, which for a 10 ms L5 epoch moves the timestamp 9 ms earlier.

The L5 baselines moved once more when I rejoined the post-sync delay
discriminator.  Capping the epoch at I's symbol is what made that possible: I now
comes out at full magnitude rather than half-cancelled, so combining two
equal-power components non-coherently is worth ~3 dB.  Only the carrier loop stays
on the pilot alone, where the dataless four-quadrant discriminator pays.  Doppler
convergence is unchanged; the DLL residual barely moves in these scenarios because
none of them is DLL-noise-limited.

The L5 baselines moved again when the post-sync epoch dropped from NH20's 20 ms to
one CNAV symbol, 10 ms.  One epoch serves every component, so its length is the
shortest limit among them: Q is a dataless pilot and would take 20 ms, but I carries
10 ms symbols and would span two of them.  The pilot gives up 3 dB; I comes back at
full magnitude (I/Q went from heavily suppressed to 1.00, 0.94, 0.86 across the three
scenarios) and is symbol-synchronous, and the loops end up wider at the shorter epoch
(PLL 10 Hz rather than 5 Hz) so convergence improved as well: -0.041 -> -0.000,
+0.045 -> -0.001, +0.020 -> -0.015 Hz.

The L5 baselines moved again when extended epochs were anchored to a code phase
the epoch length divides.  A single interval is safe wherever it starts, because
every period that matters is a whole number of milliseconds; an epoch of N
intervals is not, and begun at an arbitrary code phase it can span a data symbol
boundary.  Extension now waits for the next multiple of N ms of code phase -- at
most N-1 intervals, once -- so epochs tile deterministically from code phase 0
rather than from wherever acquisition happened to land.  Doppler errors improved
slightly (-0.051 -> -0.041, +0.054 -> +0.045, +0.031 -> +0.020 Hz).

And once more when overlay sync was decoupled from extending coherent integration.
Sync now only enables wipe-off and the pilot discriminator; the accumulation
lengthens at the next loop-filter run, gated on PLL lock, because what limits
integration length is Doppler error rather than the overlay.  The visible effect is
one extra 1 ms epoch: the interval that triggers sync is emitted as its own
wiped-off epoch instead of becoming the first of a 20 ms accumulation, so the 20 ms
grid that follows is offset by 1 ms (l5_clean epochs ... 88, 89, 90, 110, 130
rather than ... 88, 89, 109, 129).  Convergence is unchanged: final Doppler error
moved from -0.0545 to -0.0534 Hz.  L1 C/A and L2C have no overlay and are untouched.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

from .driver import OUTPUT_FIELDS, run_scenario
from .scenarios import SCENARIOS, TrackingScenario

GOLDEN_DIR = pathlib.Path(__file__).parent / "golden"

# The unified correlator sums the same products in a different loop order, so
# floating-point association can differ in the last bits.  Tolerances are far
# tighter than any real behavioural change would produce.
RTOL = 1e-9
ATOL = 1e-9


def _load_golden(name: str):
    path = GOLDEN_DIR / f"{name}.npz"
    if not path.exists():
        pytest.fail(f"missing golden baseline {path}; run `python -m tests.generate_golden`")
    return np.load(path, allow_pickle=False)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_tracking_matches_golden(scenario: TrackingScenario) -> None:
    golden = _load_golden(scenario.name)
    actual = run_scenario(scenario)

    assert int(actual["output_index"]) == int(golden["output_index"]), "epoch count changed"
    assert str(actual["final_mode"]) == str(golden["final_mode"]), "final loop mode changed"

    for field in OUTPUT_FIELDS:
        expected_arr = golden[field]
        actual_arr = actual[field]
        assert actual_arr.shape == expected_arr.shape, f"{field}: shape changed"
        np.testing.assert_allclose(
            actual_arr, expected_arr, rtol=RTOL, atol=ATOL, err_msg=f"{field} drifted"
        )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_tracking_converges(scenario: TrackingScenario) -> None:
    """
    Behavioural floor, independent of the goldens.

    Guards against the goldens themselves being regenerated from broken code: these
    thresholds encode what "tracking works" means rather than what it used to output.
    """
    actual = run_scenario(scenario)

    final_doppler_error = float(actual["doppler_freq_hz"][-1]) - scenario.doppler_hz
    assert abs(final_doppler_error) < 5.0, f"Doppler did not converge: {final_doppler_error:+.2f} Hz"

    assert str(actual["final_mode"]) == "PLL", "loop never reached PLL lock"

    assert float(actual["prompt_corr_circ_length"][-1]) > 0.9, "prompt phase not coherent"

    # Prompt should dominate early/late for an aligned 0.5-chip EPL correlator --
    # on the component the loops actually run on.  That is not component 0 in
    # general: L5 tracks its Q pilot, and its I component is data-limited past
    # 10 ms, so I's correlators say nothing about lock quality.
    component = int(actual["carrier_component"])
    prompt = actual["prompt_corr"][:, component]
    early = actual["early_corr"][:, component]
    late = actual["late_corr"][:, component]
    tail = slice(-min(500, len(prompt)), None)
    prompt_mag = np.abs(prompt[tail]).mean()
    assert prompt_mag > np.abs(early[tail]).mean(), "prompt not above early"
    assert prompt_mag > np.abs(late[tail]).mean(), "prompt not above late"
