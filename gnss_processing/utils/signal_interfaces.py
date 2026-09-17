"""
GPS signal catalog: Link, Signal, and the acquisition/tracking policies that
configure how each signal is acquired and tracked.

Terminology (see TODO_SIGNALS.md): a **link** is the RF carrier (L1, L2, L5);
a **signal** is a particular modulation on a link, for one constellation (GPS
L1 C/A, GPS L2C, GPS L5). A signal's carrier frequency can differ from its
link's nominal frequency for a BOC sidelobe -- not the case for any signal
built here yet, but `Signal.carrier_freq_hz` is independent of `Link` for when
it is.

A `Signal` subclass -- `GpsL1CA`, `GpsL2C`, `GpsL5` -- IS the class-based
definition of one signal: instantiating it for a PRN builds that satellite's
spreading-code components (see `utils.code_components`) into a `CodeSet`.
Acquisition and tracking are configured separately, in `ACQUISITION_POLICIES`
and `TRACKING_POLICIES` below, keyed by each signal's `signal_type_id` --
acquisition typically locks onto a single component (L2C acquires on CM alone
at 511.5 kcps, even though tracking later uses CM and CL together), and
discriminator/loop policy is a tracking-strategy choice independent of what
the signal itself is built of. Neither belongs on the signal definition.

What acquisition *cannot* locate is policy too.  A signal whose components
have unequal periods is only pinned modulo the shorter one, and the leftover
is resolved after the dwell rather than during it -- see `AcquisitionPolicy.
ambiguous_component`, `build_ambiguity_search` and
`resolve_acquisition_ambiguities`.  The plan itself is built per PRN because
it correlates that satellite's long code, but which component is ambiguous is
a property of how the signal is acquired, so it lives with the policy.
"""

from __future__ import annotations

import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import ClassVar, Iterable

import numpy as np

import gnss_tools.signals.gps_l1c as gps_l1c
import gnss_tools.signals.gps_l1ca as gps_l1ca
import gnss_tools.signals.gps_l2c as gps_l2c
import gnss_tools.signals.gps_l5 as gps_l5

from . import ambiguity_resolution
from . import bpsk_acquisition
from . import sample_streaming
from . import secondary_code
from . import tracking_channel
from .code_components import (
    Branch,
    CodeComponent,
    CodeSet,
    Subcarrier,
    SubcarrierKind,
    build_code_set,
)


class LinkId(StrEnum):
    L1 = "L1"
    L2 = "L2"
    L5 = "L5"


@dataclass(frozen=True)
class Link:
    """One RF carrier, shared by every signal modulated onto it."""

    id: LinkId
    nominal_freq_hz: float


LINKS: dict[LinkId, Link] = {
    LinkId.L1: Link(LinkId.L1, gps_l1ca.CARRIER_FREQ),
    LinkId.L2: Link(LinkId.L2, gps_l2c.CARRIER_FREQ),
    LinkId.L5: Link(LinkId.L5, gps_l5.CARRIER_FREQ),
}


def _symbol_period_ms(signal_module) -> int:
    """Data symbol duration in ms: 20 for L1 C/A and L2 CNAV, 10 for L5 CNAV."""
    period_ms = 1e3 / signal_module.DATA_SYMBOL_RATE
    if period_ms != int(period_ms):
        raise ValueError(f"symbol period {period_ms} ms is not a whole millisecond")
    return int(period_ms)


def _interleave(sequences: list[np.ndarray]) -> list[np.ndarray]:
    """
    Place each sequence on its own chip of a shared clock, zero elsewhere.

    L2C's CM and CL alternate chip by chip on the 1.023 Mcps combined clock
    (IS-GPS-200), so CM comes back as (CM_0, 0, CM_1, 0, ...) and CL as
    (0, CL_0, 0, CL_1, ...).  Stating every component on the signal's own chip
    axis is what lets one correlator serve every signal -- see
    `utils.code_components`.
    """
    stride = len(sequences)
    filled = []
    for offset, sequence in enumerate(sequences):
        padded = np.zeros(len(sequence) * stride, dtype=np.int8)
        padded[offset::stride] = sequence
        filled.append(padded)
    return filled


def _tiered_code(primary: np.ndarray, overlay: np.ndarray) -> np.ndarray:
    """
    Fold a tiered code into one long sequence: the primary repeated once per
    overlay chip, each repetition signed by that chip.

    Putting the overlay *in the replica* is what lets acquisition integrate across
    it instead of being broken by it -- and the resulting code phase then carries
    the overlay phase for free, since the position within the long code says which
    overlay chip is in play.
    """
    return np.concatenate([primary * int(sign) for sign in overlay]).astype(np.int8)


class Signal(ABC):
    """
    A particular signal definition on a link, for one satellite.

    Carries only what the signal *is*: which link it rides on, its carrier
    frequency, and its spreading code components (built fresh per PRN, since
    only the code sequences vary by satellite). Acquisition and tracking
    policy live outside this class entirely -- see `ACQUISITION_POLICIES`/
    `TRACKING_POLICIES` below.
    """

    signal_type_id: ClassVar[str]
    """Unique id for this signal type (constellation + signal), e.g. "GPS_L1CA".
    Matches the convention in submodules/gnss-tools/gnss_tools/signals/catalog.py.
    This is what the policy dictionaries below key on -- NOT `signal_id`, which
    is the per-satellite id ("G01") assigned below."""

    link: ClassVar[Link]
    carrier_freq_hz: ClassVar[float]
    tracking_code_rate_chips_per_sec: ClassVar[float]
    # Duration of one full pass through the primary code. Tiered-code
    # integration accumulates in units of it.
    primary_period_ms: ClassVar[int] = 1

    def __init__(self, prn: int) -> None:
        self.prn = prn
        self.signal_id = f"G{prn:02d}"
        self.code_set: CodeSet = build_code_set(
            self._build_components(prn),
            chip_rate_hz=self.tracking_code_rate_chips_per_sec,
        )

    @staticmethod
    @abstractmethod
    def _build_components(prn: int) -> list[CodeComponent]:
        """This signal's spreading code components for one PRN."""

    @property
    def component_names(self) -> tuple[str, ...]:
        return self.code_set.names

    @property
    def overlay_period_ms(self) -> int:
        """Modulus of the shared tiered-code counter; 0 when there is no overlay."""
        overlays = [c.overlay for c in self.code_set.components if c.overlay is not None]
        if not overlays:
            return 0
        return int(np.lcm.reduce([len(o) for o in overlays])) * self.primary_period_ms


