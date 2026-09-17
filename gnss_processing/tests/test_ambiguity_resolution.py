"""
Resolving the long-code ambiguity acquisition leaves.

Two signals, one mechanism.  L2C acquires on CM's 20 ms period and leaves CL --
75 times longer -- unlocated; L5 acquires on I x NH10's 10 ms composite and leaves
NH20's 20 ms half-resolved.  Both come down to scoring a handful of candidate
offsets against the dwell that acquisition already used.

The L2C case is not merely an enhancement: until it runs, the tracking channel
generates CL from the phase CM supplied, i.e. as though CL sat at its code origin.
That is right 1 time in 75, and the synthetic generator agrees with the channel by
construction, so nothing else in the suite can catch it.
"""

from __future__ import annotations

import numpy as np
import pytest

import gnss_tools.signals.gps_l2c as gps_l2c
import gnss_tools.signals.gps_l5 as gps_l5

from utils import bpsk_acquisition
from utils.ambiguity_resolution import resolve_code_ambiguity
from utils.bpsk_correlation import correlate__multicomponent
from utils.signal_interfaces import (
    GpsL1CA,
    GpsL2C,
    GpsL5,
    acquisition_code_period_ms,
    acquisition_resolves_overlay_phase,
    build_acquisition_code_params,
    build_ambiguity_search,
    build_signals,
    create_tracking_channels,
    resolve_acquisition_ambiguities,
)
from utils import tracking_channel

from . import synthetic

L2C_SAMP_RATE = 5_000_000
L5_SAMP_RATE = 25_000_000
PRN = 1



# --------------------------------------------------------------------------
# L2C: CM acquisition -> which of CL's 75 blocks
# --------------------------------------------------------------------------

def _l2c_config():
    return bpsk_acquisition.AcquisitionConfiguration(
        coherent_duration_replica_ms=20, coherent_duration_sample_ms=5.0, num_blocks=4,
        sample_rate=L2C_SAMP_RATE, min_search_doppler_hz=-2000, max_search_doppler_hz=2000,
    )


def _l2c_dwell(*, cl_block, cm_phase_ms=0.61, doppler_hz=0.0, noise_sigma=6.0, prn=PRN, seed=11):
    """
    Samples whose CL code sits `cl_block` CM periods into the 1.5 s pilot.

    The generator keys everything off one code phase, so placing the signal in
    block h is just h * 20 ms of extra phase -- which CM acquisition then folds
    away, leaving exactly the ambiguity under test.
    """
    config = _l2c_config()
    samples = synthetic.generate_l2c_samples(
        prn=prn, start_sec=0.0, duration_sec=config.acq_total_duration_ms * 1e-3,
        samp_rate=L2C_SAMP_RATE, doppler_hz=doppler_hz,
        code_phase_ms=cl_block * 20.0 + cm_phase_ms,
        nav_bits=True, noise_sigma=noise_sigma, rng=np.random.default_rng(seed),
    )
    return samples, config


def _acquire_l2c(samples, config, prn=PRN):
    signals = build_signals(GpsL2C, prns=[prn])
    result = bpsk_acquisition.run_acquisition(
        sample_block=samples, sample_block_uptime_epoch_ms=0.0, acq_config=config,
        code_parameters=build_acquisition_code_params(GpsL2C, signals),
        prob_false_alarm_total=1e-6, noise_var_method="abscorrvar",
    )[f"G{prn:02d}"]
    return result, signals


@pytest.mark.parametrize("cl_block", [0, 1, 37, 74])
def test_cl_block_is_recovered(cl_block):
    """The search must find the block CM acquisition folded away."""
    samples, config = _l2c_dwell(cl_block=cl_block)
    acq, signals = _acquire_l2c(samples, config)
    assert acq.signal_detected
    # CM acquisition sees only the phase within its own 20 ms period.
    assert acq.acq_code_phase_seconds * 1e3 == pytest.approx(0.61, abs=1e-3)

    resolutions = resolve_acquisition_ambiguities(
        GpsL2C, signals, {f"G{PRN:02d}": acq}, samples, config
    )
    resolution = resolutions[f"G{PRN:02d}"]

    assert resolution.best_index == cl_block
    assert resolution.resolved, f"confidence only {resolution.confidence:.2f}"
    assert len(resolution.scores) == 75


