"""
Detect loss of lock in a running TrackingChannel and recover it with a narrow,
Doppler/code-phase-aided re-acquisition, instead of leaving the channel to
either track garbage or drop out for good once a USRP overflow drops samples
out of the middle of a recording.

WHY THIS IS NEEDED
-------------------
`rx_samples_to_file`-style overflows do not write a gap marker: the dropped
samples are simply never written, so the file is seamlessly shorter than the
antenna time it represents (see the capture logs -- a bare "O" per overflow,
no timestamp, no sample count).  Every tracking-loop prediction in this
codebase (`TrackingSignalState.propagate_phase`, `AlignedCorrelator.accumulate`)
assumes sample index maps linearly onto elapsed time, so at the point where
real samples went missing, the receiver's actual code phase and Doppler at
that moment in the file are no longer what the loop predicts -- the recorded
signal has effectively jumped underneath it.  The loop cannot chase an
arbitrarily large, instantaneous jump; it loses lock.

WHY THE RE-SEARCH CAN STAY NARROW
-----------------------------------
Nothing about the receiver's clock or the satellite geometry changed --  only
the recording did.  So the Doppler and code phase the channel had right
before it lost lock are still an excellent prediction of where to find the
signal again: this is a ground-based, effectively stationary or slow-moving
collect, so Doppler drifts at most a few Hz/s and the code phase is a linear,
predictable function of time.  A search of a few hundred Hz around the last
known-good Doppler -- rather than the +/-5000 Hz blind sweep acquisition used
the first time -- is both cheaper and (via the Sidak false-alarm correction in
`bpsk_acquisition.run_acquisition`, which scales with the number of Doppler
bins searched) *more* sensitive than the original acquisition, at no extra
risk of a false lock.

WHAT THIS MODULE DOES NOT DO
-------------------------------
It does not modify `TrackingChannel`, `AlignedCorrelator`, or the loop
filters at all.  A lost channel is simply frozen (stops being fed samples,
so it stops writing garbage epochs) and, once re-acquired, a brand new
`TrackingChannel` is built the same way `signal_interfaces.create_tracking_channels`
already builds the first one -- reusing that tested code path rather than
hand-seeding loop/overlay/bit-sync state, which is exactly the internal
machinery this module has no business re-implementing.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np

from . import bpsk_acquisition, signal_interfaces, tracking_channel


@dataclass
class ReacquisitionConfig:
    """Tuning for lock-loss detection and the narrow re-search that follows."""

    # A channel is declared LOST once its C/N0 estimate has read below this
    # floor for `bad_streak_to_declare_lost` consecutive estimates in a row.
    # At the default CN0EstimatorParameters (1000 ms period, 50% overlap -> a
    # new estimate every 500 ms), 3 in a row is ~1.5 s of sustained bad C/N0 --
    # long enough that an ordinary fade or a brief multipath null does not
    # trip it, short enough that a channel is not left tracking noise for long.
    cn0_floor_dbhz: float = 25.0
    bad_streak_to_declare_lost: int = 3

    # Doppler search half-width around the last known-good Doppler, in Hz.
    # Widens by `doppler_margin_growth_hz` on every failed retry, capped at
    # `doppler_margin_max_hz` -- a channel that has been down longer may have
    # drifted further from its last known Doppler, so later attempts get a
    # fair second look without ever falling back to acquisition's original
    # full-range blind search.
    doppler_margin_hz: float = 250.0
    doppler_margin_growth_hz: float = 250.0
    doppler_margin_max_hz: float = 2000.0

    # Code phase CANNOT be tightly predicted across a real gap -- it advances
    # at ~1 ms per ms of real time, and the whole reason a channel is
    # SEARCHING is that real time ran ahead of recorded-sample time by an
    # unknown amount. So this is a WARN-only threshold, not an accept/reject
    # gate: a re-detection whose implied gap (predicted vs. actual code
    # phase, unwrapped) exceeds this many ms is still accepted, only flagged
    # as unusually large for one overflow. The actual protection against a
    # false lock is `run_acquisition`'s own false-alarm-controlled detection
    # threshold, applied to a narrow Doppler search of this one PRN's own
    # code -- there is no other satellite's code in the search to be
    # confused with, so code-phase proximity adds nothing there.
    implausible_gap_warn_ms: float = 2000.0

    # How often (in stream time) to retry while a channel is down.
    retry_interval_ms: float = 500.0

    # Give up after a channel has been SEARCHING, continuously, for this long
    # in stream time -- a satellite that set below the horizon, or a PRN that
    # simply never comes back, would otherwise retry every `retry_interval_ms`
    # for the REST of the run. That is not free: one narrow re-acquisition
    # attempt is a real FFT-based search, and on L5 specifically -- whose
    # acquisition code has an inherent 20 ms period, so its FFT replica is
    # 500,000 samples at 25 Msps versus L1 C/A's 25,000 -- one attempt costs
    # on the order of several SECONDS of wall clock, not milliseconds. A
    # channel stuck for the back half of a 120 s run can otherwise cost more
    # wall-clock time in failed reacquisitions than the entire rest of the
    # notebook. `None` disables the cap (retry forever, the old behaviour).
    give_up_after_ms: float | None = 30_000.0

    # A floor on the search width, in whole FFT Doppler bins, independent of
    # `doppler_margin_hz`. The FFT bin width is `1000 / coherent_duration_replica_ms`
    # Hz and is set by the signal's acquisition replica length, not by this
    # config -- L1 C/A's 1 ms replica gives 1000 Hz/bin, L5's 20 ms one gives
    # 50 Hz/bin. A `doppler_margin_hz` tuned for a fine-grid signal can collapse
    # to ZERO whole bins on a coarse one once truncated to integer bin indices
    # (`bpsk_acquisition.run_acquisition` then has an empty Doppler grid to
    # search and crashes trying to find its peak), so the actual search width
    # passed to acquisition is always widened to cover at least this many bins.
    min_doppler_search_bins: int = 5

    # Refines the coarse, C/N0-cadence truncation boundary using the per-epoch
    # `prompt_corr_circ_length` the channel already records every epoch --
    # far finer-grained than a C/N0 estimate (every epoch, 5 ms once locked,
    # vs. every ~500 ms at the default CN0EstimatorParameters). C/N0 stays the
    # TRIGGER, not this: circular length is a much noisier, faster-reacting
    # statistic that dips as a matter of course after every ordinary
    # FLL -> PLL handover (the loop hands over holding a residual phase error
    # and needs ~history_size epochs to settle -- see
    # `utils.plotting.plot_prompt_circ_length`'s docstring) and would trigger
    # far too eagerly if used as the trigger itself, not merely to sharpen a
    # boundary a slower, more robust signal already confirmed.
    circ_length_floor: float = 0.8

    # Passed straight through to bpsk_acquisition.run_acquisition for the
    # narrow search.  Left equal to the original acquisition's setting by
    # default; it is automatically MORE sensitive per cell than the original
    # full sweep, because the Sidak correction divides prob_false_alarm_total
    # by the cell count and the narrow Doppler search has far fewer cells.
    prob_false_alarm_total: float = 1e-5


@dataclass
class ChannelSupervisor:
    """
    Owns one PRN's tracking history across however many lock/re-acquire
    cycles it goes through.  `adapter` is always the CURRENTLY ACTIVE
    channel while `status == "TRACKING"`, and is frozen (no longer fed
    samples) while `status == "SEARCHING"`.  `segments` keeps every adapter
    this PRN has had, in order, so nothing about an earlier stretch of
    tracking is lost when a new segment starts -- notebook 01's plotting
    cell reads this to draw each segment as its own line, with a visible
    break at every gap instead of one figure quietly wrong at every overflow.
    """

    signal_id: str
    adapter: signal_interfaces.TrackingChannelAdapter
    config: ReacquisitionConfig
    status: str = "TRACKING"  # "TRACKING" | "SEARCHING"

    # Last known-good state, the anchor every re-acquisition search is
    # centered on.
    last_good_uptime_ms: float = 0.0
    last_good_doppler_hz: float = 0.0
    last_good_code_phase_ms: float = 0.0
    # `outputs.output_index` as of the last GOOD C/N0 estimate -- see
    # `check_lock`'s truncation, which rolls the frozen segment's recorded
    # epochs back to here the moment it is declared lost.
    last_good_output_index: int = 0

    next_cn0_check_index: int = 0
    bad_streak: int = 0
    next_retry_uptime_ms: float = 0.0
    attempts_since_lost: int = 0
    lost_events: int = 0
    segments: list = field(default_factory=list)
    # Stream-time uptime at the moment this SEARCHING episode began -- the
    # clock `give_up_after_ms` counts against. Reset on every new loss, not
    # just the first.
    lost_at_uptime_ms: float = 0.0
    # Implied gap length (see `try_reacquire`) from the most recent successful
    # re-acquisition -- the closest thing this module has to a direct estimate
    # of how much real time one overflow actually dropped.
    last_implied_gap_ms: float = 0.0
    implied_gap_ms_by_segment: list = field(default_factory=list)

    def __post_init__(self) -> None:
        self.segments.append(self.adapter)
        self._refresh_last_good()

    def _refresh_last_good(self) -> None:
        outputs = self.adapter.outputs
        self.last_good_output_index = outputs.output_index
        if outputs.output_index > 0:
            idx = outputs.output_index - 1
            self.last_good_uptime_ms = float(outputs.uptime_epoch_ms[idx])
            self.last_good_doppler_hz = float(outputs.doppler_freq_hz[idx])
            self.last_good_code_phase_ms = float(outputs.code_phase_ms[idx])
        else:
            # Freshly built, before its first epoch: fall back to the state it
            # was seeded with.
            state = self.adapter.channel.signal_state
            self.last_good_uptime_ms = state.uptime_epoch_ms
            self.last_good_doppler_hz = state.carrier_rate_cyc_per_sec
            self.last_good_code_phase_ms = state.code_phase_ms

    def _refine_truncation_index(self, outputs) -> int:
        """
        Narrow the coarse, C/N0-cadence truncation boundary using the
        per-epoch `prompt_corr_circ_length` the channel already records --
        see `ReacquisitionConfig.circ_length_floor` for why circular length
        sharpens this boundary but does not trigger it in the first place.

        Searches the WHOLE segment recorded so far, not merely from
        `last_good_output_index` onward: that coarse boundary is only as
        precise as the C/N0 estimate that set it, and a C/N0 estimate is
        itself a windowed average, so it can read "good enough" from a
        window that already straddles the true jump -- i.e. the coarse
        boundary can already sit PAST where coherence actually broke.
        Bounding the search to start there would inherit that same lag
        instead of removing it. Searching from the start costs nothing
        prohibitive (at most a few tens of thousands of epochs) and finds the
        true edge wherever it actually is.

        Returns the index of the last epoch whose own circular length still
        cleared `circ_length_floor`, plus one -- or `outputs.valid.start`
        (discarding the whole segment) if nothing in it ever qualified, which
        should not happen to a segment that was ever truly "TRACKING".
        """
        lo, hi = outputs.valid.start, outputs.output_index
        if hi <= lo:
            return lo
        window = outputs.prompt_corr_circ_length[lo:hi]
        good = np.nonzero(window >= self.config.circ_length_floor)[0]
        if not len(good):
            return lo
        return lo + int(good[-1]) + 1

    def check_lock(self) -> bool:
        """
        Consume any new C/N0 estimates written since the last call.

        Returns True the moment this call is the one that pushes the bad
        streak over threshold -- i.e. "this channel just went from TRACKING
        to SEARCHING" -- so the caller can log it once, not on every buffer
        afterwards. Only meaningful while `status == "TRACKING"`.

        Declaring a channel lost TRUNCATES its recorded output, via
        `_refine_truncation_index`. This matters because C/N0 detection has an
        inherent lag -- `bad_streak_to_declare_lost` consecutive bad estimates,
        each covering roughly a second -- so by the time a channel is actually
        declared lost, it has spent the last second or two of "TRACKING"
        writing epochs from AFTER the real jump the recording took. Those
        epochs still carry `pll_mode=True` and `overlay_synced=True` (neither
        ever reverts within one segment, by design -- see
        `utils.nav.symbols._usable_regions`), so nothing downstream can tell
        them apart from genuinely locked data without this: left untruncated,
        every segment would end in a second or two of silently corrupted
        symbols, right where `utils.nav.symbols.extract` would otherwise have
        found a clean region boundary.
        """
        outputs = self.adapter.outputs
        newly_lost = False
        idx = self.next_cn0_check_index
        while idx < outputs.cn0_index:
            row = outputs.cn0_dbhz[idx]
            best = float(np.nanmax(row)) if np.any(~np.isnan(row)) else float("nan")
            if best >= self.config.cn0_floor_dbhz:
                self.bad_streak = 0
                self._refresh_last_good()
            else:
                self.bad_streak += 1
                if (
                    self.bad_streak >= self.config.bad_streak_to_declare_lost
                    and self.status == "TRACKING"
                ):
                    self.status = "SEARCHING"
                    self.lost_events += 1
                    self.attempts_since_lost = 0
                    self.lost_at_uptime_ms = float(outputs.cn0_uptime_ms[idx])
                    refined = self._refine_truncation_index(outputs)
                    if refined > outputs.valid.start:
                        # circ_length's boundary supersedes the coarse C/N0
                        # one -- it can land either side of it (later, if C/N0
                        # just hadn't reacted yet; earlier, if C/N0's own
                        # averaging window was already straddling the jump and
                        # read "good enough" regardless) -- and is a better
                        # anchor for the re-acquisition search either way, not
                        # just a tighter truncation point.
                        ridx = refined - 1
                        self.last_good_uptime_ms = float(outputs.uptime_epoch_ms[ridx])
                        self.last_good_doppler_hz = float(outputs.doppler_freq_hz[ridx])
                        self.last_good_code_phase_ms = float(outputs.code_phase_ms[ridx])
                    outputs.output_index = refined
                    self.next_retry_uptime_ms = self.last_good_uptime_ms
                    # Release whatever capacity this segment never used. It was
                    # allocated for however much of the run remained when it
                    # STARTED (create_tracking_channels has no way to know it
                    # would only last a few seconds before the next gap), and
                    # nothing else ever shrinks it -- left alone, every segment's
                    # full over-sized arrays stay allocated for the rest of the
                    # run, which is real, compounding memory waste on a PRN that
                    # drops and reacquires many times.
                    trimmed = _trim_segment(self.adapter)
                    self.segments[-1] = trimmed
                    self.adapter = trimmed
                    outputs = trimmed.outputs
                    newly_lost = True
            idx += 1
        self.next_cn0_check_index = idx
        return newly_lost

    def give_up_if_stale(self, uptime_ms: float) -> bool:
        """
        Stop retrying a channel that has been SEARCHING for longer than
        `config.give_up_after_ms` (stream time), flipping it to "ABANDONED".
        No further re-acquisition attempts are made against it for the rest
        of the run -- see `ReacquisitionConfig.give_up_after_ms` for why this
        matters: an unbounded retry loop against a satellite that is simply
        gone is not free, especially on L5.

        Returns True the moment this call is the one that gives up, so the
        caller can log it once. A no-op, returning False, once already
        "ABANDONED" or while still "TRACKING".
        """
        if self.status != "SEARCHING":
            return False
        if self.config.give_up_after_ms is None:
            return False
        if uptime_ms - self.lost_at_uptime_ms < self.config.give_up_after_ms:
            return False
        self.status = "ABANDONED"
        return True

    def predicted_code_phase_ms(self, at_uptime_ms: float, carrier_freq_hz: float) -> float:
        """Linear projection of the last known-good code phase to `at_uptime_ms`."""
        code_rate_ms_per_sec = (
            1.0 + self.last_good_doppler_hz / carrier_freq_hz
        ) * 1e3
        dt_sec = (at_uptime_ms - self.last_good_uptime_ms) * 1e-3
        return self.last_good_code_phase_ms + dt_sec * code_rate_ms_per_sec

    def doppler_search_bounds(self) -> tuple[float, float]:
        margin = min(
            self.config.doppler_margin_hz
            + self.attempts_since_lost * self.config.doppler_margin_growth_hz,
            self.config.doppler_margin_max_hz,
        )
        return self.last_good_doppler_hz - margin, self.last_good_doppler_hz + margin

    def try_reacquire(
        self,
        signal_type,
        signal,
        acq_code_params: "bpsk_acquisition.AcqSignalCodeParameters",
        baseband_samples: np.ndarray,
        samples_start_uptime_ms: float,
        samp_rate: float,
        acq_settings: dict,
        loop_params: tracking_channel.TrackingLoopParameters,
        output_capacity: int,
        cn0_params: tracking_channel.CN0EstimatorParameters | None = None,
    ) -> bool:
        """
        One narrow re-acquisition attempt against `baseband_samples` (already
        mixed to baseband, DC not yet removed -- one of this PRN's regular
        100 ms tracking buffers is plenty; only the front of it, one
        acquisition dwell long, is actually used).

        On success, builds a fresh TrackingChannel via
        `signal_interfaces.create_tracking_channels` -- the same construction
        path the initial acquisition uses -- appends it as a new segment, and
        flips this supervisor back to TRACKING.  Returns whether it succeeded.
        """
        cfg = self.config
        min_dopp, max_dopp = self.doppler_search_bounds()

        # Widen to at least `min_doppler_search_bins` whole FFT bins -- see the
        # field's docstring in `ReacquisitionConfig`. Bin width depends only on
        # the replica length, not on the sample rate or the margin above.
        fft_resolution_hz = 1000.0 / acq_settings["coherent_duration_replica_ms"]
        min_span_hz = cfg.min_doppler_search_bins * fft_resolution_hz
        if max_dopp - min_dopp < min_span_hz:
            center = 0.5 * (min_dopp + max_dopp)
            half = 0.5 * min_span_hz
            min_dopp, max_dopp = center - half, center + half

        acq_config = bpsk_acquisition.AcquisitionConfiguration(
            coherent_duration_replica_ms=acq_settings["coherent_duration_replica_ms"],
            coherent_duration_sample_ms=acq_settings["coherent_duration_sample_ms"],
            num_blocks=acq_settings["num_blocks"],
            sample_rate=samp_rate,
            min_search_doppler_hz=min_dopp,
            max_search_doppler_hz=max_dopp,
            fine_search=acq_settings.get("fine_search"),
        )
        dwell_samples = acq_config.total_num_samples
        if len(baseband_samples) < dwell_samples:
            # Not enough data handed in for even one dwell (a short trailing
            # buffer at end of file); just wait for the next retry.
            self.next_retry_uptime_ms = samples_start_uptime_ms + cfg.retry_interval_ms
            return False

        dwell = baseband_samples[:dwell_samples].copy()
        dwell -= np.mean(dwell)

        results = bpsk_acquisition.run_acquisition(
            sample_block=dwell,
            sample_block_uptime_epoch_ms=samples_start_uptime_ms,
            acq_config=acq_config,
            code_parameters={self.signal_id: acq_code_params},
            prob_false_alarm_total=cfg.prob_false_alarm_total,
            print_progress=False,
            noise_var_method="abscorrvar",
        )
        result = results[self.signal_id]
        self.attempts_since_lost += 1

        if not result.signal_detected:
            self.next_retry_uptime_ms = samples_start_uptime_ms + cfg.retry_interval_ms
            return False

        # Code phase runs at ~1 ms per ms of real time, so the gap between the
        # PREDICTED phase (extrapolated as if no time had gone missing) and the
        # phase this re-acquisition actually found is -- to first order -- an
        # ESTIMATE of how much real time this channel's recording gap actually
        # spanned. It is informational, not a gate: `result.signal_detected`
        # already means this cleared run_acquisition's own false-alarm-controlled
        # threshold on this PRN's own code, which is what actually protects
        # against a false lock here.
        predicted_ms = self.predicted_code_phase_ms(
            samples_start_uptime_ms, signal_type.carrier_freq_hz
        )
        ambiguity_ms = result.code_phase_ambiguity_ms
        implied_gap_ms = (
            (result.acq_code_phase_ms - predicted_ms + ambiguity_ms / 2) % ambiguity_ms
            - ambiguity_ms / 2
        )
        if abs(implied_gap_ms) > cfg.implausible_gap_warn_ms:
            warnings.warn(
                f"{self.signal_id}: narrow re-acquisition implies a "
                f"{implied_gap_ms:+.1f} ms gap since the last known-good epoch "
                f"-- unusually large for one overflow (warn threshold "
                f"{cfg.implausible_gap_warn_ms:g} ms). Accepting anyway.",
                RuntimeWarning,
                stacklevel=2,
            )

        new_adapters = signal_interfaces.create_tracking_channels(
            signal_type,
            signals={self.signal_id: signal},
            acquisition_results={self.signal_id: result},
            tracking_signal_ids=[self.signal_id],
            loop_params=loop_params,
            output_capacity=output_capacity,
            cn0_params=cn0_params,
        )
        new_adapter = new_adapters[self.signal_id]

        self.adapter = new_adapter
        self.segments.append(new_adapter)
        self.last_implied_gap_ms = implied_gap_ms
        self.implied_gap_ms_by_segment.append(implied_gap_ms)
        self.status = "TRACKING"
        self.bad_streak = 0
        self.next_cn0_check_index = 0
        self.last_good_uptime_ms = samples_start_uptime_ms
        self.last_good_doppler_hz = result.acq_doppler_hz
        self.last_good_code_phase_ms = result.acq_code_phase_ms
        return True


_EPOCH_FIELDS = (
    "uptime_epoch_ms",
    "carr_phase_errors_cycles",
    "code_phase_errors_chips",
    "subcarrier_offset_chips",
    "carr_phase_cycles",
    "doppler_freq_hz",
    "code_phase_ms",
    "delta_omega",
    "prompt_corr_circ_length",
    "pll_mode",
    "epoch_duration_ms",
    "overlay_synced",
    "bit_synced",
)
_CN0_FIELDS = ("cn0_dbhz", "cn0_uptime_ms")


def merge_segments(
    segments: list[signal_interfaces.TrackingChannelAdapter],
) -> tracking_channel.SignalTrackingOutputs:
    """
    Concatenate every segment's VALID epochs and C/N0 estimates, in order, into
    one real `SignalTrackingOutputs`.

    This is what lets a PRN that lost and regained lock read, downstream, as one
    continuous record instead of only the last segment -- with a genuine, visible
    jump in `uptime_epoch_ms` at every gap.  That jump is the honest
    representation of what actually happened to the recording: it is deliberately
    NOT smoothed over, unlike discarding the pre-gap history (losing real
    tracking data) or interpolating across it (inventing tracking data).

    Field list mirrors `utils.tracking_io`'s `_EPOCH_FIELDS`/`_CN0_FIELDS` exactly,
    so the merged result round-trips through `save_tracking_results` the same way
    a single unbroken run would.
    """
    if not segments:
        raise ValueError("need at least one segment to merge")

    first_outputs = segments[0].outputs
    total_epochs = sum(seg.outputs.output_index for seg in segments)
    total_cn0 = sum(seg.outputs.cn0_index for seg in segments)

    merged = tracking_channel.SignalTrackingOutputs(
        capacity=max(total_epochs, 1),
        num_components=first_outputs.num_components,
        cn0_capacity=max(total_cn0, 1),
        tap_layout=first_outputs.tap_layout,
    )

    epoch_write = 0
    cn0_write = 0
    for seg in segments:
        outputs = seg.outputs
        n = outputs.output_index
        for name in _EPOCH_FIELDS:
            getattr(merged, name)[epoch_write : epoch_write + n] = getattr(outputs, name)[:n]
        merged.corr[epoch_write : epoch_write + n] = outputs.corr[:n]
        epoch_write += n

        n_cn0 = outputs.cn0_index
        for name in _CN0_FIELDS:
            getattr(merged, name)[cn0_write : cn0_write + n_cn0] = getattr(outputs, name)[:n_cn0]
        cn0_write += n_cn0

    merged.output_index = epoch_write
    merged.cn0_index = cn0_write
    return merged


@dataclass
class _MergedChannelView:
    """
    Stand-in for a `TrackingChannel`, carrying only what a consumer of
    `MergedTrackingChannelAdapter.channel` actually reads: `loop_params`/`policy`
    (static configuration, identical across every segment of one PRN since they
    all share `tracking_loop_params`) -- and, critically, `outputs`, so that it
    resolves to the MERGED array rather than the last segment's own small one.

    This exists because `tracking_io.save_tracking_results` does
    `getattr(holder, "channel", holder).outputs` to unwrap an adapter -- if
    `.channel` were the last segment's real `TrackingChannel` (as a first version
    of this module had it), that line would silently read that channel's own
    unmerged `outputs`, saving only its last segment to disk while `outputs` on
    the adapter itself -- the one actually populated by `merge_segments` -- was
    never looked at. Every earlier segment's tracking data would still be
    correctly discarded before it were saved, not merely before it were shown.
    """

    outputs: tracking_channel.SignalTrackingOutputs
    loop_params: tracking_channel.TrackingLoopParameters
    policy: tracking_channel.LoopDiscriminatorPolicy


@dataclass
class MergedTrackingChannelAdapter:
    """
    Read-only stand-in for `signal_interfaces.TrackingChannelAdapter`, built from
    every segment one PRN went through.  Exposes exactly the surface notebook 01's
    plotting cell and `tracking_io.save_tracking_results` actually read
    (`.signal`, `.channel`, `.outputs`, and the three `get_*_component` methods) so
    both work against a reacquired PRN with no changes of their own.

    `process_sample_buffer` is deliberately not implemented: nothing should ever
    feed live samples to a merged view.
    """

    signal: object
    channel: _MergedChannelView
    outputs: tracking_channel.SignalTrackingOutputs

    def component_index(self, name: str) -> int:
        return self.signal.code_set.index_of(name)

    def get_prompt_component(self, component: int = 0) -> np.ndarray:
        return self.outputs.prompt_corr[self.outputs.valid, component]

    def get_early_component(self, component: int = 0) -> np.ndarray:
        return self.outputs.early_corr[self.outputs.valid, component]

    def get_late_component(self, component: int = 0) -> np.ndarray:
        return self.outputs.late_corr[self.outputs.valid, component]


def build_merged_adapter(supervisor: ChannelSupervisor) -> MergedTrackingChannelAdapter:
    merged_outputs = merge_segments(supervisor.segments)
    last = supervisor.segments[-1]
    view = _MergedChannelView(
        outputs=merged_outputs,
        loop_params=last.channel.loop_params,
        policy=last.channel.policy,
    )
    return MergedTrackingChannelAdapter(signal=last.signal, channel=view, outputs=merged_outputs)


def _trim_segment(adapter) -> MergedTrackingChannelAdapter:
    """
    A right-sized copy of one frozen segment, holding only the epochs it
    actually used.

    `merge_segments` on a single-element list is exactly the compaction this
    needs: a fresh `SignalTrackingOutputs` allocated at `output_index` rather
    than at whatever capacity the segment was originally given -- reused
    rather than duplicated, since the two operations are the same thing at
    n=1. Called the instant a segment is frozen (`ChannelSupervisor.check_lock`),
    so the old, over-sized backing arrays (a reacquired segment is allocated
    for however much of the run remained when it STARTED, not for how long it
    will actually last before the next gap) can be garbage collected
    immediately instead of sitting in `segments` for the rest of the run.
    """
    trimmed_outputs = merge_segments([adapter])
    view = _MergedChannelView(
        outputs=trimmed_outputs,
        loop_params=adapter.channel.loop_params,
        policy=adapter.channel.policy,
    )
    return MergedTrackingChannelAdapter(signal=adapter.signal, channel=view, outputs=trimmed_outputs)


def start_supervisors(
    tracking_channels: dict[str, signal_interfaces.TrackingChannelAdapter],
    config: ReacquisitionConfig,
) -> dict[str, ChannelSupervisor]:
    """One ChannelSupervisor per initially-acquired PRN, wrapping its adapter."""
    return {
        signal_id: ChannelSupervisor(signal_id=signal_id, adapter=adapter, config=config)
        for signal_id, adapter in tracking_channels.items()
    }


def summary_rows(supervisors: dict[str, ChannelSupervisor]) -> list[list[str]]:
    """
    One row per PRN: how many times it dropped, how many segments resulted, and
    the implied gap length of each successful re-acquisition -- the closest
    thing available to a direct measurement of how much real time each
    overflow actually dropped from the recording (see `try_reacquire`).
    """
    rows = []
    for signal_id, sup in sorted(supervisors.items()):
        total_epochs = sum(seg.outputs.output_index for seg in sup.segments)
        gaps = ", ".join(f"{g:+.0f}" for g in sup.implied_gap_ms_by_segment) or "-"
        rows.append([
            signal_id,
            str(sup.lost_events),
            str(len(sup.segments)),
            str(total_epochs),
            sup.status,
            gaps,
        ])
    return rows