class GpsL1CA(Signal):
    """GPS L1 C/A: one BPSK component, 1.023 Mcps Gold code."""

    signal_type_id = "GPS_L1CA"
    link = LINKS[LinkId.L1]
    carrier_freq_hz = gps_l1ca.CARRIER_FREQ
    tracking_code_rate_chips_per_sec = gps_l1ca.CODE_RATE
    primary_period_ms = 1

    @staticmethod
    def _build_components(prn: int) -> list[CodeComponent]:
        code = (1 - 2 * gps_l1ca.get_GPS_L1CA_code_sequence(prn)).astype(np.int8)
        return [
            CodeComponent(
                name="L1C/A", sequence=code, branch=Branch.Q,
                symbol_period_ms=_symbol_period_ms(gps_l1ca),
            )
        ]


class GpsL2C(Signal):
    """
    GPS L2C: CM and CL, time-division multiplexed chip by chip on the
    1.023 Mcps combined clock -- CM on even chips, CL on odd, each zero-filled
    on the other's.  Both ride the quadrature branch.
    """

    signal_type_id = "GPS_L2C"
    link = LINKS[LinkId.L2]
    carrier_freq_hz = gps_l2c.CARRIER_FREQ
    tracking_code_rate_chips_per_sec = gps_l2c.CODE_RATE_L2CLM
    # CM repeats every 20 ms; CL every 1.5 s.
    primary_period_ms = 20

    @staticmethod
    def _build_components(prn: int) -> list[CodeComponent]:
        code_cm = (1 - 2 * gps_l2c.get_GPS_L2CM_code_sequence(prn)).astype(np.int8)
        code_cl = (1 - 2 * gps_l2c.get_GPS_L2CL_code_sequence(prn)).astype(np.int8)
        cm, cl = _interleave([code_cm, code_cl])
        return [
            CodeComponent(
                name="L2CM", sequence=cm, branch=Branch.Q,
                symbol_period_ms=_symbol_period_ms(gps_l2c),
            ),
            # CL is the dataless pilot: no symbol period, so nothing caps its
            # coherent integration but Doppler.  Same branch as CM -- both sit in
            # quadrature to P(Y) -- which is what makes the carrier handover at
            # ambiguity resolution phase-continuous.
            CodeComponent(name="L2CL", sequence=cl, branch=Branch.Q),
        ]


class GpsL5(Signal):
    """
    GPS L5: I and Q, co-located on every chip and separated by carrier
    phase (a QPSK pair), at 10.23 Mcps. Each carries a Neuman-Hoffman overlay
    -- NH10 on I, NH20 on Q -- that advances one chip per 1 ms primary code
    period; NH10's period is exactly the 10 ms CNAV symbol, so locking NH20
    (the pilot) also gives symbol sync on I for free (see
    `utils.secondary_code`).
    """

    signal_type_id = "GPS_L5"
    link = LINKS[LinkId.L5]
    carrier_freq_hz = gps_l5.CARRIER_FREQ
    tracking_code_rate_chips_per_sec = gps_l5.CODE_RATE
    # 10230 chips at 10.23 Mcps -- the aligned correlator's existing interval
    # granularity, which is what makes tiered-code wipe-off cheap.
    primary_period_ms = 1

    @staticmethod
    def _build_components(prn: int) -> list[CodeComponent]:
        # NOTE: unlike the L1CA/L2C getters, these return float64 0/1 rather than int8.
        code_i = (1 - 2 * gps_l5.get_GPS_L5I_code_sequence(prn)).astype(np.int8)
        code_q = (1 - 2 * gps_l5.get_GPS_L5Q_code_sequence(prn)).astype(np.int8)
        overlay_i = (1 - 2 * gps_l5.NEUMAN_HOFFMAN_SEQ_L5I).astype(np.int8)
        overlay_q = (1 - 2 * gps_l5.NEUMAN_HOFFMAN_SEQ_L5Q).astype(np.int8)
        return [
            CodeComponent(
                name="L5I", sequence=code_i, branch=Branch.I, overlay=overlay_i,
                symbol_period_ms=_symbol_period_ms(gps_l5),
            ),
            # Q is the dataless pilot: no symbol period.  Opposite branch to I,
            # so moving the carrier loop between them would cost a quarter-cycle
            # re-pull -- which is why it starts on Q and stays there.
            CodeComponent(name="L5Q", sequence=code_q, branch=Branch.Q, overlay=overlay_q),
        ]