def test_an_absent_satellite_gives_no_confident_block():
    """All 75 hypotheses score noise, so nothing should stand out."""
    samples, config = _l2c_dwell(cl_block=12, prn=PRN)
    _, signals = _acquire_l2c(samples, config, prn=PRN)
    # Score PRN 7's CL code against PRN 1's signal.
    other = build_signals(GpsL2C, prns=[7])
    acq, _ = _acquire_l2c(samples, config, prn=PRN)

    plan = build_ambiguity_search(GpsL2C, other["G07"])
    resolution = resolve_code_ambiguity(
        samples, L2C_SAMP_RATE,
        code_set=plan.code_set, scored_component=plan.scored_component,
        acquired_code_phase_sec=acq.acq_code_phase_seconds,
        doppler_hz=acq.acq_doppler_hz, carrier_freq_hz=gps_l2c.CARRIER_FREQ,
        nominal_code_rate_chips_per_sec=plan.code_rate_chips_per_sec,
        coherent_length_samples=config.coherent_length_samples,
        num_blocks=config.num_blocks, num_hypotheses=plan.num_hypotheses,
        hypothesis_stride_chips=plan.hypothesis_stride_chips,
    )
    assert not resolution.resolved
    assert resolution.confidence < 2.0


def test_cl_only_correlates_at_the_resolved_phase():
    """
    The defect the search closes, measured directly.

    Correlating the real CM/CL code set at the phase CM acquisition supplies gives
    a healthy CM prompt and a CL prompt in the noise, because CL is generated 37
    blocks away from where it actually is.  Adding the resolved offset brings CL up
    to CM's level -- they are equal power.
    """
    cl_block = 37
    samples, config = _l2c_dwell(cl_block=cl_block, noise_sigma=0.0)
    acq, signals = _acquire_l2c(samples, config)
    code_set = signals[f"G{PRN:02d}"].code_set
    cm, cl = code_set.index_of("L2CM"), code_set.index_of("L2CL")

    def prompts(offset_chips):
        out = np.zeros((1, code_set.num_components), dtype=np.complex64)
        correlate__multicomponent(
            samples[: config.coherent_length_samples], L2C_SAMP_RATE, 0.0,
            acq.acq_doppler_hz, code_set, gps_l2c.CODE_RATE_L2CLM,
            acq.acq_code_phase_seconds * gps_l2c.CODE_RATE_L2CLM + offset_chips,
            np.zeros(1, dtype=np.float64), out,
        )
        return abs(complex(out[0, cm])), abs(complex(out[0, cl]))

    cm_unresolved, cl_unresolved = prompts(0.0)
    cm_resolved, cl_resolved = prompts(cl_block * 2.0 * gps_l2c.CODE_LENGTH_L2CM)

    # CM is unaffected: the offset is a whole number of CM periods.
    assert cm_resolved == pytest.approx(cm_unresolved, rel=1e-6)
    assert cl_unresolved < 0.15 * cm_unresolved, "CL should be lost before resolution"
    assert cl_resolved > 0.5 * cm_resolved, "CL should match CM once resolved"


