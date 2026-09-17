"""
From tracking outputs to navigation-message symbols.

The decoders in this package take soft symbols, one per data symbol.  A tracking
channel produces prompt correlator values, one per *epoch*, and an epoch is not
generally a symbol.  This module is the bridge, and it exists as its own file
because the mapping is different for every signal and each difference is a
property of the signal rather than of the decoder:

    signal   data component   symbol    where the boundary comes from
    ------   --------------   ------    -----------------------------
    L1 C/A   0 (the only one)  20 ms    statistical bit sync -- nothing else exists
    L2C      0 (CM)            20 ms    the CM code period IS the symbol
    L5       0 (L5I)           10 ms    NH10 overlay sync
    L1C      0 (L1CD)          10 ms    L1CD's code period; L1CO gives the frame

The two things that make this more than a reshape:

**Epoch length changes mid-run.**  Every channel opens at one 1 ms correlation
interval, and `TrackingChannel._maybe_extend_coherent_duration` lengthens the
coherent accumulation to the configured duration once the PLL has locked and the
symbol boundary is known -- on L5, 1 ms to at most 10 ms.  So the same output array
holds epochs of two different lengths, and only the longer ones tile whole symbols.
That is what `outputs.epoch_duration_ms` is for.

**Not every epoch is usable.**  Two edges have to be behind the channel before its
epochs are symbols.  Until the overlay is synced the tiered code is still flipping
the data component's sign every code period; and until the carrier loop is in PLL
the epochs have random relative phase, so summing them cancels rather than
integrates.  Epochs before either edge are dropped rather than salvaged -- see
`_usable_regions`, which explains why salvaging them would fail silently.

What this module deliberately does not do is resolve the carrier's 180 degree
phase ambiguity.  That is not knowable here; it is knowable at frame sync, where a
preamble either matches or does not, so `cnav.decode` and `lnav.decode` handle it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import bitsync

# Data component index and symbol period, per signal type.  The component is index
# 0 in every case -- the pilot is always the later component in this project's
# catalog (see `utils.signal_interfaces`) -- but stating it explicitly keeps the
# table readable against the signal definitions rather than relying on a
# coincidence that a future signal could break.
DATA_COMPONENT: dict[str, int] = {
    "GPS_L1CA": 0,
    "GPS_L2C": 0,  # CM
    "GPS_L5": 0,  # L5I
    "GPS_L1C": 0,  # L1CD
}

SYMBOL_PERIOD_MS: dict[str, int] = {
    "GPS_L1CA": 20,
    "GPS_L2C": 20,
    "GPS_L5": 10,
    "GPS_L1C": 10,
}

# Whether the signal's symbol boundary is established by an overlay code.  L1 C/A
# is the only one that has to find it statistically.
NEEDS_BIT_SYNC: dict[str, bool] = {
    "GPS_L1CA": True,
    "GPS_L2C": False,
    "GPS_L5": False,
    "GPS_L1C": False,
}

# Whether the channel had a tiered (overlay) code to synchronise, and therefore
# whether `overlay_synced` is a meaningful gate on the epochs.
#
# NOT the complement of `NEEDS_BIT_SYNC`, which is the mistake this table exists
# to prevent: L2C needs neither.  Its 20 ms CM code period *is* one symbol, so the
# code phase locates the boundary with nothing to sync and nothing to search for,
# and `overlay_synced` stays False for the whole run.  Gating on it there discards
# every epoch and the channel silently yields no symbols at all.
# The longest epoch at which this module can still find a bit boundary itself.
# One code period: a longer epoch has already summed the 1 ms structure the
# histogram is built from into a single number, and no amount of such numbers
# brings it back.  Beyond this length the boundary has to come from the channel,
# which measured it while its epochs were still short.
RESOLVABLE_EPOCH_MS = 1.0

HAS_OVERLAY: dict[str, bool] = {
    "GPS_L1CA": False,   # 1 ms code, 20 ms bit, nothing tying them together
    "GPS_L2C": False,    # CM code period is the symbol
    "GPS_L5": True,      # NH10/NH20
    "GPS_L1C": True,     # L1CO, 1800 bits at one per 10 ms code period
}

# Whether the data component sits 90 degrees from the carrier loop's reference.
#
# This decides whether a soft symbol is the real or the imaginary part of the
# prompt correlator, and getting it wrong costs the entire message -- the decoder
# is handed the noise axis and simply never syncs.
#
# L5 is the one signal where it is True, and the reason is in
# `TRACKING_POLICIES["GPS_L5"]`: the carrier loop runs on the Q pilot from the
# first epoch, and L5's I and Q are separated by carrier phase, so the data
# component lands in quadrature with the reference.  Everywhere else the data
# component shares the reference's phase -- L1 C/A tracks the data component
# itself; L2C's CM and CL are time-multiplexed on one carrier branch, which is
# exactly why the carrier can move between them without a re-pull; L1C sums L1CD
# and L1CP in phase (IS-GPS-800J 3.2.1.6.1).
DATA_IN_QUADRATURE: dict[str, bool] = {
    "GPS_L1CA": False,
    "GPS_L2C": False,
    "GPS_L5": True,
    "GPS_L1C": False,
}


@dataclass
class SymbolStream:
    """Soft symbols with enough context to tie a decoded time back to tracking."""

    values: np.ndarray
    """Complex soft symbols, one per data symbol."""

    uptime_ms: np.ndarray
    """Uptime at the *start* of each symbol.  Same length as `values`."""

    code_phase_ms: np.ndarray
    """Cumulative code phase at the start of each symbol.  This is what turns a
    decoded time of week into a transmit time for every later epoch, because it
    measures satellite code time and advances at the satellite's own rate."""

    symbol_period_ms: int
    signal_type_id: str
    epochs_per_symbol: int
    """How many tracking epochs were summed into each symbol."""

    dropped_epochs: int
    """Epochs discarded before the first whole symbol -- unsynced, unlocked, or a
    partial leading symbol.  A large value on a long run means the channel spent a
    long time pulling in, not that anything is wrong."""

    bit_sync: bitsync.BitSyncResult | None = None
    """The boundary search this module ran, when it ran one.

    None whenever the boundary arrived already established: from an overlay, from a
    code period that is itself a symbol, or -- on L1 C/A tracked at more than one
    code period per epoch -- from the channel, which measured it while its epochs
    were still short and anchored the longer grid to it.  A populated value here
    therefore means the phase was recovered from these symbols, not that the signal
    is L1 C/A."""

    data_in_quadrature: bool = False
    """Whether the data rides 90 degrees from the carrier reference -- see
    `DATA_IN_QUADRATURE`.  True for L5 only."""

    @property
    def soft(self) -> np.ndarray:
        """
        Real soft symbols, sign carrying the bit -- what every decoder here takes.

        Which axis that is depends on the signal, not on the decoder, so the
        projection happens once here.  See `DATA_IN_QUADRATURE`.
        """
        return np.imag(self.values) if self.data_in_quadrature else np.real(self.values)

    @property
    def quadrature_axis(self) -> np.ndarray:
        """The axis the data is *not* on, which is therefore noise alone."""
        return np.real(self.values) if self.data_in_quadrature else np.imag(self.values)

    @property
    def symbol_snr_db(self) -> float:
        """
        Signal-to-noise ratio of one symbol, in dB, measured off the two axes.

        The data axis carries signal plus noise; the axis 90 degrees from it
        carries noise alone.  That second fact is what makes this cheap -- the
        noise power is not modelled, it is measured on an axis the signal is not
        supposed to be on:

            N = mean(quadrature axis squared)    noise power per axis
            S = mean(data axis squared) - N      what is left on the data axis

        Subtracting rather than reading the data axis as signal is what keeps the
        estimate honest at low SNR, where most of what sits on that axis is noise.

        This is the SNR of one **symbol**, after its epochs were summed -- not
        C/N0, which is a density and is what `utils.tracking_channel` estimates
        per correlation interval.  They differ by the symbol duration:
        `C/N0 = SNR + 10*log10(1/T)`, so a 20 ms symbol sits about 17 dB below the
        C/N0 figure notebook 01 plots and a 10 ms symbol about 20 dB below.
        Comparing the two is a real check -- they are measured on different
        quantities at different rates -- but only a rough one, since this makes no
        allowance for the coherence the summing actually achieved.

        Returns -inf when the data axis holds no more power than the quadrature
        one.  That is not a weak signal but a signal on the wrong axis: the carrier
        is not locked the way `DATA_IN_QUADRATURE` says it is, and the decoder is
        about to be handed the noise axis.  Worth seeing before blaming the decoder
        for failing to sync.  A residual carrier phase error tilts the estimate the
        same way, by rotating signal onto the noise axis, so this reads low rather
        than high when phase lock is poor.
        """
        if not len(self.values):
            return float("-inf")
        noise_power = float(np.mean(self.quadrature_axis**2))
        signal_power = float(np.mean(self.soft**2)) - noise_power
        if noise_power <= 0.0 or signal_power <= 0.0:
            return float("-inf")
        return float(10.0 * np.log10(signal_power / noise_power))

    def __len__(self) -> int:
        return len(self.values)