try:
    class GpsL1C(Signal):
        """
        GPS L1C: a data component and a pilot, co-located on every chip at 1.023 Mcps
        and -- unlike L5's I/Q -- in the SAME carrier phase (IS-GPS-800J 3.2.1.6.1).
        They are separated by their codes and by a 25/75 power split, nothing else.

        Both carry a subcarrier, which is what makes L1C the first signal here needing
        more than the BPSK correlator: L1CD is BOC(1,1) throughout, and L1CP is
        TMBOC(6,1,4/33) -- BOC(6,1) on 4 of every 33 chips, BOC(1,1) on the rest.

        The pilot carries the 1800-bit L1CO overlay, one bit per 10 ms code period, so
        it repeats only every 18 s.  That is 90 times L5's NH20 and is why the overlay
        search is FFT-based here and runs on a window far shorter than one period.
        """

        signal_type_id = "GPS_L1C"
        link = LINKS[LinkId.L1]
        carrier_freq_hz = gps_l1c.CARRIER_FREQ
        tracking_code_rate_chips_per_sec = gps_l1c.CODE_RATE
        # 10230 chips at 1.023 Mcps.  The first signal here whose primary period is
        # longer than one correlation interval while also carrying an overlay -- see
        # `TrackingChannel._intervals_per_primary_period`.
        primary_period_ms = gps_l1c.PRIMARY_PERIOD_MS

        @staticmethod
        def _build_components(prn: int) -> list[CodeComponent]:
            code_d = (1 - 2 * gps_l1c.get_GPS_L1CD_code_sequence(prn)).astype(np.int8)
            code_p = (1 - 2 * gps_l1c.get_GPS_L1CP_code_sequence(prn)).astype(np.int8)
            overlay_p = (1 - 2 * gps_l1c.get_GPS_L1CO_overlay_sequence(prn)).astype(np.int8)
            return [
                CodeComponent(
                    name="L1CD", sequence=code_d, branch=Branch.I,
                    power_weight=gps_l1c.L1CD_POWER_FRACTION,
                    # One CNAV-2 symbol is exactly one code period, so a symbol flip
                    # never lands inside a code period -- and 10 ms is the ceiling on
                    # coherent integration for the whole signal, pilot included, since
                    # one epoch serves every component.
                    symbol_period_ms=_symbol_period_ms(gps_l1c),
                    subcarrier=Subcarrier(
                        kind=SubcarrierKind.BOC_SIN, rate_hz=gps_l1c.BOC_SUBCARRIER_RATE
                    ),
                ),
                # The dataless pilot, at three times the data component's power.  Same
                # branch as L1CD, so the carrier loop could be handed between them
                # phase-continuously; it starts on the pilot and stays there.
                CodeComponent(
                    name="L1CP", sequence=code_p, branch=Branch.I, overlay=overlay_p,
                    power_weight=gps_l1c.L1CP_POWER_FRACTION,
                    subcarrier=Subcarrier(
                        kind=SubcarrierKind.TMBOC,
                        rate_hz=gps_l1c.BOC_SUBCARRIER_RATE,
                        pattern=gps_l1c.TMBOC_PATTERN,
                        pattern_rate_hz=gps_l1c.TMBOC_SUBCARRIER_RATE,
                    ),
                ),
            ]
except AttributeError as _gps_l1c_error:
    # The checked-out gnss-tools submodule commit doesn't implement the L1C
    # constants/code generators this class needs yet (see gps_l1c.py's own
    # "INCOMPLETE" docstring). Degrade to skipping L1C rather than breaking
    # `import utils` for every signal.
    warnings.warn(
        "GPS L1C signal definition unavailable in the current gnss-tools "
        f"submodule checkout ({_gps_l1c_error}); skipping GpsL1C registration."
    )
    GpsL1C = None


SIGNALS: dict[str, type[Signal]] = {
    GpsL1CA.signal_type_id: GpsL1CA,
    GpsL2C.signal_type_id: GpsL2C,
    GpsL5.signal_type_id: GpsL5,
}
if GpsL1C is not None:
    SIGNALS[GpsL1C.signal_type_id] = GpsL1C


# --------------------------------------------------------------------------
# Acquisition and tracking policy -- kept separate from the signal
# definitions above (see TODO_SIGNALS.md and the module docstring).
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AcquisitionPolicy:
    """Which single component a signal is acquired on, and how."""

    component_name: str

    # Fold the component's tiered (overlay) code into the replica rather than
    # letting it break coherence.  That lengthens the replica by the overlay's
    # period, and the recovered code phase then carries the overlay phase for
    # free -- the position within the long code says which overlay chip is in
    # play.  See `acquisition_resolves_overlay_phase`.
    include_overlay: bool = False

    # A component whose period is longer than the acquisition code's, and which
    # acquisition therefore leaves unlocated: the dwell pins the signal only
    # modulo the code it correlated against.  `build_ambiguity_search` turns
    # this into a hypothesis search over the same dwell; None means acquisition
    # locates the whole signal (L1 C/A).
    ambiguous_component: str | None = None