def test_resolution_extends_the_seeded_code_phase():
    """
    The resolved block is simply part of the code phase: acquisition pinned it
    modulo CM's 20 ms period, and the search supplies the rest.
    """
    cl_block = 37
    samples, config = _l2c_dwell(cl_block=cl_block)
    acq, signals = _acquire_l2c(samples, config)
    resolutions = resolve_acquisition_ambiguities(
        GpsL2C, signals, {f"G{PRN:02d}": acq}, samples, config
    )

    without = _track_l2c(signals, acq, None, buffers=0)
    with_ = _track_l2c(signals, acq, resolutions, buffers=0)

    delta_ms = (
        with_.channel.signal_state.code_phase_ms - without.channel.signal_state.code_phase_ms
    )
    assert delta_ms == pytest.approx(cl_block * 20.0, abs=1e-6)

    # The clock is still stream time, not code phase -- separate axes.  It starts on
    # the first interval boundary rather than at the acquisition epoch, because the
    # constructor carries the state there so that `signal_state` is the prior AT AN
    # EPOCH START from the very first epoch.  Acquisition reported 0.61 ms, so the
    # first boundary is at code phase 1 ms, i.e. 0.39 ms of stream time later.
    assert with_.channel.signal_state.uptime_epoch_ms == pytest.approx(0.39, abs=1e-3)
    assert with_.channel.signal_state.code_phase_ms % 1.0 == pytest.approx(0.0, abs=1e-9)


def _track_l2c(signals, acq, resolutions, *, cl_block=37, buffers=4, buffer_ms=50,
               noise_sigma=6.0, seed=11):
    from utils import sample_streaming

    loop_params = tracking_channel.TrackingLoopParameters(
        DLL_bandwidth_hz=2.0, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0,
        coherent_duration_ms=1, EPL_chip_spacing=0.5,
    )
    kwargs = dict(
        signal_type=GpsL2C, signals=signals,
        acquisition_results={f"G{PRN:02d}": acq},
        tracking_signal_ids=[f"G{PRN:02d}"], loop_params=loop_params,
        output_capacity=4000,
    )
    if resolutions is not None:
        kwargs["ambiguity_resolutions"] = resolutions
    adapter = create_tracking_channels(**kwargs)[f"G{PRN:02d}"]

    rng = np.random.default_rng(seed)
    for i in range(buffers):
        adapter.process_sample_buffer(sample_streaming.SampleBuffer(
            samples=synthetic.generate_l2c_samples(
                prn=PRN, start_sec=i * buffer_ms * 1e-3, duration_sec=buffer_ms * 1e-3,
                samp_rate=L2C_SAMP_RATE, doppler_hz=0.0,
                code_phase_ms=cl_block * 20.0 + 0.61, nav_bits=True,
                noise_sigma=noise_sigma, rng=rng),
            start_uptime_ms=i * buffer_ms, samp_rate=L2C_SAMP_RATE))
    return adapter