def _usable_regions(
    outputs, *, require_overlay_sync: bool
) -> list[tuple[int, int, float]]:
    """
    Every maximal stretch of epochs worth turning into symbols, as
    `(first, stop, epoch_ms)` triples -- half-open ranges into `outputs`, each
    internally at one, steady-state coherent duration.

    A single, uninterrupted tracking run crosses three edges -- carrier lock,
    the coherent-duration extension, overlay sync -- exactly once, each in one
    direction, which is why this used to return a single start index and trust
    everything after it. That stops being true the moment a run is stitched
    from more than one segment (`utils.tracking_supervisor`, recovering a
    channel that lost lock partway through -- e.g. a USRP overflow dropping
    samples out of the recording): each new segment reopens at FLL, at a 1 ms
    correlation interval, with its own overlay to (re-)establish, so those
    same three edges happen again, once per segment. Treating the whole merged
    array as "good from the first lock onward" would fold a later segment's
    own un-synced, short-epoch warm-up into what should be the previous
    segment's clean, steady-state data -- and reshaping that mix at one fixed
    stride (see `extract`) corrupts every symbol from the first such seam on,
    silently, which is why a run stitched from several segments could
    previously decode nothing at all despite each segment individually
    carrying a perfectly good stretch of signal.

    **Carrier lock.**  Summing epochs into a symbol is *coherent* summation, and
    without a phase-locked carrier the epochs have random relative phase -- they
    cancel instead of integrating, and the data/quadrature split that
    `DATA_IN_QUADRATURE` relies on does not exist at all.

    **Steady-state coherent duration.**  Before a segment's extension the epochs
    are shorter and do not tile the symbol grid the same way; each region uses
    its OWN final duration, since two segments need not agree (though in this
    project's pipeline they always do -- both are built from the same
    `TrackingLoopParameters`).

    **Overlay sync**, for signals that have an overlay.  Until the tiered code is
    stripped it is still flipping the data component's sign every code period.

    A region with no PLL-locked epoch at its own final duration is dropped
    entirely rather than reported as one bad epoch wide.
    """
    valid = outputs.valid
    start, stop = valid.start, valid.stop
    if stop <= start:
        return []

    pll = outputs.pll_mode[start:stop]
    overlay = (
        outputs.overlay_synced[start:stop]
        if require_overlay_sync
        else np.ones(stop - start, dtype=bool)
    )
    good = pll & overlay
    if not good.any():
        return []

    # Maximal runs of True in `good`, as (run_start, run_stop) index pairs local
    # to [start, stop). Padding with a False on each end turns every run
    # boundary into a sign change in the diff, at an even/odd pair of indices.
    padded = np.concatenate(([False], good, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    durations = outputs.epoch_duration_ms[start:stop]

    regions: list[tuple[int, int, float]] = []
    for run_start, run_stop in zip(edges[0::2], edges[1::2]):
        run_durations = durations[run_start:run_stop]
        final_duration = run_durations[-1]
        at_final = np.nonzero(run_durations == final_duration)[0]
        if not len(at_final):
            continue
        first = run_start + int(at_final[0])
        if first < run_stop:
            regions.append((start + first, start + run_stop, float(final_duration)))
    return regions


def extract(
    outputs,
    signal_type_id: str,
    *,
    component: int | None = None,
    bit_sync_confidence_threshold: float = 2.0,
) -> SymbolStream:
    """
    Turn one channel's tracking outputs into a decodable symbol stream.

    `outputs` is a `utils.tracking_channel.SignalTrackingOutputs`.  Only the epochs
    that are genuinely symbols are used -- see the module docstring for what that
    excludes and why.

    Raises `ValueError` for a signal this module has no mapping for, rather than
    guessing a symbol period.  A wrong symbol period does not fail loudly later; it
    produces a stream that simply never frame-syncs, which is a much harder thing
    to diagnose.
    """
    if signal_type_id not in SYMBOL_PERIOD_MS:
        raise ValueError(
            f"no symbol mapping for {signal_type_id!r}; known signals are "
            f"{sorted(SYMBOL_PERIOD_MS)}"
        )
    if component is None:
        component = DATA_COMPONENT[signal_type_id]

    symbol_period_ms = SYMBOL_PERIOD_MS[signal_type_id]
    needs_bit_sync = NEEDS_BIT_SYNC[signal_type_id]

    def _empty(dropped: int, epochs_per_symbol: int = 0, sync=None) -> SymbolStream:
        return SymbolStream(
            values=np.zeros(0, dtype=complex),
            uptime_ms=np.zeros(0),
            code_phase_ms=np.zeros(0),
            symbol_period_ms=symbol_period_ms,
            signal_type_id=signal_type_id,
            epochs_per_symbol=epochs_per_symbol,
            dropped_epochs=dropped,
            bit_sync=sync,
            data_in_quadrature=DATA_IN_QUADRATURE[signal_type_id],
        )

    regions = _usable_regions(outputs, require_overlay_sync=HAS_OVERLAY[signal_type_id])
    if not regions:
        return _empty(outputs.output_index)

    # Symbols accumulate ACROSS regions -- one per maximal locked/synced/steady-
    # state stretch (ordinarily one, the whole run; more than one on a run that
    # lost and regained lock, see `_usable_regions`) -- and are concatenated, so
    # the decoder downstream (`cnav.decode`/`lnav.decode`) sees every good
    # stretch of signal in one stream. A seam between two temporally unrelated
    # regions is harmless there: a frame-sync search across it just fails its
    # CRC/parity check like any other wrong offset, exactly as it would at a
    # random offset inside a single region.
    all_values, all_uptime, all_code_phase = [], [], []
    epochs_per_symbol = 0
    last_sync: bitsync.BitSyncResult | None = None
    # Epochs outside any region (never locked, never synced, or trimmed to a
    # neighbouring segment's warm-up) plus, per region, the leading epochs
    # skipped to reach the first symbol boundary and any trailing partial symbol.
    dropped = outputs.output_index - sum(stop - first for first, stop, _ in regions)

    for first, stop, epoch_ms in regions:
        if epoch_ms <= 0:
            raise ValueError(
                "epoch duration was never recorded; these outputs predate "
                "SignalTrackingOutputs.epoch_duration_ms and cannot be turned into symbols"
            )
        if symbol_period_ms % epoch_ms != 0:
            raise ValueError(
                f"{signal_type_id} has a {symbol_period_ms} ms symbol but the channel's "
                f"epochs are {epoch_ms:g} ms, which does not divide it. Set "
                "COHERENT_DURATION_MS to a divisor of the symbol period."
            )
        eps = int(symbol_period_ms // epoch_ms)
        epochs_per_symbol = eps

        prompts = outputs.prompt_corr[first:stop, component]
        uptime = outputs.uptime_epoch_ms[first:stop]
        code_phase = outputs.code_phase_ms[first:stop]

        phase = 0
        if needs_bit_sync and epoch_ms > RESOLVABLE_EPOCH_MS:
            # The channel found the boundary and opened this grid on it, so the
            # region's first epoch is a symbol boundary and there is nothing left
            # to search for. It has to be checked rather than assumed -- see
            # `_usable_regions`'s docstring and the original single-region
            # version of this check for why.
            if not outputs.bit_synced[first:stop].all():
                raise ValueError(
                    f"{signal_type_id} epochs are {epoch_ms:g} ms, but the channel never "
                    "anchored its grid to a measured data bit boundary, so where a symbol "
                    "starts is unknown to within a code period. Build the channel with "
                    "`signal_interfaces.create_tracking_channels`, which starts at one "
                    "code period and extends once the boundary is found, or track at "
                    f"{RESOLVABLE_EPOCH_MS:g} ms and let this module find it."
                )
        elif needs_bit_sync:
            # Bit sync reads sign transitions, so it must look at the data axis
            # too. Run fresh per region: each one is its own independent stretch
            # of epochs with its own, unrelated symbol phase.
            sync = bitsync.synchronise(
                1j * prompts if DATA_IN_QUADRATURE[signal_type_id] else prompts,
                periods_per_bit=eps,
                confidence_threshold=bit_sync_confidence_threshold,
            )
            last_sync = sync
            if not sync.synced:
                continue
            phase = sync.phase
        else:
            # The overlay already aligned the epoch grid to symbol boundaries, but
            # the region's first epoch is not necessarily the first epoch OF a
            # symbol. Code phase says which: it counts code periods from a
            # symbol-aligned origin, so the offset into the current symbol is code
            # phase modulo the symbol period.
            offset_ms = code_phase[0] % symbol_period_ms
            phase = int(round((symbol_period_ms - offset_ms) % symbol_period_ms / epoch_ms))

        usable = (len(prompts) - phase) // eps
        if usable <= 0:
            continue

        trimmed = prompts[phase : phase + usable * eps]
        values = trimmed.reshape(usable, eps).sum(axis=1)
        # Timestamps come from the FIRST epoch of each symbol, not an average: the
        # symbol's epoch is when it started, and that is what a decoded time of week
        # refers to.
        starts = np.arange(usable) * eps + phase
        all_values.append(values)
        all_uptime.append(uptime[starts])
        all_code_phase.append(code_phase[starts])
        dropped += phase + (len(prompts) - phase - usable * eps)

    if not all_values:
        return _empty(dropped, epochs_per_symbol, last_sync)

    return SymbolStream(
        values=np.concatenate(all_values),
        uptime_ms=np.concatenate(all_uptime),
        code_phase_ms=np.concatenate(all_code_phase),
        symbol_period_ms=symbol_period_ms,
        signal_type_id=signal_type_id,
        epochs_per_symbol=epochs_per_symbol,
        dropped_epochs=dropped,
        bit_sync=last_sync,
        data_in_quadrature=DATA_IN_QUADRATURE[signal_type_id],
    )