ACQUISITION_POLICIES: dict[str, AcquisitionPolicy] = {
    "GPS_L1CA": AcquisitionPolicy(component_name="L1C/A"),
    # Acquisition locates CM's 20 ms period, which leaves CL -- 75 times longer
    # -- unlocated.  Until the search below runs, the channel generates CL from
    # whatever phase CM supplied, i.e. as though CL were at its code origin,
    # which is right 1 time in 75.
    "GPS_L2C": AcquisitionPolicy(component_name="L2CM", ambiguous_component="L2CL"),
    # Acquire on Q x NH20 -- the pilot, with its overlay folded into the
    # replica.  I x NH10 is half the length and was the obvious choice, but it
    # has a degeneracy: L5I's CNAV symbol period EQUALS NH10's, so the data
    # reduces to a sign on each whole composite period and a dwell whose blocks
    # straddle a symbol boundary can score a neighbouring NH10 index above the
    # true one (measured: 8.31 ms recovered for a true 7.31 ms).  Q carries no
    # data at all, so its composite is exact over the full 20 ms.
    #
    # It costs 4x the search grid -- twice the code phase cells and, because the
    # 20 ms replica halves the Doppler bin, twice the Doppler bins.  It buys:
    #
    #   * the shared overlay counter outright.  A 20 ms code phase pins the
    #     counter mod 20, and NH10's index is that mod 10, so BOTH overlays are
    #     resolved and no hypothesis search is needed at all.
    #   * ~1.5 dB, because no data flip breaks coherence inside a block
    #     (measured on real L5: G14 +16.9 -> +18.7, G01 +12.8 -> +14.5,
    #     G08 +1.9 -> +3.4 dB of margin over threshold).
    "GPS_L5": AcquisitionPolicy(component_name="L5Q", include_overlay=True),
    # Acquire on the pilot: three times the data component's power, and dataless
    # within its own 10 ms period.
    #
    # The overlay is NOT folded in, unlike L5's.  L5's NH20 makes a 20 ms replica;
    # L1CO would make an 18 SECOND one.  So acquisition pins the code phase modulo
    # 10 ms and leaves the overlay phase to the post-lock search -- which is what
    # `utils.secondary_code.fft_search` exists for.
    #
    # Nothing is left ambiguous in the `ambiguous_component` sense: both components
    # are 10230 chips, so locating one locates the other.
}
if GpsL1C is not None:
    ACQUISITION_POLICIES["GPS_L1C"] = AcquisitionPolicy(component_name="L1CP")


@dataclass(frozen=True)
class TrackingPolicy:
    """
    Which components drive the tracking loops, and how tiered-code sync and
    ambiguity resolution change that.

    `synced_discriminator_policy` applies once a tiered (overlay) code is
    synchronised and can be wiped off.  How LONG the accumulation then runs for
    is deliberately not stated here: it is the caller's `coherent_duration_ms`,
    clamped to what the signal can carry by `coherent_duration_target_ms`.  A
    per-signal constant used to live here and always equalled that clamp, so it
    could only ever disagree with the caller by ignoring them.

    `resolved_discriminator_policy` applies instead of `discriminator_policy`
    when the long-code ambiguity was resolved, i.e. when a component
    acquisition could not locate is now usable.  Distinct from `synced_*`,
    which is about stripping a tiered code: L2C's CL is not correlatable at all
    until its block is known, whereas L5's Q is always correlatable and only
    becomes *dataless* once NH is wiped off.
    """

    discriminator_policy: tracking_channel.LoopDiscriminatorPolicy
    synced_discriminator_policy: tracking_channel.LoopDiscriminatorPolicy | None = None
    resolved_discriminator_policy: tracking_channel.LoopDiscriminatorPolicy | None = None

    # How the overlay phase is searched for, when acquisition did not supply it.
    # Both defaults suit a short Neuman-Hofman overlay; a long one needs an FFT
    # search and a window stated in prompts rather than whole overlay periods --
    # see `utils.secondary_code`.  Strategy, not structure, so it lives here.
    overlay_search: secondary_code.OverlaySearch | None = None
    overlay_prompts_to_observe: int | None = None