def test_cl_accumulates_only_once_the_block_is_resolved():
    """
    End to end, and the test that would have caught pushing the offset through
    `code_phase_ms`: CL only produces a real correlation when the channel both
    knows its block and still knows what time it is.
    """
    cl_block = 37
    samples, config = _l2c_dwell(cl_block=cl_block)
    acq, signals = _acquire_l2c(samples, config)
    resolutions = resolve_acquisition_ambiguities(
        GpsL2C, signals, {f"G{PRN:02d}": acq}, samples, config
    )

    unresolved = _track_l2c(signals, acq, None, cl_block=cl_block)
    resolved = _track_l2c(signals, acq, resolutions, cl_block=cl_block)

    def mean_prompts(adapter):
        n = adapter.outputs.output_index
        tail = slice(n // 2, n)
        cm = adapter.component_index("L2CM")
        cl = adapter.component_index("L2CL")
        return (
            np.abs(adapter.outputs.prompt_corr[tail, cm]).mean(),
            np.abs(adapter.outputs.prompt_corr[tail, cl]).mean(),
        )

    cm_u, cl_u = mean_prompts(unresolved)
    cm_r, cl_r = mean_prompts(resolved)

    # CM is untouched: the offset is a whole number of CM periods.
    assert cm_r == pytest.approx(cm_u, rel=0.2)
    assert cl_u < 0.3 * cm_u, f"CL should be noise before resolution ({cl_u:.0f} vs {cm_u:.0f})"
    assert cl_r > 0.5 * cm_r, f"CL should match CM after resolution ({cl_r:.0f} vs {cm_r:.0f})"


# --------------------------------------------------------------------------
# L5: nothing left to resolve
# --------------------------------------------------------------------------

def test_l5_needs_no_ambiguity_search():
    """
    Acquiring on Q x NH20 spans a whole overlay period, so the recovered code phase
    pins the shared counter mod 20 -- and NH10's index is that mod 10.  Both
    overlays fall out of acquisition, so the two-hypothesis search that the shorter
    I x NH10 composite required is gone.

    Q is also the right component to acquire on for a second reason: it is dataless,
    so its composite is exact.  On I the CNAV symbol period equals NH10's, which
    makes data a sign on each whole composite period and can shift the recovered
    index by one.
    """
    signal = build_signals(GpsL5, prns=[PRN])[f"G{PRN:02d}"]

    assert build_ambiguity_search(GpsL5, signal) is None
    assert acquisition_resolves_overlay_phase(GpsL5, signal)
    assert signal.overlay_period_ms == 20
    assert acquisition_code_period_ms(GpsL5, signal) == pytest.approx(20.0)


def test_l2c_still_needs_one():
    """L2C acquires on CM's 20 ms period; CL runs 75 times longer."""
    signal = build_signals(GpsL2C, prns=[PRN])[f"G{PRN:02d}"]
    plan = build_ambiguity_search(GpsL2C, signal)
    assert plan is not None
    assert plan.num_hypotheses == 75
    assert not acquisition_resolves_overlay_phase(GpsL2C, signal)


# --------------------------------------------------------------------------
# Generic behaviour
# --------------------------------------------------------------------------

def test_l1ca_has_nothing_to_resolve():
    """L1 C/A acquires on its whole 1 ms code, so there is no ambiguity left."""
    signals = build_signals(GpsL1CA, prns=[PRN])
    assert build_ambiguity_search(GpsL1CA, signals[f"G{PRN:02d}"]) is None
    assert resolve_acquisition_ambiguities(
        GpsL1CA, signals, {}, np.zeros(4), _l2c_config()
    ) == {}


def _generic_kwargs(**overrides):
    plan = build_ambiguity_search(GpsL2C, build_signals(GpsL2C, prns=[PRN])[f"G{PRN:02d}"])
    kwargs = dict(
        code_set=plan.code_set, scored_component=0, acquired_code_phase_sec=0.0,
        doppler_hz=0.0, carrier_freq_hz=gps_l2c.CARRIER_FREQ,
        nominal_code_rate_chips_per_sec=plan.code_rate_chips_per_sec,
        coherent_length_samples=100, num_blocks=4, num_hypotheses=2,
        hypothesis_stride_chips=plan.hypothesis_stride_chips,
    )
    kwargs.update(overrides)
    return kwargs


def test_short_sample_block_is_rejected():
    with pytest.raises(ValueError, match="need 400 samples"):
        resolve_code_ambiguity(
            np.zeros(10, dtype=np.complex64), L2C_SAMP_RATE, **_generic_kwargs()
        )


def test_scored_component_is_range_checked():
    with pytest.raises(ValueError, match="scored_component"):
        resolve_code_ambiguity(
            np.zeros(400, dtype=np.complex64), L2C_SAMP_RATE,
            **_generic_kwargs(scored_component=5),
        )


def test_an_unresolved_search_warns_that_integration_is_not_active():
    """
    Guessing would be worse than waiting -- a mis-phased wipe-off never locks once
    the pilot discriminator drops Costas wrapping -- but the user should be told,
    because the visible consequence is that the configured integration is inert.
    """
    import warnings

    from utils.ambiguity_resolution import AmbiguityResolution

    signals = build_signals(GpsL2C, prns=[PRN])
    config = _l2c_config()
    corr = bpsk_acquisition.CorrelationResult(np.zeros((1, 1)), 0.0, 1.0, 0.0, 1.0)
    acq = {
        f"G{PRN:02d}": bpsk_acquisition.AcquisitionResult(
            0.0, f"G{PRN:02d}", 0, 7500, 999.0, 1e-6, 1e-13, 1000, 100.0, 1.0,
            True, corr, config,
        )
    }
    loop_params = tracking_channel.TrackingLoopParameters(
        DLL_bandwidth_hz=2.0, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0,
        coherent_duration_ms=1,
    )
    unresolved = {
        f"G{PRN:02d}": AmbiguityResolution(
            best_index=0, confidence=1.14, scores=np.array([1.0, 0.88]),
            offset_chips=0.0, resolved=False,
        )
    }

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        adapter = create_tracking_channels(
            GpsL2C, signals=signals, acquisition_results=acq,
            tracking_signal_ids=[f"G{PRN:02d}"], loop_params=loop_params,
            output_capacity=10, ambiguity_resolutions=unresolved,
        )[f"G{PRN:02d}"]

    assert len(caught) == 1
    message = str(caught[0].message)
    assert "unresolved" in message
    assert "NOT" in message and "CL block" in message, message

    # Falls back to CM alone rather than guessing CL's block.
    names = signals[f"G{PRN:02d}"].code_set.names
    assert names[adapter.channel.policy.carrier_component] == "L2CM"
    assert adapter.channel.coherent_duration_ms == 1


def test_a_confident_search_does_not_warn():
    import warnings

    cl_block = 37
    samples, config = _l2c_dwell(cl_block=cl_block)
    acq, signals = _acquire_l2c(samples, config)
    resolutions = resolve_acquisition_ambiguities(
        GpsL2C, signals, {f"G{PRN:02d}": acq}, samples, config
    )
    assert resolutions[f"G{PRN:02d}"].resolved

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _track_l2c(signals, acq, resolutions, buffers=0)
    assert not caught, [str(c.message) for c in caught]


def test_resolving_cl_moves_the_carrier_loop_onto_the_pilot():
    """
    The payoff of the CL search, and the reason it was worth building.

    Without the block, CL cannot be generated at the right phase, so both loops run
    on CM with Costas wrapping.  With it, CL is a genuine dataless pilot: the
    carrier loop moves onto it and takes the full four-quadrant angle, and the
    delay discriminator combines two equal-power components.

    CM and CL are time-multiplexed on the same in-phase carrier, unlike L5's
    quadrature I/Q, so moving the carrier reference between them is
    phase-continuous and costs no re-pull.
    """
    samples, config = _l2c_dwell(cl_block=37)
    acq, signals = _acquire_l2c(samples, config)
    resolutions = resolve_acquisition_ambiguities(
        GpsL2C, signals, {f"G{PRN:02d}": acq}, samples, config
    )
    names = signals[f"G{PRN:02d}"].code_set.names

    fallback = _track_l2c(signals, acq, None, buffers=0).channel.policy
    assert names[fallback.carrier_component] == "L2CM"
    assert tuple(names[i] for i in fallback.code_components) == ("L2CM",)
    assert fallback.costas is True

    resolved = _track_l2c(signals, acq, resolutions, buffers=0).channel.policy
    assert names[resolved.carrier_component] == "L2CL"
    assert tuple(names[i] for i in resolved.code_components) == ("L2CM", "L2CL")
    assert resolved.costas is False


def test_the_pilot_discriminator_is_a_full_four_quadrant_angle():
    """
    `costas=False` is atan2 already: np.angle is four-quadrant, and wrapping at
    +/-1/2 cycle is the identity on its range.  Costas folds by half a cycle, which
    is the two-quadrant form -- a 180 degree data flip becomes invisible, at the
    cost of the squaring loss.
    """
    from utils.tracking_channel import _wrap_cycles

    for prompt in (1 + 0.2j, -1 + 0.2j, -0.2 - 1j, -1 - 0.01j):
        angle_cycles = np.angle(prompt) / (2 * np.pi)
        assert _wrap_cycles(angle_cycles, 0.5) == pytest.approx(angle_cycles)

    # A sign flip is invisible to Costas and fully visible to the pilot form.
    a = np.angle(1 + 0.2j) / (2 * np.pi)
    b = np.angle(-1 - 0.2j) / (2 * np.pi)
    assert _wrap_cycles(a, 0.25) == pytest.approx(_wrap_cycles(b, 0.25))
    assert _wrap_cycles(a, 0.5) != pytest.approx(_wrap_cycles(b, 0.5))
