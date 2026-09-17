"""
Tracking regression scenarios.

These are pure data: signal family, ground truth, and the seeding error a realistic
acquisition would hand to the tracking loops.  They must stay stable across refactors
so the golden baseline remains comparable.

Seeding errors are expressed in physical units (Hz, chips) rather than internal
state units, so they stay meaningful if the state representation changes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrackingScenario:
    name: str
    family: str  # "L1CA" | "L2C" | "L5" | "L1C"
    prn: int
    duration_ms: int
    samp_rate: float
    # ground truth
    doppler_hz: float
    code_phase_ms: float
    # acquisition seeding error handed to the loops
    doppler_error_hz: float
    code_error_chips: float
    # channel conditions
    noise_sigma: float
    nav_bits: bool
    rng_seed: int = 0
    buffer_duration_ms: int = 200


# Chosen to span the realistic acquisition seeding envelope:
#   L1 C/A acquisition at 4 ms coherent -> 250 Hz bins -> up to +/-125 Hz error
#   code phase to within one sample -> ~0.2 chip at 5 Msps
# plus clean noise-free cases that pin down exact correlator behaviour.
SCENARIOS: tuple[TrackingScenario, ...] = (
    TrackingScenario(
        name="l1ca_clean",
        family="L1CA",
        prn=1,
        duration_ms=2000,
        samp_rate=5e6,
        doppler_hz=1234.0,
        code_phase_ms=0.37,
        doppler_error_hz=0.0,
        code_error_chips=0.0,
        noise_sigma=0.0,
        nav_bits=False,
    ),
    TrackingScenario(
        name="l1ca_navbits_seeded",
        family="L1CA",
        prn=1,
        duration_ms=2000,
        samp_rate=5e6,
        doppler_hz=1234.0,
        code_phase_ms=0.37,
        doppler_error_hz=125.0,
        code_error_chips=0.25,
        noise_sigma=0.0,
        nav_bits=True,
    ),
    TrackingScenario(
        name="l1ca_noisy",
        family="L1CA",
        prn=7,
        duration_ms=2000,
        samp_rate=5e6,
        doppler_hz=-2100.0,
        code_phase_ms=0.81,
        doppler_error_hz=60.0,
        code_error_chips=0.5,
        noise_sigma=3.0,
        nav_bits=True,
        rng_seed=42,
    ),
    TrackingScenario(
        name="l2c_clean",
        family="L2C",
        prn=1,
        duration_ms=2000,
        samp_rate=5e6,
        doppler_hz=-870.0,
        code_phase_ms=0.61,
        doppler_error_hz=0.0,
        code_error_chips=0.0,
        noise_sigma=0.0,
        nav_bits=False,
    ),
    # CM's code period is 20 ms, so a real acquisition reports a code phase
    # anywhere in [0, 20) ms -- not the sub-millisecond value every other scenario
    # uses.  That gap hid a defect for a long time: the loop filter took the code
    # phase for the epoch's stream time, which fed the correlator a `dt_sec` wrong
    # by exactly that phase and turned the loop's own Doppler corrections into
    # apparent frequency errors `code_phase / corr_period` times larger.  Below
    # about 4 ms it converged anyway; above it, L2C diverged -- roughly 80% of the
    # code phases real data produces.
    TrackingScenario(
        name="l2c_late_code_phase",
        family="L2C",
        prn=1,
        duration_ms=2000,
        samp_rate=5e6,
        doppler_hz=-870.0,
        code_phase_ms=15.61,
        doppler_error_hz=100.0,
        code_error_chips=0.25,
        noise_sigma=3.0,
        nav_bits=True,
        rng_seed=2,
    ),
    TrackingScenario(
        name="l2c_navbits_noisy",
        family="L2C",
        prn=1,
        duration_ms=2000,
        samp_rate=5e6,
        doppler_hz=-870.0,
        code_phase_ms=0.61,
        doppler_error_hz=100.0,
        code_error_chips=0.25,
        noise_sigma=3.0,
        nav_bits=True,
        rng_seed=1,
    ),
    # L5 runs at 25 Msps (10.23 Mcps needs >=2 samples/chip), so these are kept
    # short relative to the 2 s L1 C/A runs.  Doppler seeding errors reflect the
    # half-bin acquisition search, whose worst case is 250 Hz -- the edge of a
    # 1 ms FLL's unambiguous range.
    #
    # 400 ms is chosen to span the overlay transition with room to spare: PLL
    # lock lands near 50 ms and Neuman-Hofman sync near 100 ms, leaving ~15
    # coherent 20 ms epochs for the baseline to cover.
    TrackingScenario(
        name="l5_clean",
        family="L5",
        prn=1,
        duration_ms=400,
        samp_rate=25e6,
        doppler_hz=1500.0,
        code_phase_ms=0.31,
        doppler_error_hz=0.0,
        code_error_chips=0.0,
        noise_sigma=0.0,
        nav_bits=False,
        buffer_duration_ms=50,
    ),
    TrackingScenario(
        name="l5_navbits_seeded",
        family="L5",
        prn=1,
        duration_ms=400,
        samp_rate=25e6,
        doppler_hz=1500.0,
        code_phase_ms=0.31,
        doppler_error_hz=250.0,
        code_error_chips=0.25,
        noise_sigma=0.0,
        nav_bits=True,
        buffer_duration_ms=50,
    ),
    TrackingScenario(
        name="l5_noisy",
        family="L5",
        prn=7,
        duration_ms=400,
        samp_rate=25e6,
        doppler_hz=-2400.0,
        code_phase_ms=0.72,
        doppler_error_hz=125.0,
        code_error_chips=0.5,
        noise_sigma=3.0,
        nav_bits=True,
        rng_seed=5,
        buffer_duration_ms=50,
    ),
    # GPS L1C.  25 Msps for the same reason L5 needs it, but a different one: not
    # the chip rate (1.023 Mcps) but the subcarrier -- BOC(6,1) puts twelve
    # sub-chips in a chip, so the sub-chip rate is 12.276 Mcps.
    TrackingScenario(
        name="l1c_clean",
        family="L1C",
        prn=1,
        duration_ms=400,
        samp_rate=25e6,
        doppler_hz=1500.0,
        code_phase_ms=3.27,  # mid-period: L1C's code period is 10 ms, not 1
        doppler_error_hz=0.0,
        code_error_chips=0.0,
        noise_sigma=0.0,
        nav_bits=False,
        buffer_duration_ms=50,
    ),
    TrackingScenario(
        name="l1c_navbits_seeded",
        family="L1C",
        prn=1,
        duration_ms=400,
        samp_rate=25e6,
        doppler_hz=1500.0,
        code_phase_ms=7.31,
        # Acquisition on a 10 ms replica gives 100 Hz bins, so half a bin is 50 Hz.
        doppler_error_hz=50.0,
        # A BOC delay discriminator is linear over a far narrower window than a
        # BPSK one, so the seeding error that matters here is smaller.
        code_error_chips=0.1,
        noise_sigma=0.0,
        nav_bits=True,
        buffer_duration_ms=50,
    ),
    TrackingScenario(
        name="l1c_noisy",
        family="L1C",
        prn=7,
        duration_ms=400,
        samp_rate=25e6,
        doppler_hz=-2400.0,
        code_phase_ms=0.72,
        doppler_error_hz=50.0,
        code_error_chips=0.1,
        noise_sigma=3.0,
        nav_bits=True,
        rng_seed=5,
        buffer_duration_ms=50,
    ),
    # Seeded onto a BOC side peak.  The composite |ACF| has stable secondary maxima
    # at +/-0.53 chip -- measured -- and a single-loop BOC discriminator locks onto
    # one and stays there, 158 m out, reporting a healthy prompt the whole time.
    # The double estimator cannot: its code loop rides the plain code triangle,
    # which has one peak.  This is the scenario the technique exists for, so it is
    # a golden rather than a one-off test.
    TrackingScenario(
        name="l1c_side_peak_seed",
        family="L1C",
        prn=1,
        # Long enough for the 0.5 Hz code loop to settle well clear of the +/-0.25
        # chip wrap: 0.15 chip of residual at 600 ms, 0.04 at 1500.  A golden
        # sitting near the boundary would swing wildly rather than drift if any
        # later change nudged the loop, which is not what a baseline is for.
        duration_ms=1500,
        samp_rate=25e6,
        doppler_hz=900.0,
        code_phase_ms=4.10,
        doppler_error_hz=0.0,
        code_error_chips=0.53,
        noise_sigma=0.0,
        nav_bits=True,
        buffer_duration_ms=50,
    ),
    # Long enough to reach overlay sync with the shipped 200-prompt window (2 s of
    # 10 ms prompts, after PLL lock), so the golden covers the discriminator switch
    # and the move to 10 ms epochs -- the part of the channel that only L1C's
    # 10 ms primary period exercises.
    TrackingScenario(
        name="l1c_overlay_synced",
        family="L1C",
        prn=1,
        duration_ms=2600,
        samp_rate=25e6,
        doppler_hz=800.0,
        code_phase_ms=2.15,
        doppler_error_hz=0.0,
        code_error_chips=0.0,
        noise_sigma=0.0,
        nav_bits=True,
        buffer_duration_ms=50,
    ),
)


def get_scenario(name: str) -> TrackingScenario:
    for scenario in SCENARIOS:
        if scenario.name == name:
            return scenario
    raise KeyError(f"unknown scenario: {name}")