TRACKING_POLICIES: dict[str, TrackingPolicy] = {
    "GPS_L1CA": TrackingPolicy(
        discriminator_policy=tracking_channel.LoopDiscriminatorPolicy(
            carrier_component=0, code_components=(0,), costas=True
        ),
    ),
    "GPS_L2C": TrackingPolicy(
        # Fallback, used when the CL block is unknown: CM alone, Costas on.  CL
        # cannot be generated at the right phase, so it would feed noise into
        # both loops.
        discriminator_policy=tracking_channel.LoopDiscriminatorPolicy(
            carrier_component=0, code_components=(0,), costas=True
        ),
        # Once the block is resolved, CL is a genuine dataless pilot: the
        # carrier loop moves to it and drops Costas wrapping for the full
        # four-quadrant angle, and the delay discriminator combines both
        # equal-power components non-coherently.  Unlike L5's I/Q, CM and CL are
        # time-multiplexed on one carrier branch (both quadrature to P(Y)), so
        # moving the carrier reference between them is phase-continuous and costs
        # no re-pull -- see `CodeSet.share_branch`.
        resolved_discriminator_policy=tracking_channel.LoopDiscriminatorPolicy(
            carrier_component=1, code_components=(0, 1), costas=False
        ),
    ),
    "GPS_L5": TrackingPolicy(
        # Carrier runs on Q, the pilot, from the start. I and Q are in
        # quadrature, so switching the carrier component mid-track (e.g. at
        # overlay sync) would force the PLL to re-pull; starting on Q avoids
        # that. Costas stays True until the overlay is stripped: NH20 flips
        # Q's sign every 1 ms, so the pilot is not yet effectively dataless.
        #
        # The delay discriminator combines I and Q non-coherently. They are
        # equal power, so this is worth ~3 dB and costs nothing: overlay and
        # data flips cancel in the magnitudes.
        discriminator_policy=tracking_channel.LoopDiscriminatorPolicy(
            carrier_component=1, code_components=(0, 1), costas=True
        ),
        # Once NH is stripped, Q is genuinely dataless: the phase
        # discriminator can use the full four-quadrant angle instead of
        # wrapping at +/-1/4 cycle, which is where the pilot's ~6 dB comes
        # from.
        #
        # The delay discriminator keeps both components.  Capping the epoch at
        # I's 10 ms CNAV symbol is what makes that possible: I now comes out at
        # full magnitude rather than half-cancelled across two symbols, so
        # combining two equal-power components non-coherently is worth ~3 dB.
        # Data flips do not matter here -- the DLL combines magnitudes.
        #
        # Only the *carrier* loop moves to the pilot alone, which is where the
        # dataless four-quadrant discriminator pays.
        synced_discriminator_policy=tracking_channel.LoopDiscriminatorPolicy(
            carrier_component=1, code_components=(0, 1), costas=False
        ),
        # The ceiling here is 10 ms, one CNAV symbol on I, and it is read off the
        # code set rather than written down: see `coherent_duration_limits_ms`.
        # NH20's 20 ms period is the tempting choice and buys another 3 dB on the
        # pilot, but one epoch serves every component: Q is dataless and would take
        # it, while I would span two CNAV symbols and cancel.  10 ms is exactly one
        # symbol on I, exactly half an NH20 period on Q -- and since epochs anchor
        # to multiples of their own length in code phase, they keep tiling NH20
        # evenly.  It also leaves I symbol-synchronous, which is what a CNAV
        # decoder wants.
    ),
}
if GpsL1C is not None:
    TRACKING_POLICIES["GPS_L1C"] = TrackingPolicy(
        # Carrier on L1CP, the pilot, from the start -- three times L1CD's power.
        # Unlike L5 there is no quadrature penalty for choosing either, since both
        # components are in the same carrier phase; the pilot is simply stronger
        # and eventually dataless.
        #
        # The delay discriminator combines both, weighted 75/25 by
        # `power_weight` -- the first signal here where that weighting is not
        # uniform, and the reason `_combine_code_magnitude` weights at all.
        #
        # Costas stays on until the overlay is stripped: L1CO flips L1CP's sign
        # every 10 ms, so the pilot is not yet effectively dataless.
        discriminator_policy=tracking_channel.LoopDiscriminatorPolicy(
            carrier_component=1, code_components=(0, 1), costas=True
        ),
        # Once L1CO is wiped off, L1CP really is dataless and the phase
        # discriminator can use the full four-quadrant angle.  That is the whole
        # prize here -- about 6 dB.
        synced_discriminator_policy=tracking_channel.LoopDiscriminatorPolicy(
            carrier_component=1, code_components=(0, 1), costas=False
        ),
        # The ceiling here is also 10 ms, and the reason is worth stating because
        # it differs from L5's: the binding limit is L1CD's CNAV-2 symbol, not the
        # overlay.  One epoch serves every component, so integrating past 10 ms
        # would leave the pilot happy and quietly cancel the data component.
        # Stripping L1CO therefore buys a better discriminator, not a longer
        # accumulation.
        # 1800 offsets is where brute force stops being sensible, and four whole
        # periods would be 72 seconds of tracking before the first attempt.  200
        # prompts is 2 s, and at 40 dB-Hz a 10 ms prompt already carries ~20 dB.
        overlay_search=secondary_code.fft_search,
        overlay_prompts_to_observe=200,
    )


# --------------------------------------------------------------------------
# What acquisition leaves ambiguous, and how it is resolved.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AmbiguitySearchPlan:
    """
    How to resolve what acquisition left ambiguous, for one signal.

    `code_set` is correlated as transmitted and only `scored_component` is read.
    `hypothesis_stride_chips` is the acquisition code's period on the signal's chip
    clock, so hypothesis h tests "the dwell is h acquisition periods into the long
    code".
    """

    code_set: CodeSet
    scored_component: int
    num_hypotheses: int
    hypothesis_stride_chips: float
    code_rate_chips_per_sec: float
    description: str


def _acquisition_component(signal_type: type[Signal], signal: Signal) -> CodeComponent:
    policy = ACQUISITION_POLICIES[signal_type.signal_type_id]
    return signal.code_set.components[signal.code_set.index_of(policy.component_name)]


def acquisition_code(signal_type: type[Signal], signal: Signal) -> np.ndarray:
    """The replica sequence acquisition correlates against, on its own chip clock."""
    policy = ACQUISITION_POLICIES[signal_type.signal_type_id]
    component = _acquisition_component(signal_type, signal)

    sequence = component.sequence
    if not policy.include_overlay:
        return sequence
    if component.overlay is None:
        raise ValueError(
            f"{signal_type.signal_type_id}: acquisition policy asks to fold in an "
            f"overlay, but component {policy.component_name!r} has none"
        )
    return _tiered_code(sequence, component.overlay)


def acquisition_code_period_ms(signal_type: type[Signal], signal: Signal) -> float:
    rate = signal_type.tracking_code_rate_chips_per_sec
    return len(acquisition_code(signal_type, signal)) / rate * 1e3


def acquisition_resolves_overlay_phase(
    signal_type: type[Signal], signal: Signal
) -> bool:
    """
    True when acquisition correlates a code spanning a whole overlay period, so
    the recovered code phase already carries the overlay index and no separate
    search is needed.  L5 acquires on Q x NH20, whose period is the counter's
    modulus, so it pins both NH20 and -- since the counters share a clock --
    NH10 as well.
    """
    period = signal.overlay_period_ms
    return period > 0 and acquisition_code_period_ms(signal_type, signal) >= period


def build_ambiguity_search(
    signal_type: type[Signal], signal: Signal
) -> AmbiguitySearchPlan | None:
    """
    The long-code search implied by `AcquisitionPolicy.ambiguous_component`, or
    None when acquisition locates the whole signal.

    The acquisition component plays no part in the search: it gave the code
    phase and Doppler, and the hypothesis stride is a whole acquisition period,
    so it would score identically under every hypothesis.  The search therefore
    correlates the ambiguous component alone, still on its own chips of the
    combined clock -- a partial view of the signal, which is what
    `allow_partial_coverage` is for.
    """
    policy = ACQUISITION_POLICIES[signal_type.signal_type_id]
    if policy.ambiguous_component is None:
        return None

    code_set = signal.code_set
    acquired = code_set.components[code_set.index_of(policy.component_name)]
    ambiguous = code_set.components[code_set.index_of(policy.ambiguous_component)]

    stride_chips = acquired.code_length
    num_hypotheses, remainder = divmod(ambiguous.code_length, stride_chips)
    if remainder or num_hypotheses < 2:
        raise ValueError(
            f"{signal_type.signal_type_id}: {policy.ambiguous_component!r} spans "
            f"{ambiguous.code_length} chips, which is not a whole multiple "
            f"(>1) of {policy.component_name!r}'s {stride_chips}"
        )

    rate = signal_type.tracking_code_rate_chips_per_sec
    period_sec = ambiguous.code_length / rate
    return AmbiguitySearchPlan(
        code_set=build_code_set([ambiguous], allow_partial_coverage=True),
        scored_component=0,
        num_hypotheses=int(num_hypotheses),
        hypothesis_stride_chips=float(stride_chips),
        code_rate_chips_per_sec=rate,
        description=f"{ambiguous.name} block within the {period_sec:g} s pilot code",
    )


# --------------------------------------------------------------------------
# Runtime: tracking channel adapter, and the module-level helpers that tie a
# signal type's components together with its (separately defined) policies.
# --------------------------------------------------------------------------


@dataclass
class TrackingChannelAdapter:
    """Pairs a tracking channel with the signal that configured it."""

    signal: Signal
    channel: tracking_channel.TrackingChannel

    @property
    def outputs(self) -> tracking_channel.SignalTrackingOutputs:
        return self.channel.outputs

    def process_sample_buffer(self, sample_buffer: sample_streaming.SampleBuffer) -> None:
        self.channel.process_sample_buffer(sample_buffer)

    def set_mode_pll(self) -> None:
        self.channel.loop_state.mode = tracking_channel.TrackingLoopMode.PLL

    def component_index(self, name: str) -> int:
        """Index of a named component, e.g. "L2CM"/"L2CL" for L2C."""
        return self.signal.code_set.index_of(name)

    def get_prompt_component(self, component: int = 0) -> np.ndarray:
        return self.outputs.prompt_corr[self.outputs.valid, component]

    def get_early_component(self, component: int = 0) -> np.ndarray:
        return self.outputs.early_corr[self.outputs.valid, component]

    def get_late_component(self, component: int = 0) -> np.ndarray:
        return self.outputs.late_corr[self.outputs.valid, component]


def build_signals(signal_type: type[Signal], prns: Iterable[int] = range(1, 33)) -> dict[str, Signal]:
    return {f"G{prn:02d}": signal_type(prn) for prn in prns}


def build_acquisition_code_params(
    signal_type: type[Signal],
    signals: dict[str, Signal],
) -> dict[str, bpsk_acquisition.AcqSignalCodeParameters]:
    params: dict[str, bpsk_acquisition.AcqSignalCodeParameters] = {}
    for signal_id, signal in signals.items():
        # Already zero-filled on any sibling's chips, so it cannot correlate
        # against that sibling's code -- nothing for acquisition to special-case.
        sequence = acquisition_code(signal_type, signal)
        params[signal_id] = bpsk_acquisition.AcqSignalCodeParameters(
            rate_chips_per_sec=signal_type.tracking_code_rate_chips_per_sec,
            length_chips=len(sequence),
            sequence=sequence,
            carrier_freq_hz=signal_type.carrier_freq_hz,
            # Acquisition evaluates it from the fractional chip position, exactly
            # as the correlator does, so the replica and the tracking channel see
            # one subcarrier on one chip axis.
            subcarrier=_acquisition_component(signal_type, signal).subcarrier,
        )
    return params


def resolve_acquisition_ambiguities(
    signal_type: type[Signal],
    signals: dict[str, Signal],
    acquisition_results: dict[str, bpsk_acquisition.AcquisitionResult],
    sample_block: np.ndarray,
    acq_config: bpsk_acquisition.AcquisitionConfiguration,
    signal_ids: Iterable[str] | None = None,
) -> dict[str, ambiguity_resolution.AmbiguityResolution]:
    """
    Run each signal's long-code search over the acquisition dwell.

    The same samples acquisition used are reused, with the same coherent block
    structure, so the hypothesis scores carry exactly the Doppler loss the
    acquisition already survived and no refinement step is needed in between.

    Signals with no ambiguous component (L1 C/A, L5) are skipped, as are ones
    that did not acquire.
    """
    if signal_ids is None:
        signal_ids = [
            sid for sid, result in acquisition_results.items() if result.signal_detected
        ]

    resolutions: dict[str, ambiguity_resolution.AmbiguityResolution] = {}
    for signal_id in signal_ids:
        signal = signals[signal_id]
        plan = build_ambiguity_search(signal_type, signal)
        if plan is None:
            continue
        acq_result = acquisition_results[signal_id]
        resolutions[signal_id] = ambiguity_resolution.resolve_code_ambiguity(
            sample_block,
            acq_config.sample_rate,
            code_set=plan.code_set,
            scored_component=plan.scored_component,
            acquired_code_phase_sec=acq_result.acq_code_phase_seconds,
            doppler_hz=acq_result.acq_doppler_hz,
            carrier_freq_hz=signal_type.carrier_freq_hz,
            nominal_code_rate_chips_per_sec=plan.code_rate_chips_per_sec,
            coherent_length_samples=acq_config.coherent_length_samples,
            num_blocks=acq_config.num_blocks,
            num_hypotheses=plan.num_hypotheses,
            hypothesis_stride_chips=plan.hypothesis_stride_chips,
        )
    return resolutions


def requires_bit_sync(signal_type: type[Signal], signal: Signal) -> bool:
    """
    Whether the data symbol boundary has to be found statistically.

    True for GPS L1 C/A alone, and for the reason `utils.nav.bitsync` describes:
    every other signal here hands the boundary over for free, either through an
    overlay whose phase is synchronised or through a primary code period that is
    itself one symbol long.  L1 C/A has a 1 ms code under a 20 ms bit and nothing
    tying the two together, so the twenty candidate boundaries are
    indistinguishable until the data itself picks one out.
    """
    if signal.overlay_period_ms:
        return False
    symbol_periods = [
        c.symbol_period_ms
        for c in signal.code_set.components
        if c.symbol_period_ms is not None
    ]
    return bool(symbol_periods) and min(symbol_periods) > signal_type.primary_period_ms


def tracking_signal_params(
    signal_type: type[Signal], signal: Signal
) -> tracking_channel.TrackingSignalParameters:
    """The signal's structure as the tracking channel wants it."""
    return tracking_channel.TrackingSignalParameters(
        code_set=signal.code_set,
        nominal_code_rate_chips_per_sec=signal_type.tracking_code_rate_chips_per_sec,
        carrier_freq_hz=signal_type.carrier_freq_hz,
        primary_period_ms=signal_type.primary_period_ms,
    )


def coherent_duration_target_ms(
    signal_type: type[Signal],
    signal: Signal,
    requested_ms: int,
    warn: bool = True,
) -> int:
    """
    How long the coherent accumulation runs once the channel is synchronised.

    The caller's `coherent_duration_ms` is a CAP, not a target: this returns it
    unchanged when the signal can carry it and cuts it down when it cannot.  Two
    ways it gets cut, and a warning either way, because the loop the caller ends
    up with is not the one they configured:

      too long      past the shortest data symbol the signal carries -- 10 ms on
                    L5 and L1C, 20 ms on L1 C/A and L2C.  Beyond that the epoch
                    spans a symbol boundary and the data components cancel.
      not a divisor rounded DOWN to one.  Epochs anchor to multiples of their
                    own length, so 3 ms against L5's 10 ms symbol would put every
                    third epoch across a boundary; 2 ms tiles it exactly.

    The overlay is taken as stripped, which is what makes this the SYNCED length
    rather than the starting one.  Every signal here starts at one correlation
    interval regardless (see `create_tracking_channels`), so the un-stripped
    bound never binds anything.
    """
    signal_params = tracking_signal_params(signal_type, signal)
    target_ms = tracking_channel.clamp_coherent_duration_ms(
        signal_params, requested_ms, overlay_stripped=True
    )
    if warn and target_ms != requested_ms:
        limits = tracking_channel.coherent_duration_limits_ms(
            signal_params, overlay_stripped=True
        )
        limit_ms, reason = min(limits)
        warnings.warn(
            f"{signal_type.signal_type_id}: coherent_duration_ms {requested_ms} is not "
            f"usable on this signal, which is bounded by {reason} at {limit_ms} ms and "
            f"can only use lengths that divide it; coherent integration is capped at "
            f"{target_ms} ms.",
            RuntimeWarning,
            stacklevel=3,
        )
    return target_ms


def create_tracking_channels(
    signal_type: type[Signal],
    signals: dict[str, Signal],
    acquisition_results: dict[str, bpsk_acquisition.AcquisitionResult],
    tracking_signal_ids: Iterable[str],
    loop_params: tracking_channel.TrackingLoopParameters,
    output_capacity: int,
    start_mode_pll: bool = False,
    ambiguity_resolutions: dict[str, ambiguity_resolution.AmbiguityResolution]
    | None = None,
    cn0_params: tracking_channel.CN0EstimatorParameters | None = None,
) -> dict[str, TrackingChannelAdapter]:
    tracking_policy = TRACKING_POLICIES[signal_type.signal_type_id]

    # EVERY signal starts at one correlation interval and extends afterwards.  The
    # requested duration is therefore the duration to extend TO, never the one the
    # channel opens with.  Two independent reasons, and it is worth separating them
    # because only the first is about knowing where a symbol begins:
    #
    #   Doppler.  Acquisition leaves a residual of up to half a search bin, and a
    #     coherent accumulation of length T loses sinc(df*T) to it.  Worse, the FLL
    #     discriminator wraps at `half_range / T` -- +/-250 Hz at 1 ms with Costas,
    #     +/-25 Hz at 10 ms -- so a channel opened long can start with its residual
    #     OUTSIDE the range of the loop meant to remove it, and the FLL then pulls
    #     the wrong way.  One millisecond is where the pull-in range is widest and
    #     the Doppler loss is negligible, which is exactly what a loop that has not
    #     converged yet needs.  This applies to every signal.
    #   Boundary.  A signal whose symbol boundary is not known yet ALSO cannot open
    #     a multi-interval grid: there is nothing to anchor it to, and anchoring on
    #     the code phase lattice instead misplaces every epoch by the offset between
    #     the two (see `TrackingChannel._observe_bit_sync`).  One interval is the
    #     only length at which that offset is measurable at all.  L1 C/A alone.
    #
    # The extension back to the requested length is gated on PLL lock and on a real
    # symbol boundary, in `TrackingChannel._maybe_extend_coherent_duration`.
    interval_ms = tracking_channel.CORRELATION_INTERVAL_MS
    # Any PRN answers this -- overlay presence and symbol periods are properties of
    # the signal, not of the satellite -- and `tracking_signal_ids` is only an
    # Iterable, so peeking into it here would consume a caller's generator.
    probe = next(iter(signals.values()), None)
    synced_coherent_duration_ms = loop_params.coherent_duration_ms
    if probe is not None:
        # Warned once per signal type, not once per satellite: the bound comes from
        # the code set, so every PRN would report the same cap.
        synced_coherent_duration_ms = coherent_duration_target_ms(
            signal_type, probe, loop_params.coherent_duration_ms
        )
    start_loop_params = replace(loop_params, coherent_duration_ms=interval_ms)
    # Capacity is one row per epoch, and the epochs before the extension are the
    # short ones.  A channel that never reaches PLL -- or whose bit sync never
    # converges -- stays short for the whole run, so the honest bound is the run
    # length in intervals; anything less truncates the record silently, exactly
    # where a reader would be looking to find out why it never extended.
    output_capacity = output_capacity * (synced_coherent_duration_ms // interval_ms)

    channels: dict[str, TrackingChannelAdapter] = {}
    for signal_id in tracking_signal_ids:
        signal = signals[signal_id]
        acq_result = acquisition_results[signal_id]

        # The resolved offset is simply part of the code phase: it says how far into
        # the long code the signal actually is, which acquisition could only pin down
        # modulo the code it correlated against.  L2C needs it or CL is generated as
        # though at its code origin, right 1 time in 75.
        #
        # An unresolved (low-confidence) search is ignored rather than trusted,
        # leaving the pre-existing behaviour.
        code_phase_offset_ms = 0.0
        resolution_applied = False
        resolution = (ambiguity_resolutions or {}).get(signal_id)
        plan = build_ambiguity_search(signal_type, signal)
        if resolution is not None and resolution.resolved and plan is not None:
            resolution_applied = True
            code_phase_offset_ms = (
                resolution.offset_chips / plan.code_rate_chips_per_sec * 1e3
            )
        elif resolution is not None and plan is not None:
            # The search ran and could not decide.  Guessing would be worse than
            # waiting: a mis-phased wipe-off never locks at all once the pilot
            # discriminator drops Costas wrapping.  Say so, because the visible
            # consequence is that the configured integration does not take effect.
            warnings.warn(
                f"{signal_id}: {plan.description} unresolved "
                f"(confidence {resolution.confidence:.2f} < threshold); tracking starts at "
                f"{interval_ms} ms and the configured {synced_coherent_duration_ms} ms "
                f"coherent integration is NOT active yet. The channel falls back to "
                f"searching for the overlay phase after PLL lock, and will extend only if "
                f"that succeeds.",
                RuntimeWarning,
                stacklevel=2,
            )

        code_rate_ms_per_sec = (
            1.0 + acq_result.acq_doppler_hz / signal_type.carrier_freq_hz
        ) * 1e3
        initial_state = tracking_channel.TrackingSignalState(
            uptime_epoch_ms=acq_result.uptime_epoch_ms,
            code_phase_ms=acq_result.acq_code_phase_seconds * 1e3 + code_phase_offset_ms,
            code_rate_ms_per_sec=code_rate_ms_per_sec,
            carrier_phase_cycles=0.0,
            carrier_rate_cyc_per_sec=acq_result.acq_doppler_hz,
        )
        signal_params = tracking_signal_params(signal_type, signal)
        # A signal with a tiered code can start already synced: the acquisition
        # code phase is in the overlay's frame, so the overlay index of the first
        # correlation interval is just its integer millisecond.  That skips the
        # post-lock search entirely -- wipe-off and the pilot discriminator are
        # live from the first interval, while the coherent accumulation still
        # waits for PLL lock (see TrackingChannel).
        initial_overlay_counter = None
        if acquisition_resolves_overlay_phase(signal_type, signal):
            # The first correlation interval starts at ceil(code phase); the
            # overlay counter counts PRIMARY CODE PERIODS, so that millisecond is
            # divided by the period rather than used directly.  The two are the
            # same number on L5 and only on L5.
            primary_period_ms = signal_type.primary_period_ms
            initial_overlay_counter = int(
                np.ceil(initial_state.code_phase_ms) // primary_period_ms
            ) % (signal.overlay_period_ms // primary_period_ms)

        # A resolved ambiguity makes a component usable that was not before, so it
        # can change which components drive the loops -- for L2C that is the whole
        # point, moving the carrier onto the CL pilot.
        discriminator_policy = tracking_policy.discriminator_policy
        if resolution_applied and tracking_policy.resolved_discriminator_policy is not None:
            discriminator_policy = tracking_policy.resolved_discriminator_policy

        channel = tracking_channel.TrackingChannel(
            loop_params=start_loop_params,
            signal_params=signal_params,
            initial_signal_state=initial_state,
            output_capacity=output_capacity,
            discriminator_policy=discriminator_policy,
            synced_policy=tracking_policy.synced_discriminator_policy,
            synced_coherent_duration_ms=synced_coherent_duration_ms,
            cn0_params=cn0_params,
            initial_overlay_counter=initial_overlay_counter,
            overlay_search=tracking_policy.overlay_search,
            overlay_prompts_to_observe=tracking_policy.overlay_prompts_to_observe,
        )

        adapter = TrackingChannelAdapter(signal=signal, channel=channel)
        if start_mode_pll:
            adapter.set_mode_pll()
        channels[signal_id] = adapter
    return channels
