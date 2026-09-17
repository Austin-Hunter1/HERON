from typing import TYPE_CHECKING, Iterable, Optional, Sequence

import numpy as np
import matplotlib.pyplot as plt
import scipy.signal
import scipy.stats
from matplotlib.axes import Axes
from matplotlib.figure import Figure, SubFigure
from scipy.constants import speed_of_light

from . import bpsk_acquisition, tracking_channel
from .collect_metadata_utils import ExperimentMetadata

if TYPE_CHECKING:  # avoids importing signal_interfaces at runtime
    from .signal_interfaces import TrackingChannelAdapter


def setup_default_plotting():
    """
    Sets up default plotting parameters for matplotlib.
    """
    plt.rcParams.update({
        'figure.dpi': 150,
        'figure.figsize': (10, 6),
        # 'axes.titlesize': 16,
        # 'axes.labelsize': 14,
        # 'xtick.labelsize': 12,
        # 'ytick.labelsize': 12,
        # 'legend.fontsize': 12,
        # 'grid.color': 'gray',
        # 'grid.linestyle': '--',
        # 'grid.linewidth': 0.5,
        # 'lines.linewidth': 2,
        # 'lines.markersize': 6
    })


def plot_receiver_channel_bands(
        fig: Figure | SubFigure,
        metadata: ExperimentMetadata,
        include_bands: Optional[Iterable[str]] = None,
        exclude_bands: Optional[Iterable[str]] = None,
        samp_bandwidth_height: float = 0.5,
        samp_center_height: float = 0.8,
):
    axes = fig.subplots(1, 2, sharey=True)
    ax1: Axes = axes[0]
    ax2: Axes = axes[1]
    # Want to plot two axes;
    # Left shows baseband frequencies and right shows RF frequencies
    # Channel IDs go down the y-axis
    band_colors = {}
    for i, band_id in enumerate(metadata.band_ids):
        band_colors[band_id] = f"C{i}"

    band_configurations = metadata.band_configurations
    for i, channel_id in enumerate(metadata.channel_ids):
        channel_config = metadata.channel_configurations[channel_id]
        samp_bandwidth_MHz = channel_config.samp_rate / 1e6
        is_real = not channel_config.sample_params.is_complex
        if is_real:
            # Only shade positive frequencies
            ax1.fill_betweenx([i - samp_bandwidth_height / 2, i + samp_bandwidth_height / 2], 0, samp_bandwidth_MHz / 2, color="r", alpha=0.3)
        else:
            ax1.fill_betweenx([i - samp_bandwidth_height / 2, i + samp_bandwidth_height / 2], -samp_bandwidth_MHz / 2, samp_bandwidth_MHz / 2, color="b", alpha=0.3)

        for band_id in channel_config.band_ids:
            band_config = band_configurations[band_id]
            baseband_if_MHz = band_config.inter_freq / 1e6
            rf_center_MHz = band_config.center_freq / 1e6

            # Plot delta-like markers at IF (baseband) and RF center frequencies.
            ax1.vlines(baseband_if_MHz, i - samp_center_height / 2, i + samp_center_height / 2, color="k", linewidth=2)
            # ax1.plot([baseband_if], [i], marker="|", markersize=12, color="C0")

            ax2.vlines(rf_center_MHz, i - samp_center_height / 2, i + samp_center_height / 2, color="k", linewidth=2)
            # ax2.plot([rf_center], [i], marker="|", markersize=12, color="C1")

            rf_band_center_MHz = (band_config.center_freq - band_config.inter_freq) / 1e6
            if is_real:
                ax2.fill_betweenx([i - samp_bandwidth_height / 2, i + samp_bandwidth_height / 2], rf_band_center_MHz, rf_band_center_MHz + samp_bandwidth_MHz / 2, color="r", alpha=0.3)
            else:
                ax2.fill_betweenx([i - samp_bandwidth_height / 2, i + samp_bandwidth_height / 2], rf_band_center_MHz - samp_bandwidth_MHz / 2, rf_band_center_MHz + samp_bandwidth_MHz / 2, color="r", alpha=0.3)

    ax1.set_yticks(range(len(metadata.channel_ids)))
    ax1.set_yticklabels([f"{channel_id}: {metadata.channel_configurations[channel_id].band_ids[0]}" for channel_id in metadata.channel_ids])
    for ax in [ax1, ax2]:
        ax.grid()
    ax1.set_xlabel("Baseband Frequency [MHz]")
    ax2.set_xlabel("RF Frequency [MHz]")


# --- raw sample diagnostics -------------------------------------------------

def plot_raw_sample_histogram(fig: Figure | SubFigure, sample_buffer: np.ndarray, hist_bins: Optional[np.ndarray] = None) -> Axes:
    """Histogram of a raw complex sample buffer's real/imaginary components."""
    ax = fig.add_subplot(1, 1, 1)
    if hist_bins is None:
        hist_bins = np.arange(-128, 128)
    ax.hist(sample_buffer.real, bins=hist_bins, rwidth=0.5, color="r", align="left", label="Real")
    ax.hist(sample_buffer.imag, bins=hist_bins, rwidth=0.5, color="b", align="mid", label="Imaginary")
    ax.set_title("Histogram of Raw Samples")
    ax.set_xlabel("Sample Value")
    ax.set_ylabel("Count")
    ax.legend()
    ax.grid()
    return ax


def plot_welch_psd(
    fig: Figure | SubFigure,
    baseband_samples: np.ndarray,
    samp_rate: float,
    orig_samples: np.ndarray | None = None,
    nperseg: int = 4096,
    noverlap: int = 2048,
) -> Axes:
    """Welch PSD estimate of a raw sample buffer overlaid with its mixed-down baseband."""
    ax = fig.add_subplot(1, 1, 1)
    freqs, psd = scipy.signal.welch(
        baseband_samples, fs=samp_rate, nperseg=nperseg, noverlap=noverlap,
        window="hann", return_onesided=False, scaling="density",
    )
    freqs = np.fft.fftshift(freqs)
    psd = np.fft.fftshift(psd)
    if orig_samples is not None:
        _, psd_orig = scipy.signal.welch(
            orig_samples, fs=samp_rate, nperseg=nperseg, noverlap=noverlap,
            window="hann", return_onesided=False, scaling="density",
        )
        psd_orig = np.fft.fftshift(psd_orig)
        ax.plot(freqs / 1e6, 10 * np.log10(psd_orig), color="gray", label="Original Samples")

    ax.plot(freqs / 1e6, 10 * np.log10(psd), color="black", label="Baseband Samples")
    
    ax.set_title("Welch PSD Estimate of Raw Samples")
    ax.set_xlabel("Frequency (MHz)")
    ax.set_ylabel("Power/Frequency (dB/Hz)")
    ax.grid()
    ax.legend()
    return ax


def plot_sample_histogram_and_constellation(
    fig: Figure | SubFigure,
    samples: np.ndarray,
    bit_depth: int = 8,
) -> Sequence[Axes]:
    """
    Two views of one buffer of raw samples: a histogram of the I and Q values,
    and the I-vs-Q scatter ("constellation").

    What a healthy collect looks like: both histograms are bell-shaped (Gaussian)
    and centred near zero, and the scatter is a round, featureless blob. GNSS
    signals arrive far below the noise floor, so what you are looking at is
    essentially receiver noise -- the satellites are invisible until correlation
    pulls them out.

    What problems look like:
      - Histogram pressed flat against the ends of the range -> the front-end gain
        is too high and samples are clipping.
      - Histogram squeezed into just a few values near zero -> gain too low, and
        quantisation is throwing away the signal.
      - Q identically zero -> the data is real-valued, not complex; check
        `is_complex` in the collect's metadata.yml.
      - An off-centre blob, or a ring/arc rather than a disc -> a DC bias or an
        uncorrected carrier offset.

    `bit_depth` sets the histogram range to the full span the sample format can
    represent, so an under-driven collect is obvious by how little of the axis it
    fills.
    """
    axes = fig.subplots(1, 2, width_ratios=[1.5, 1])
    ax_hist: Axes = axes[0]
    ax_scatter: Axes = axes[1]

    hist_bins = np.arange(-(2 ** (bit_depth - 1)), 2 ** (bit_depth - 1))
    ax_hist.hist(samples.real, bins=hist_bins, histtype="stepfilled", color="r", alpha=0.6, align="left", label="Real (I)")
    ax_hist.hist(samples.imag, bins=hist_bins, histtype="stepfilled", color="b", alpha=0.6, align="mid", label="Imaginary (Q)")
    ax_hist.set_xlabel("Sample Value")
    ax_hist.set_ylabel("Count")
    ax_hist.grid()
    ax_hist.legend(loc="upper right")

    # alpha is very low because a 40 ms buffer is ~1e6 points: the density, not
    # any single dot, is the thing to read.
    ax_scatter.scatter(samples.real, samples.imag, color="k", s=1, alpha=0.01, zorder=1)
    ax_scatter.set_axisbelow(True)
    ax_scatter.grid()
    ax_scatter.set_xlabel("Real (I)")
    ax_scatter.set_ylabel("Imaginary (Q)")
    return axes


def plot_stft_periodogram(
    fig: Figure | SubFigure,
    periodogram: np.ndarray,
    samp_rate: float,
    total_duration_s: float,
) -> Axes:
    """
    Spectrogram: how the power spectrum of the collect changes over time.

    `periodogram` is (num_windows, num_freq_bins), one PSD estimate per time
    window, already fftshifted so frequency runs monotonically from -samp_rate/2
    to +samp_rate/2. Colour is power in dB.

    What to look for: a steady horizontal band across the whole capture means the
    front end behaved consistently. Vertical stripes are momentary interference or
    dropped samples; a band that brightens or fades over time means the gain (or
    the antenna's view of the sky) changed mid-collect. Narrow horizontal lines
    that persist are continuous-wave interference -- a jammer or a nearby
    oscillator -- which is exactly the kind of thing that stops acquisition from
    working later.
    """
    ax = fig.add_subplot(1, 1, 1)
    im = ax.imshow(
        10 * np.log10(periodogram.T),
        aspect="auto",
        origin="lower",
        extent=[0, total_duration_s, -samp_rate / 2e6, samp_rate / 2e6],
        interpolation="nearest",
    )
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Frequency [MHz]")
    ax.set_title("STFT Periodogram")
    fig.colorbar(im, ax=ax, label="Power/Frequency [dB/Hz]")
    return ax


# --- acquisition diagnostics -------------------------------------------------

def plot_acquisition_peak_values(
    fig: Figure | SubFigure,
    acq_results: dict[str, bpsk_acquisition.AcquisitionResult],
) -> Axes:
    """Stem plot of normalized peak correlation value per signal, with the detection threshold."""
    ax = fig.add_subplot(1, 1, 1)
    all_sig_ids = sorted(acq_results.keys())
    all_peak_vals = [acq_results[sig_id].normalized_peak_value for sig_id in all_sig_ids]
    ax.stem(range(len(all_peak_vals)), all_peak_vals, basefmt=" ")
    detection_threshold = acq_results[all_sig_ids[0]].detection_threshold
    ax.plot([0, len(all_sig_ids)], [detection_threshold] * 2, color="red", linestyle="--", label="Detection Threshold")
    ax.legend()
    ax.set_yscale("log")
    ax.set_xticks(range(len(all_sig_ids)))
    ax.set_xticklabels(all_sig_ids, rotation=45)
    ax.set_title("Acquisition Peak Correlation Values")
    ax.set_xlabel("Signal ID (PRN)")
    ax.set_ylabel("Normalized Peak Correlation Value")
    ax.grid()
    return ax


def plot_acquisition_doppler_slices(
    fig: Figure | SubFigure,
    acq_results: dict[str, bpsk_acquisition.AcquisitionResult],
) -> Axes:
    """
    Correlation-vs-Doppler slice through each signal's peak code-phase bin.

    Needs the full Doppler grid, so the results must come from an acquisition run
    with `save_corr_doppler_window_bins=None`; with the default window this
    degenerates to the three retained rows.
    """
    ax = fig.add_subplot(1, 1, 1)
    cmap = plt.get_cmap("tab20b")
    for i, (signal_id, acq_result) in enumerate(acq_results.items()):
        color = cmap(i / 40.0)
        marker = ("o", "x")[i % 2]
        ax.plot(
            acq_result.correlation_result.doppler_bins_hz,
            acq_result.correlation_result.correlation_matrix[:, acq_result.peak_code_phase_bin],
            color=color, marker=marker, label=f"{signal_id}",
        )
    ax.set_yscale("log")
    ax.set_title("Acquisition Correlation Results")
    ax.set_xlabel("Doppler Frequency [Hz]")
    ax.set_ylabel("Peak Slice Correlation Magnitude")
    ax.legend(ncol=4, fontsize=8)
    ax.grid()
    return ax


def plot_acquisition_delay_doppler_map(
    fig: Figure | SubFigure,
    acq_result: bpsk_acquisition.AcquisitionResult,
    code_phase_window_samples: int = 150,
    use_fine: bool = False,
) -> Axes:
    """Delay-Doppler correlation heatmap for one signal's acquisition result, zoomed to the peak."""
    ax = fig.add_subplot(1, 1, 1)
    source = acq_result.correlation_result
    if use_fine:
        if acq_result.fine_correlation_result is None:
            raise ValueError(
                f"{acq_result.signal_id}: no fine search result -- it runs only on a "
                "detection, and only when acq_config.fine_search is set"
            )
        source = acq_result.fine_correlation_result
    correlation = source.correlation_matrix
    num_doppler_bins, num_code_phases = correlation.shape

    # The Doppler extent comes from the rows actually retained, not from the config's
    # full search range: `save_corr_doppler_window_bins` keeps only a few rows around
    # the peak, and labelling them +/-5 kHz would be a lie.  The delay axis is kept
    # whole, so `peak_code_phase_bin` indexes it directly and the zoom below works.
    doppler_hz = source.doppler_bins_hz
    half_row = 0.5 * source.doppler_resolution_hz
    extent = [0, num_code_phases, doppler_hz[0] - half_row, doppler_hz[-1] + half_row]

    # NaN rows (a window overhanging the grid) should read as absent, not as zero.
    cmap = plt.get_cmap("plasma").copy()
    cmap.set_bad(color="0.85")

    if use_fine:
        # The fine grid IS the zoom, and its columns are fractional samples about
        # the peak, so the coarse sample-index window does not apply.
        code_phase_window_samples = num_code_phases
    peak_code_phase_bin = (
        num_code_phases // 2 if use_fine else acq_result.peak_code_phase_bin
    )
    im = ax.imshow(
        correlation, extent=extent, aspect="auto", interpolation="nearest",
        cmap=cmap, origin="lower", vmin=0,
    )
    ax.set_xlim(peak_code_phase_bin - code_phase_window_samples, peak_code_phase_bin + code_phase_window_samples)
    ax.set_xlabel("Code Phase [samples]")
    ax.set_ylabel("Doppler Frequency [Hz]")
    ax.set_title(f"Delay-Doppler Correlation Map for {acq_result.signal_id}")
    fig.colorbar(im, ax=ax, label="Correlation Magnitude")
    return ax


def plot_acquisition_correlation_histogram(
    fig: Figure | SubFigure,
    acq_result: bpsk_acquisition.AcquisitionResult,
    num_blocks: int,
    hist_max_val: float = 120.0,
) -> Axes:
    """
    Histogram of normalized correlation magnitudes for one signal, with the
    chi-squared distribution (df = 2 * num_blocks) that non-coherent
    square-law summation should follow under noise alone, and the detection
    threshold used to declare acquisition.

    Needs the *whole* search grid, so the result must come from an acquisition run
    with `save_corr_doppler_window_bins=None`.  A retained Doppler window is
    centred on the peak and is not a fair sample of the noise distribution.
    """
    ax = fig.add_subplot(1, 1, 1)
    corr_matrix = acq_result.correlation_result.correlation_matrix

    # nanmean/NaN-drop: a result kept with `save_corr_doppler_window_bins` NaN-fills
    # any Doppler row that fell outside the searched grid, and plain np.mean of an
    # array containing NaN is NaN.
    y_noise_mean = np.nanmean(corr_matrix)
    sigma_n = np.sqrt(y_noise_mean / (2 * num_blocks))
    normalized_corr_matrix = corr_matrix / sigma_n**2

    hist_bins = np.linspace(0, hist_max_val, 100)
    finite_values = normalized_corr_matrix[np.isfinite(normalized_corr_matrix)]
    hist = np.histogram(finite_values, bins=hist_bins)[0]

    ax.bar(hist_bins[:-1], hist, width=hist_bins[1] - hist_bins[0], color="blue", alpha=0.7)
    x_vals = np.linspace(0, np.nanmax(normalized_corr_matrix), 1000)
    chi2_pdf = scipy.stats.chi2.pdf(x_vals, df=2 * num_blocks)
    ax.plot(
        x_vals, chi2_pdf * np.max(hist) / np.max(chi2_pdf), color="red", linewidth=2,
        label=f"Chi-squared PDF (df={2 * num_blocks})",
    )
    ax.vlines([acq_result.detection_threshold], ymin=0, ymax=np.max(hist), color="black", linestyle="--", label="Detection Threshold")
    ax.legend()
    ax.set_yscale("log")
    ax.set_ylim(1, 1e6)
    ax.set_title(f"Histogram of Correlation Magnitudes for {acq_result.signal_id}")
    ax.set_xlabel("Correlation Magnitude")
    ax.set_ylabel("Count")
    ax.grid()
    ax.set_xlim(0, hist_max_val)
    return ax


def plot_acquisition_dwell_layout(
    fig: Figure | SubFigure,
    acq_config: bpsk_acquisition.AcquisitionConfiguration,
    symbol_period_ms: Optional[float] = None,
    symbol_phase_ms: float = 0.0,
) -> Sequence[Axes]:
    """
    Why acquisition has two lengths, drawn from the configuration itself.

    Left panel -- the dwell in time.  `num_blocks` coherent blocks of
    `coherent_duration_sample_ms` are taken back to back from the stream, and each
    is fitted to `coherent_duration_replica_ms` before its FFT (the correlation is
    circular, so the FFT length is the replica length).  Which way it is fitted
    depends on which length is longer: a block shorter than the replica is
    zero-padded, and a block longer than it is FOLDED -- its whole code periods
    summed onto one window, filling it with no padding at all.

    Each block sits at the offset it actually occupied within the code period --
    block `j` at `j * T_coherent` modulo `T_replica`, wrapping if it straddles the
    end -- not at the start of its window.  That is what `pack_coherent_blocks`
    does, and it is what makes the square-law sum accumulate: block `j` was
    received after the code had already advanced that far, so padding every block
    at position 0 would leave each peak at a different lag and smear the sum
    across the code phase axis instead of stacking it.

    If `symbol_period_ms` is given, data symbol boundaries are drawn across the
    window.  A sign flip *between* blocks is harmless because the blocks are
    combined by square law; one *inside* a block cancels part of that block's own
    integration.  `symbol_phase_ms` offsets that grid: acquisition does not know
    the symbol alignment, so the realistic picture is an arbitrary offset, and
    keeping blocks short is what bounds the damage whatever it turns out to be.

    Right panel -- the same two lengths in frequency.  The curve is the coherent
    response, whose mainlobe is `1 / T_coherent` wide.  Orange ticks are the grid
    the search actually steps on.

    Those two are not the same as the FFT's own bin spacing, `1 / T_replica`.
    Rolling the replica's spectrum by whole bins is what steps Doppler for free,
    so an unfolded search steps exactly one bin at a time.  Folding shortens the
    transform, which coarsens those bins by the fold factor while leaving the
    mainlobe -- set by how much data is integrated, not by the FFT length --
    exactly where it was.  The missing steps come back as sub-bin phase ramps
    applied before the fold, and the green ticks show which of the orange ones the
    FFT supplied.  Left unfilled, a 1 ms replica would search a 5 ms integration
    on a 1 kHz grid, and `sinc(500 Hz * 5 ms)` is -18 dB: a hole in every gap.
    """
    axes = fig.subplots(1, 2, width_ratios=[1.4, 1])
    ax_time: Axes = axes[0]
    ax_freq: Axes = axes[1]

    t_coh = float(acq_config.coherent_duration_sample_ms)
    t_rep = float(acq_config.coherent_duration_replica_ms)
    num_blocks = acq_config.num_blocks
    fold = getattr(acq_config, "fold_factor", 1)

    for m in range(num_blocks):
        y = num_blocks - 1 - m
        if fold > 1:
            # Folded: the block is `fold` whole code periods summed onto the
            # window, so it covers all of it and there is nothing to pad.  Its
            # offset is `m * t_coh` modulo `t_rep`, which is zero for every block
            # because `t_coh` is a whole multiple of `t_rep`.
            spans = [(0.0, t_rep)]
        else:
            # Where this block's data actually sits in the window, wrapping if it
            # straddles the end of the code period.
            start = (m * t_coh) % t_rep
            spans = [(start, min(t_coh, t_rep - start))]
            if start + t_coh > t_rep:
                spans.append((0.0, start + t_coh - t_rep))

        ax_time.broken_barh([(0.0, t_rep)], (y - 0.32, 0.64),
                            facecolors="lightgrey", edgecolor="k",
                            linewidth=0.5, hatch="//")
        ax_time.broken_barh(spans, (y - 0.32, 0.64),
                            facecolors="tab:blue", edgecolor="k", linewidth=0.5)
        label = f"{m}" if fold == 1 else f"{m}  ({fold} periods summed)"
        ax_time.text(spans[0][0] + spans[0][1] / 2, y, label, ha="center",
                     va="center", fontsize=8, color="white")

    # Boundaries are absolute in the window: it is one code period, and the blocks
    # have been placed back onto their true positions within it.  A symbol longer
    # than the window may simply not have one inside it -- which is the case once
    # folding makes the window a single code period -- so draw what falls in it
    # rather than assuming one does.
    edges = (
        np.arange(symbol_phase_ms % symbol_period_ms, t_rep, symbol_period_ms)
        if symbol_period_ms
        else np.zeros(0)
    )
    draw_symbols = len(edges) > 0
    if draw_symbols:
        ax_time.vlines(edges, -0.5, num_blocks - 0.5, color="tab:red", lw=2, zorder=3)

    ax_time.set_yticks(range(num_blocks))
    ax_time.set_yticklabels([f"{num_blocks - 1 - i}" for i in range(num_blocks)])
    ax_time.set_ylabel("Block")
    ax_time.set_xlabel("Time within the FFT window [ms]")
    ax_time.set_xlim(0, t_rep)
    folded_note = f", folded {fold}x" if fold > 1 else ""
    ax_time.set_title(
        f"Dwell: {num_blocks} x {t_coh:g} ms coherent, {t_rep:g} ms replica{folded_note}"
    )
    handles = [
        plt.Rectangle((0, 0), 1, 1, facecolor="tab:blue", edgecolor="k", label="data integrated"),
    ]
    if fold == 1:
        handles.append(plt.Rectangle((0, 0), 1, 1, facecolor="lightgrey", edgecolor="k",
                                     hatch="//", label="zero padding"))
    if draw_symbols:
        handles.append(plt.Line2D([0], [0], color="tab:red", lw=2,
                                  label=f"symbol boundary ({symbol_period_ms:g} ms)"))
    elif symbol_period_ms:
        # A 20 ms symbol cannot be drawn inside a 1 ms window, but the block it
        # can straddle is 5 ms long, and saying so is the whole point.
        handles.append(plt.Line2D([0], [0], color="none",
                                  label=f"symbol {symbol_period_ms:g} ms: none in this window"))
    ax_time.legend(handles=handles, fontsize=7, loc="upper right")

    # --- frequency ---
    # What the search actually steps, which is the FFT bin spacing divided by the
    # fold: whole-bin rolls coarsen with the shorter transform, and the sub-bin
    # ramps put the steps back.  Plotting `fft_resolution` here would claim a
    # 1 kHz grid for a search that is really stepping 200 Hz.
    grid_hz = getattr(acq_config, "doppler_step_hz", acq_config.fft_resolution)
    bin_hz = acq_config.fft_resolution
    response_hz = acq_config.doppler_response_width_hz
    span = 3 * response_hz
    f = np.linspace(-span, span, 1001)
    ax_freq.plot(f, np.abs(np.sinc(f / response_hz)), color="k", lw=2,
                 label=f"response, {response_hz:.0f} Hz wide")
    # Centred on the true value, not on the edge of the plotted span: these mark
    # where the search can land relative to truth, so a tick belongs at zero.
    def _centred(spacing: float) -> np.ndarray:
        k = int(np.floor(span / spacing))
        return np.arange(-k, k + 1) * spacing

    ax_freq.vlines(_centred(grid_hz), 0, 0.12, color="tab:orange", lw=1.5,
                   label=f"search grid, {grid_hz:.0f} Hz apart")
    if fold > 1:
        ax_freq.vlines(_centred(bin_hz), 0, 0.26, color="tab:green", lw=1.5,
                       label=f"FFT bins, {bin_hz:.0f} Hz ({fold} sub-bin ramps fill in)")
    ax_freq.set_xlabel("Doppler offset from the true value [Hz]")
    ax_freq.set_ylabel("Normalised correlation")
    ax_freq.set_title("Grid spacing vs response width")
    ax_freq.set_xlim(-span, span)
    ax_freq.set_ylim(0, 1.1)
    ax_freq.grid()
    ax_freq.legend(fontsize=7, loc="upper right")
    return axes


def plot_acquisition_code_phase_slices(
    fig: Figure | SubFigure,
    acq_results: dict[str, bpsk_acquisition.AcquisitionResult],
    signal_ids: Optional[Sequence[str]] = None,
    window_chips: float = 3.0,
) -> Axes:
    """
    Correlation power against code delay, one curve per signal, each taken at its
    own peak Doppler bin.

    **The axis is delay, increasing to the right**, so it reads the way the words
    do: a point to the *left* of the peak is a replica that arrives earlier than the
    signal (an early correlator), one to the *right* arrives later (late), and a
    reflection -- always later than the direct path -- adds energy on the right.

    That takes a negation.  The correlation grid is indexed by code phase, which
    runs the other way: a signal delayed by `d` chips peaks at index `L - d` of an
    `L`-chip code, so plotting the index directly puts early on the right and
    multipath on the left.  The two are easy to conflate because they differ only
    in sign, and nothing about a single curve reveals which one is being drawn --
    which is why `tests/test_acquisition.py` pins the direction with an injected
    echo rather than leaving it to inspection.

    This is the view for reading **multipath**, which lives in the delay dimension:
    a reflection is always delayed relative to the direct path, so it shows up as
    asymmetry about zero -- a shoulder on the late (right) side, a broadened or
    flattened peak -- rather than as a change in peak height.

    Delay is plotted relative to each signal's own peak, so the curves are
    comparable; absolute code phase is in the acquisition table.  Power is in dB
    above the expected noise level, the same scale as `AcquisitionResult.
    peak_snr_db` and that table, so every noise floor sits at 0 dB and every peak
    reads its own SNR.  The detection threshold is drawn as one horizontal line --
    it depends only on the per-cell false-alarm rate and `num_blocks`, so it is a
    property of the sweep rather than of any signal.

    A healthy dwell is a sharp peak at delay zero, far above the line, falling to a
    flat floor within about a chip either side.  A false acquisition is a low,
    shapeless bump that barely clears it.

    Resolution is set by the sample rate, and it is coarse: at 22 Msps against a
    10.23 Mcps code there are only 2.15 samples per chip -- one sample is 45.5 ns,
    13.6 m, 0.465 chips, and the ideal +/-1 chip correlation triangle spans just
    4.3 samples.  Multipath appears as asymmetry across a handful of points, not as
    a smoothly resolved shoulder.  Anything finer needs a higher sample rate or an
    interpolated peak.

    **Do not read asymmetry as multipath without checking the sign.**  At a couple
    of samples per chip the true peak almost never lands on a sample, so its two
    neighbours are unequal purely from where the sampling grid happened to fall --
    and that lands either way at random, differing in sign from one satellite to
    the next.  Multipath is a *delayed* reflection, so it skews late consistently,
    across satellites and over time.  A single dwell cannot separate the two; a run
    of dwells can.

    Signals are taken from `acq_results` by default only where `signal_detected` is
    set; elsewhere the "peak" is a noise maximum whose neighbourhood means nothing.
    """
    if signal_ids is None:
        signal_ids = sorted(sid for sid, r in acq_results.items() if r.signal_detected)

    ax = fig.add_subplot(1, 1, 1)
    if not signal_ids:
        ax.set_title("No signals acquired")
        return ax

    def _slice(result, corr, peak_row, reference_seconds):
        """One Doppler row of `corr`, in dB above noise, against delay in chips."""
        power = corr.correlation_matrix[peak_row]
        with np.errstate(divide="ignore", invalid="ignore"):
            power_db = 10.0 * np.log10(
                power / result.noise_var / (2 * result.config.num_blocks)
            )
        # NEGATED, and this is the whole reason the axis reads as delay.  The
        # correlation index is a code phase, and code phase runs *opposite* to
        # delay: a signal arriving `d` chips later peaks at index `L - d` of an
        # `L`-chip code.  Plotted raw, a late reflection would appear to the LEFT
        # of the direct path and an early correlator to the right -- both backwards
        # from every description of what this figure is for.  Verified rather than
        # derived: inject a 0.6-chip echo and it lands at +0.6 here.
        delay_chips = (
            reference_seconds - corr.code_phase_bins_seconds
        ) * result.acquisition_code_rate_chips_per_sec
        keep = np.abs(delay_chips) <= window_chips
        order = np.argsort(delay_chips[keep])
        return delay_chips[keep][order], power_db[keep][order]

    any_fine = False
    for index, signal_id in enumerate(signal_ids):
        result = acq_results[signal_id]
        colour = f"C{index}"
        fine = result.fine_correlation_result

        # Both curves are referenced to the FINE peak, so the coarse curve's own
        # displacement from zero is visible -- that offset is exactly what the
        # refinement corrected, and hiding it by giving each curve its own origin
        # would throw away the most informative thing in the figure.
        #
        # The *unwrapped* fine peak is the reference: the fine delay axis is a local
        # axis that may run past the code period, and `acq_code_phase_seconds` is
        # wrapped, so mixing the two would offset the coarse curve by a whole period
        # near the boundary.
        if fine is not None:
            reference_seconds = float(
                fine.code_phase_bins_seconds[result.fine_peak_code_phase_bin]
            )
        else:
            reference_seconds = result.coarse_code_phase_seconds

        # The retained coarse window is centred on the coarse peak and NaN-filled
        # where it overhangs the grid, so the peak is its middle row by
        # construction.  Verified rather than assumed: a silent off-by-one would
        # plot a neighbouring Doppler bin and understate the whole curve.
        coarse_row = result.correlation_result.correlation_matrix.shape[0] // 2
        if not np.isclose(
            result.correlation_result.doppler_bins_hz[coarse_row], result.coarse_doppler_hz
        ):
            raise ValueError(
                f"{signal_id}: coarse row {coarse_row} is "
                f"{result.correlation_result.doppler_bins_hz[coarse_row]:.1f} Hz but the coarse "
                f"peak is at {result.coarse_doppler_hz:.1f} Hz; the retained Doppler window is "
                "not centred on the peak"
            )

        x, y = _slice(result, result.correlation_result, coarse_row, reference_seconds)
        # Faded, same colour and marker: the eye should read the pair as one signal
        # measured two ways, not as two signals.
        ax.plot(x, y, ".-", color=colour, alpha=0.35, markersize=6, lw=1.2)

        if fine is not None:
            any_fine = True
            if not np.isclose(
                fine.doppler_bins_hz[result.fine_peak_doppler_bin], result.acq_doppler_hz
            ):
                raise ValueError(
                    f"{signal_id}: fine row {result.fine_peak_doppler_bin} is "
                    f"{fine.doppler_bins_hz[result.fine_peak_doppler_bin]:.1f} Hz but the refined "
                    f"peak is at {result.acq_doppler_hz:.1f} Hz"
                )
            x, y = _slice(result, fine, result.fine_peak_doppler_bin, reference_seconds)
            ax.plot(x, y, ".-", color=colour, alpha=1.0, markersize=6, lw=1.6)

        snr = result.peak_snr_db
        fine_snr = result.fine_peak_snr_db
        label = f"{signal_id}  {snr:.1f} dB"
        if fine_snr is not None:
            label = f"{signal_id}  {snr:.1f} => {fine_snr:.1f} dB"
        ax.plot([], [], ".-", color=colour, markersize=6, label=label)

    threshold_db = acq_results[signal_ids[0]].detection_threshold_db
    ax.axhline(threshold_db, color="k", ls="--", lw=1.5,
               label=f"detection threshold ({threshold_db:.2f} dB)")
    if any_fine:
        ax.plot([], [], ".-", color="0.4", alpha=0.35, markersize=6, label="faded: coarse")
        ax.plot([], [], ".-", color="0.4", alpha=1.0, markersize=6, label="solid: fine")

    # Noise cells scatter several dB below the 0 dB mean, so clamp the floor rather
    # than letting one deep null squash every curve.
    ax.set_ylim(bottom=max(-12.0, ax.get_ylim()[0]))
    ax.set_xlabel("Code delay relative to the refined peak [chips]")
    ax.set_ylabel("Correlation power [dB above noise]")
    # Which way is which, at the ends of the axis rather than in the caption: the
    # sign of this axis is the one thing about the figure that cannot be read off
    # it.  Above the frame, so they never collide with the legend or the curves,
    # with the title padded up to make room.
    ax.set_title("Acquisition correlation vs code delay", pad=18)
    for x, ha, text in (
        (0.0, "left", "\u2190 early: replica ahead, shorter delay"),
        (1.0, "right", "late: replica behind, longer delay (multipath) \u2192"),
    ):
        ax.text(x, 1.01, text, transform=ax.transAxes, ha=ha, va="bottom",
                fontsize=7.5, color="0.35")
    ax.grid(True)
    ax.legend(fontsize=8, ncol=1, loc="upper right", bbox_to_anchor=(1.0, 1.0))
    return ax


# --- tracking-result diagnostics --------------------------------------------

def corr_component(corr: np.ndarray, index: int = 0) -> np.ndarray:
    """
    Pick one code component out of a correlator output array.

    Correlator outputs are (epochs, components) so that multi-component signals
    such as L2C/L5 keep their components separate. Results pickled before that
    change are 1-D, so they are passed through unchanged.
    """
    return corr[:, index] if np.ndim(corr) == 2 else corr


def plot_prompt_iq_grid(
    fig: Figure | SubFigure,
    tracking_outputs: dict[str, tracking_channel.SignalTrackingOutputs],
    sig_ids: Optional[Sequence[str]] = None,
    title: str = "",
) -> Sequence[Axes]:
    """One row per signal of prompt I (red) / Q (blue) scatter vs. uptime."""
    if sig_ids is None:
        sig_ids = sorted(tracking_outputs.keys())
    axes = np.atleast_1d(fig.subplots(len(sig_ids), 1, sharex=True, sharey=True))
    for i, sig_id in enumerate(sig_ids):
        tracking_output = tracking_outputs[sig_id]
        valid = tracking_output.valid
        prompt = corr_component(tracking_output.prompt_corr)[valid]
        plot_time = tracking_output.uptime_epoch_ms[valid] * 1e-3

        ax: Axes = axes[i]
        ax.scatter(plot_time, prompt.real, s=1, color="tab:red")
        ax.scatter(plot_time, prompt.imag, s=1, color="tab:blue")
        ax.grid(True)
        ax.set_ylabel(f"{sig_id}", fontsize=22)

    if title:
        axes[0].set_title(title)
    axes[-1].set_xlabel("Uptime [seconds]")
    handles = [
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor='tab:red', markersize=5, label='I'),
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor='tab:blue', markersize=5, label='Q'),
    ]
    axes[0].legend(handles=handles, loc='upper right', markerscale=3)
    return axes


def plot_carrier_phase_doppler_comparison(
    fig: Figure | SubFigure,
    tracking_outputs_by_version: dict[str, dict[str, tracking_channel.SignalTrackingOutputs]],
    loop_params_by_version: dict[str, tracking_channel.TrackingLoopParameters],
    sig_id: str,
    version_ids: Optional[Sequence[str]] = None,
) -> Sequence[Axes]:
    """
    Compare carrier-phase error, Doppler, and detrended carrier phase for one
    signal across several tracking-loop parameter "versions", overlaid with a
    shared color scale (useful for e.g. sweeping PLL bandwidth).
    """
    if version_ids is None:
        version_ids = sorted(tracking_outputs_by_version.keys())
    axes = fig.subplots(3, 1, sharex=True)
    cmap = plt.get_cmap("viridis")

    for i, version_id in enumerate(version_ids[::-1]):
        loop_params = loop_params_by_version[version_id]
        tracking_output = tracking_outputs_by_version[version_id][sig_id]

        valid = tracking_output.valid
        plot_time = tracking_output.uptime_epoch_ms[valid][:-1] * 1e-3
        carr_phase_errors_cycles = tracking_output.carr_phase_errors_cycles[valid][:-1]
        doppler_freq_hz = tracking_output.doppler_freq_hz[valid][:-1]
        carr_phase_cycles = tracking_output.carr_phase_cycles[valid][:-1]

        ave_doppler_hz = np.mean(doppler_freq_hz)
        detr_carr_phase_cycles = carr_phase_cycles - ave_doppler_hz * plot_time
        detr_carr_phase_cycles -= detr_carr_phase_cycles[0]

        color = cmap(i / len(version_ids))

        axes[0].plot(plot_time, carr_phase_errors_cycles, color=color, lw=3)
        axes[1].plot(plot_time, doppler_freq_hz, color=color, lw=3)
        axes[2].plot(plot_time, detr_carr_phase_cycles, color=color, lw=3)

    axes[0].set_ylabel("Carrier Phase\nError [cycles]")
    axes[0].set_title(f"Tracking Results for Signal {sig_id}")
    axes[1].set_ylabel("Doppler\nFrequency [Hz]")
    axes[2].set_ylabel("Detrended Carrier\nPhase [cycles]")
    axes[2].set_xlabel("Uptime [seconds]")
    for ax in axes:
        ax.grid()

    handles = [
        plt.Line2D(
            [0], [0], color=cmap(i / len(version_ids)), lw=5,
            label=f"PLL BW: {loop_params_by_version[version_id].PLL_bandwidth_hz} Hz",
        )
        for i, version_id in enumerate(version_ids)
    ]
    axes[0].legend(handles=handles, loc="upper right", framealpha=1)
    fig.align_labels()
    return axes


def plot_epl_magnitude_and_code_error(
    fig: Figure | SubFigure,
    tracking_output: tracking_channel.SignalTrackingOutputs,
    sig_id: str,
) -> Sequence[Axes]:
    """Early/Prompt/Late correlation magnitude and code-phase error vs. uptime for one signal."""
    axes = fig.subplots(2, 1, sharex=True)

    valid = tracking_output.valid
    plot_time = tracking_output.uptime_epoch_ms[valid][:-1] * 1e-3
    early = corr_component(tracking_output.early_corr)[valid][:-1]
    prompt = corr_component(tracking_output.prompt_corr)[valid][:-1]
    late = corr_component(tracking_output.late_corr)[valid][:-1]
    code_phase_errors_chips = tracking_output.code_phase_errors_chips[valid][:-1]

    axes[0].scatter(plot_time, np.abs(early), color="g", s=5)
    axes[0].scatter(plot_time, np.abs(prompt), color="b", s=5)
    axes[0].scatter(plot_time, np.abs(late), color="r", s=5)
    axes[0].set_ylabel("EPL Magnitude")
    axes[0].set_title(f"EPL Magnitudes and Code Phase Error for Signal {sig_id}")
    axes[0].legend(["Early", "Prompt", "Late"], markerscale=10, loc="upper right")

    axes[1].plot(plot_time, code_phase_errors_chips, color=plt.get_cmap("viridis")(0.5), lw=3)
    axes[1].set_ylabel("Code Phase\nError [chips]")
    axes[1].set_xlabel("Uptime [seconds]")

    for ax in axes:
        ax.grid()
    fig.align_labels()
    return axes


def plot_component_prompt_magnitudes(
    fig: Figure | SubFigure,
    adapter: "TrackingChannelAdapter",
    sig_id: str,
    title: Optional[str] = None,
) -> Axes:
    """
    Prompt correlation magnitude of every code component of a multi-component
    signal, on one set of axes. Meaningless for a single-component signal such as
    L1 C/A, so callers should skip it when `len(component_names) == 1`.

    What to expect:
      - L5: I and Q carry equal power, so the two traces should sit on top of each
        other. A large gap means one component is not being tracked properly.
      - L2C: CM and CL each transmit on only half the chip slots, so both sit near
        half the magnitude a single full-rate code would reach -- and they should
        be roughly equal to each other.

    Magnitude is used rather than I/Q because it is insensitive to carrier phase:
    it answers "how much signal power is this component recovering", not "is the
    phase right".
    """
    ax = fig.add_subplot(1, 1, 1)
    outputs = adapter.outputs
    plot_time = outputs.uptime_epoch_ms[outputs.valid] * 1e-3
    for index, name in enumerate(adapter.signal.component_names):
        prompt = adapter.get_prompt_component(component=index)
        ax.scatter(plot_time, np.abs(prompt), s=2, label=f"{name}", color=f"C{index}")
    ax.set_title(title if title is not None else f"Components: {sig_id}")
    ax.set_ylabel("Prompt Magnitude")
    ax.set_xlabel("Uptime [s]")
    ax.grid(True)
    ax.legend(markerscale=10)
    return ax


def plot_prompt_components(
    fig: Figure | SubFigure,
    adapter: "TrackingChannelAdapter",
    sig_id: str,
    title: Optional[str] = None,
) -> Sequence[Axes]:
    """
    Prompt correlator output per code component, one row each, in-phase and
    quadrature together.

    A locked channel puts essentially all of each component's power on *one* axis
    and leaves the other at zero.  Which axis depends on the component's carrier
    phase relative to the one the loop is tracking:

      - the component the carrier loop runs on lands on I, because that is what the
        PLL is driving it to do;
      - a component transmitted in phase quadrature with it lands on Q.  GPS L5 is
        exactly this case -- I and Q ride quadrature carriers, and with the loop on
        the Q pilot the L5I component's power appears in the *imaginary* part;
      - components sharing a carrier phase (L2C's CM and CL) both land on I.

    How many bands that axis forms says what the component carries: a data
    component (L1 C/A's CA, L2C's CM, L5's I) splits into a positive and a negative
    band as navigation symbols flip its sign, while a dataless pilot (L5's Q, L2C's
    CL once resolved) stays in a single band.

    Loss of lock looks like I and Q both scattered symmetrically about zero.  Since
    every component shares one epoch, a component whose magnitude collapses on a
    subset of epochs while its siblings are healthy is straddling its own symbol
    boundary -- see `utils.tracking_channel`'s epoch anchoring.
    """
    outputs = adapter.outputs
    names = adapter.signal.component_names
    plot_time = outputs.uptime_epoch_ms[outputs.valid] * 1e-3

    axes = np.atleast_1d(fig.subplots(len(names), 1, sharex=True))
    for index, name in enumerate(names):
        prompt = adapter.get_prompt_component(component=index)
        ax: Axes = axes[index]
        ax.scatter(plot_time, prompt.real, s=2, color="tab:red", label="In-phase (I)")
        ax.scatter(plot_time, prompt.imag, s=2, color="tab:blue", label="Quadrature (Q)")
        ax.axhline(0.0, color="k", lw=0.5)
        ax.set_ylabel(f"{name}\nPrompt")
        ax.grid(True)
    axes[0].set_title(title if title is not None else f"Prompt correlators: {sig_id}")
    axes[0].legend(markerscale=8, loc="upper right", fontsize=8)
    axes[-1].set_xlabel("Uptime [s]")
    fig.align_labels()
    return axes


def _detrend(x: np.ndarray, y: np.ndarray, order: Optional[int]) -> np.ndarray:
    """Subtract a least-squares polynomial fit of the given order from y(x)."""
    if order is None or len(x) <= order:
        return y
    trend = np.polyval(np.polyfit(x, y, order), x)
    return y - trend


def plot_code_delay_and_doppler(
    fig: Figure | SubFigure,
    adapter: "TrackingChannelAdapter",
    sig_id: str,
    title: Optional[str] = None,
    code_delay_detrend_order: Optional[int] = 2,
    doppler_detrend_order: Optional[int] = 1,
    ambiguity_ms: Optional[float] = None,
) -> Axes:
    """
    Code delay and carrier Doppler on shared axes, as the line-of-sight dynamics.

    **Code phase and code delay are different quantities**, and the difference is
    the whole content of this docstring.  `code_phase_ms` is what tracking reports:
    the satellite's own transmit-time coordinate, advancing at very nearly one
    millisecond per millisecond of receiver time.  The **code delay** is

        code delay = uptime - code phase

    -- receiver time minus transmit time, which is the transit time plus the
    receiver clock offset, i.e. a pseudorange in milliseconds.  Two consequences
    worth holding on to: the two run in *opposite* directions as range changes,
    because a satellite moving away makes its transmit-time coordinate fall further
    behind receiver time; and the delay is tiny beside either of them, which is why
    the raw difference has to be plotted as a residual to show anything.

    **The delay is ambiguous, and negative without the ambiguity added back.**
    Acquisition seeds the code phase at the phase it measured, so `uptime -
    code_phase` starts at exactly minus that -- and the seed is only known modulo
    the acquired code's period.  Pass `ambiguity_ms`
    (`bpsk_acquisition.code_phase_ambiguity_ms`) and the reported starting delay is
    wrapped into `[0, ambiguity_ms)`, which is what makes it a delay rather than a
    negative number.  Only the annotation is wrapped; the plotted curve is the
    unwrapped, continuous difference, because a delay drifting across the modulus
    mid-run would otherwise jump by a whole period.

    The curve is the *residual*: the delay referenced to its first epoch, so what
    is left is the part that reflects the satellite actually moving, reported as an
    equivalent range in metres (`c * delay`; the right-hand axis carries Doppler in
    Hz).

    The two are related by construction, not independently measured: this tracker
    slaves the code rate to the carrier, `code_rate = (1 + doppler / f_carrier)`,
    so the delay residual is the *negative* integral of Doppler over the carrier
    frequency -- approaching (positive Doppler) shrinks the delay, receding
    (negative Doppler) grows it.  What the plot is good for is seeing that dynamic
    directly -- a steady Doppler of a few kHz produces a delay ramp of a fraction
    of a chip per second -- and seeing both break together when a channel loses
    lock.

    Over a short pass the line-of-sight range accelerates roughly steadily, so the
    code delay residual (its integral) is dominated by a quadratic trend and the
    Doppler (its derivative) by a linear one -- both of which swamp the finer
    structure on the plot. `code_delay_detrend_order` and `doppler_detrend_order`
    remove a least-squares polynomial fit of that order before plotting (default
    2nd and 1st order respectively); pass `None` for either to plot the raw
    residual/Doppler instead.
    """
    outputs = adapter.outputs
    valid = outputs.valid
    plot_time = outputs.uptime_epoch_ms[valid] * 1e-3
    doppler_freq_hz = _detrend(plot_time, outputs.doppler_freq_hz[valid], doppler_detrend_order)

    # Code phase accumulates without wrapping and runs behind uptime by the transit
    # delay (same convention as the receive_time - transmit_time pseudorange in
    # observables.py), so this grows with range; subtracting the first value removes
    # the nominal rate and leaves the departure caused by the satellite's own motion.
    #
    # Unwrapped on purpose -- see the docstring.  Wrapping here would put a
    # whole-period step in the middle of any run whose delay crosses the modulus.
    delay_ms = bpsk_acquisition.code_delay_ms(
        outputs.uptime_epoch_ms[valid], outputs.code_phase_ms[valid]
    )
    residual_ms = delay_ms - delay_ms[0] if len(delay_ms) else delay_ms
    residual_m = residual_ms * 1e-3 * speed_of_light
    residual_m = _detrend(plot_time, residual_m, code_delay_detrend_order)

    ax = fig.add_subplot(1, 1, 1)
    ax.plot(plot_time, residual_m, color="tab:purple", lw=2, label="Code delay")
    code_delay_label = "Code delay [m]"
    if code_delay_detrend_order is not None:
        code_delay_label += f" (order-{code_delay_detrend_order} detrended)"
    ax.set_ylabel(code_delay_label, color="tab:purple")
    ax.tick_params(axis="y", labelcolor="tab:purple")
    ax.set_xlabel("Uptime [s]")

    ax_doppler = ax.twinx()
    ax_doppler.plot(plot_time, doppler_freq_hz, color="tab:green", lw=2, label="Doppler")
    doppler_label = "Doppler [Hz]"
    if doppler_detrend_order is not None:
        doppler_label += f" (order-{doppler_detrend_order} detrended)"
    ax_doppler.set_ylabel(doppler_label, color="tab:green")
    ax_doppler.tick_params(axis="y", labelcolor="tab:green")
    doppler_freq_bounds = tuple(np.percentile(doppler_freq_hz[np.min([100, doppler_freq_hz.size]):], [0.1, 99.9]))
    doppler_freq_bounds = doppler_freq_bounds + np.array([-1, 1]) * 1.5 * np.ptp(doppler_freq_bounds)
    ax_doppler.set_ylim(doppler_freq_bounds)

    # The two lines live in different Axes, and a line's zorder only ranks it
    # against artists in its own Axes; across Axes what counts is the Axes zorder,
    # which puts the twin (added last) on top.  Raise the code delay Axes instead,
    # and hand its now-covering background patch back to the twin.  The grid goes
    # on the lower Axes so it stays under both lines rather than over the Doppler.
    ax.set_zorder(ax_doppler.get_zorder() + 1)
    ax.patch.set_visible(False)
    ax_doppler.patch.set_visible(True)
    ax_doppler.grid(True)

    ax.set_title(title if title is not None else f"Code delay and Doppler: {sig_id}")
    handles = [
        plt.Line2D([0], [0], color="tab:purple", lw=2, label="Code delay residual"),
        plt.Line2D([0], [0], color="tab:green", lw=2, label="Doppler"),
    ]
    # The absolute delay the residual was taken from, which the residual itself
    # cannot show.  Stated once, at the first epoch, with the modulus it is only
    # known to -- a reader who does not know the delay is ambiguous will otherwise
    # read this number as a transit time, and on most signals it is not one.
    if ambiguity_ms and len(delay_ms):
        start_ms = float(
            bpsk_acquisition.code_delay_ms(
                outputs.uptime_epoch_ms[valid][0],
                outputs.code_phase_ms[valid][0],
                ambiguity_ms,
            )
        )
        handles.append(plt.Line2D(
            [0], [0], color="none",
            label=f"delay at t=0: {start_ms:.3f} ms  (mod {ambiguity_ms:g} ms)",
        ))
    ax.legend(handles=handles, loc="best", fontsize=8)
    return ax


# Minimum height of the C/N0 axis, in dB.  A locked channel's C/N0 varies by about
# a dB, and letting matplotlib autoscale to that makes estimator noise look like a
# fading signal.  Ten dB is wide enough that flat reads as flat and a real fade
# still shows.
MIN_CN0_AXIS_SPAN_DB = 10.0


def plot_prompt_circ_length(
    fig: Figure | SubFigure,
    adapter: "TrackingChannelAdapter",
    sig_id: str,
    title: Optional[str] = None,
    component: Optional[int] = None,
) -> Axes:
    """
    Prompt circular length and VSM C/N0 -- two quantities on one pair of axes,
    despite what the name says: coherence on the left, C/N0 in dB-Hz on the right.

    Circular length per epoch, coloured by which carrier loop was running.

    Circular length is the coherence of the recent prompt history: the magnitude of
    the mean unit phasor, wrapped for Costas where the policy says so.  It is ~1
    when the prompt phase is steady and falls towards 0 as it scatters, which makes
    it the statistic the channel uses to decide the FLL has pulled the frequency
    error in far enough for the PLL to take over.

    Epochs filtered by the FLL and by the PLL are drawn in different colours, with
    the switching threshold and the handover instant both marked.

    **A dip just after the handover is expected, and is not a loss of signal.**
    The FLL controls frequency only, so it hands over holding whatever carrier
    phase error it happens to have -- a third of a cycle is typical.  The PLL then
    drives that to zero over the next ten or so epochs, and it is the *sweep* of
    phase during that correction, not any drop in power, that spreads the history's
    phasors and pulls this metric down.  Prompt magnitude is flat throughout;
    `plot_prompt_components` is where a real power loss would show.

    Two consequences.  The dip's depth scales with the phase error the FLL left.
    And it **lags the handover by up to `history_size` epochs**, because this is a
    sliding window: it bottoms once the window is maximally filled with sweep-era
    phases and recovers as they scroll out.  Read the bottom of the dip as "the
    correction finished about ten epochs ago", not as something happening then.

    The threshold rarely gates anything for a healthy signal, whose coherence is
    already above it at the first epoch; the binding condition is `history_filled`,
    so the FLL runs for exactly `history_size` epochs and then hands over.  A
    channel that never leaves FLL never locked at all, and one sagging back towards
    the threshold later in a run is about to lose lock.

    **C/N0** comes from `estimate_cn0_vsm` over correlation intervals, on a far
    slower cadence than the epochs -- one point per hop of the estimator's window,
    so a run shorter than a couple of periods shows almost nothing.  It is drawn
    for one component, the carrier loop's by default; pass `component` to compare
    I against Q.  Unlike the coherence trace it should be flat for a locked
    channel: it measures how much signal is arriving, not how well the loop holds
    phase, so it does *not* dip at the FLL/PLL handover.
    """
    outputs = adapter.outputs
    valid = outputs.valid
    plot_time = outputs.uptime_epoch_ms[valid] * 1e-3
    circ_length = outputs.prompt_corr_circ_length[valid]
    pll = outputs.pll_mode[valid]

    ax = fig.add_subplot(1, 1, 1)
    ax.scatter(plot_time[~pll], circ_length[~pll], s=4, color="tab:orange", label="FLL")
    ax.scatter(plot_time[pll], circ_length[pll], s=4, color="tab:blue", label="PLL")

    threshold = adapter.channel.loop_params.prompt_corr_circ_length_threshold
    ax.axhline(threshold, color="k", ls="--", lw=1.5,
               label=f"FLL -> PLL threshold ({threshold:g})")

    # On a strong signal the FLL stretch can be only a handful of epochs wide and
    # all but invisible as scattered points, so mark the handover explicitly.
    if pll.any() and not pll.all():
        handover_s = float(plot_time[np.argmax(pll)])
        ax.axvline(handover_s, color="k", ls=":", lw=1.5)
        ax.annotate(f"FLL -> PLL at {handover_s:.3f} s",
                    xy=(handover_s, 0.5), xytext=(6, 0), textcoords="offset points",
                    rotation=90, va="center", fontsize=8)

    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Prompt circular length", color="tab:blue")
    ax.tick_params(axis="y", labelcolor="tab:blue")
    ax.set_xlabel("Uptime [s]")
    ax.set_title(title if title is not None else f"Carrier loop coherence: {sig_id}")
    ax.grid(True)
    handles = ax.get_legend_handles_labels()[0]

    # C/N0 on its own axis, and its own much slower cadence.
    if component is None:
        component = adapter.channel.policy.carrier_component
    cn0_slice = outputs.cn0_valid
    if cn0_slice.stop > 0:
        name = adapter.signal.component_names[component]
        ax_cn0 = ax.twinx()
        line, = ax_cn0.plot(
            outputs.cn0_uptime_ms[cn0_slice] * 1e-3,
            outputs.cn0_dbhz[cn0_slice, component],
            marker="o", markersize=4, lw=1.5, color="tab:green",
            label=f"C/N0 ({name})",
        )
        ax_cn0.set_ylabel("C/N0 [dB-Hz]", color="tab:green")
        ax_cn0.tick_params(axis="y", labelcolor="tab:green")

        # Hold a minimum span, or autoscale magnifies a steady channel's ~1 dB of
        # estimator noise to fill the axis and it reads as a dramatic swing.
        values = outputs.cn0_dbhz[cn0_slice, component]
        finite = values[np.isfinite(values)]
        if finite.size:
            centre = 0.5 * (finite.min() + finite.max())
            half_span = max(0.5 * (finite.max() - finite.min()) * 1.2, MIN_CN0_AXIS_SPAN_DB / 2)
            ax_cn0.set_ylim(centre - half_span, centre + half_span)
        handles.append(line)

    ax.legend(handles=handles, markerscale=4, loc="lower right", fontsize=8)
    return ax


# ===========================================================================
# Navigation solution
#
# These five read a `utils.navigation.SolutionSeries` (and a skyplot's geometry)
# rather than a tracking channel, so they sit apart from everything above.
# ===========================================================================


# Below this the troposphere is poorly modelled, the multipath is worse, and the
# geometry gain does not make up for it.  Drawn on the skyplot so a marginal
# satellite is visibly marginal rather than just low.
DEFAULT_ELEVATION_MASK_DEG = 10.0


def plot_skyplot(
    fig: Figure | SubFigure,
    azimuth_deg: dict[str, np.ndarray],
    elevation_deg: dict[str, np.ndarray],
    *,
    cn0_dbhz: Optional[dict[str, float]] = None,
    tracked: Optional[Iterable[str]] = None,
    mask_deg: float = DEFAULT_ELEVATION_MASK_DEG,
    title: Optional[str] = None,
) -> Axes:
    """
    Where the satellites were, in the receiver's own sky.

    Each entry of `azimuth_deg`/`elevation_deg` is that satellite's track over the
    collect -- usually a short arc, since a GPS satellite moves a few degrees in a
    minute.  Passing a single value per satellite works too and draws a point.

    `tracked` distinguishes the satellites the receiver actually acquired from the
    ones that were merely up.  That contrast is the most useful thing on the plot:
    it turns "we got five satellites" into a statement about the front end, since
    the ones that were missed are almost always the low ones.

    Elevation is drawn increasing *inward*, which is the convention: the zenith is
    the centre of the sky, not its edge.

    `mask_deg` draws an elevation mask -- a dashed ring with the band below it
    shaded.  It is annotation only: nothing here or downstream drops a satellite
    for being under it, and the fix in `utils.navigation` uses every satellite it
    is given.  Its job is to make a low satellite obvious, which matters most on a
    horizon-pointed antenna, where the low ones are the strong ones.
    """
    ax = fig.add_subplot(1, 1, 1, projection="polar")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)  # azimuth runs clockwise from north
    # `set_rlim(90, 0)` is what puts the zenith at the centre, and it is the ONLY
    # reversal there should be: the radius a satellite is drawn at is its elevation
    # unchanged, so a ring at r = 15 is the 15 degree ring and must be labelled 15.
    # Reversing the labels as well inverted them against the data -- the centre read
    # "0" while an 80 degree satellite sat on it -- so the labels are the ticks.
    ax.set_rlim(90, 0)          # zenith at the centre
    ax.set_rgrids([0, 15, 30, 45, 60, 75, 90])
    ax.set_xticks(np.deg2rad([0, 45, 90, 135, 180, 225, 270, 315]))
    ax.set_xticklabels(["N", "NE", "E", "SE", "S", "SW", "W", "NW"])

    if mask_deg > 0:
        # The band BELOW the mask, which is the part being called into question --
        # shading the sky above it instead tinted everything the mask accepts and
        # left the low satellites, the ones a mask exists to flag, on clear ground.
        ax.fill_between(
            np.linspace(0, 2 * np.pi, 181),
            0,
            mask_deg,
            color="tab:red",
            alpha=0.06,
            zorder=0,
        )
        ax.plot(np.linspace(0, 2 * np.pi, 181), np.full(181, mask_deg),
                color="tab:red", lw=1, ls="--", alpha=0.5, zorder=1)

    tracked = set(tracked) if tracked is not None else set(azimuth_deg)
    values = [cn0_dbhz[s] for s in cn0_dbhz or {} if np.isfinite(cn0_dbhz[s])]
    norm = None
    if values:
        norm = plt.Normalize(vmin=min(values), vmax=max(values))

    scatter = None
    for sat_id in sorted(azimuth_deg):
        az = np.deg2rad(np.atleast_1d(azimuth_deg[sat_id]))
        el = np.atleast_1d(elevation_deg[sat_id])
        above = el > 0
        if not above.any():
            continue
        is_tracked = sat_id in tracked

        if len(az) > 1:
            ax.plot(az[above], el[above],
                    color="tab:blue" if is_tracked else "gray",
                    lw=1.5 if is_tracked else 0.8,
                    alpha=0.9 if is_tracked else 0.4, zorder=2)

        end_az, end_el = az[above][-1], el[above][-1]
        if is_tracked and cn0_dbhz and np.isfinite(cn0_dbhz.get(sat_id, np.nan)):
            scatter = ax.scatter(end_az, end_el, c=[cn0_dbhz[sat_id]], cmap="viridis",
                                 norm=norm, s=90, edgecolors="k", linewidths=0.6, zorder=3)
        else:
            ax.scatter(end_az, end_el,
                       color="tab:blue" if is_tracked else "white",
                       edgecolors="k" if is_tracked else "gray",
                       s=90 if is_tracked else 55, linewidths=0.6,
                       zorder=3 if is_tracked else 2)
        ax.annotate(sat_id, (end_az, end_el), textcoords="offset points",
                    xytext=(8, 6), fontsize=8,
                    color="black" if is_tracked else "gray")

    if scatter is not None:
        fig.colorbar(scatter, ax=ax, pad=0.1, shrink=0.75, label="C/N0 [dB-Hz]")

    ax.set_title(title if title is not None else "Sky view")
    return ax


def plot_position_enu(
    fig: Figure | SubFigure,
    enu_m: np.ndarray,
    time_s: Optional[np.ndarray] = None,
    *,
    title: Optional[str] = None,
) -> Axes:
    """
    The position solution: east/north scatter beside the three components in time.

    The scatter carries a one-sigma error ellipse from the sample covariance, which
    is the honest way to summarise a cloud that is almost never circular -- GPS
    geometry couples east and north differently depending on which satellites are
    up.

    Height is plotted with the horizontal components rather than on its own so its
    larger scatter is visible next to them.  A ground receiver's vertical error is
    reliably two to three times its horizontal error, because every satellite is
    above the antenna and none below.
    """
    enu_m = np.atleast_2d(enu_m)
    good = np.isfinite(enu_m[:, 0])
    east, north, up = enu_m[good, 0], enu_m[good, 1], enu_m[good, 2]

    axes = fig.subplots(1, 2, width_ratios=[1.0, 1.4])
    ax_scatter, ax_time = axes

    if len(east):
        ax_scatter.scatter(east, north, s=12, alpha=0.5, color="tab:blue",
                           edgecolors="none", label="fixes")
        ax_scatter.scatter([east.mean()], [north.mean()], marker="x", s=90,
                           color="tab:red", linewidths=2, label="mean", zorder=3)

        if len(east) > 2:
            covariance = np.cov(np.vstack([east, north]))
            eigenvalues, eigenvectors = np.linalg.eigh(covariance)
            angle = np.degrees(np.arctan2(eigenvectors[1, -1], eigenvectors[0, -1]))
            width, height = 2.0 * np.sqrt(np.maximum(eigenvalues[::-1], 0.0))
            ax_scatter.add_patch(
                plt.matplotlib.patches.Ellipse(
                    (east.mean(), north.mean()), width, height, angle=angle,
                    fill=False, edgecolor="tab:red", lw=1.5, ls="--",
                    label="1$\\sigma$",
                )
            )
        rms = np.sqrt(np.mean(east**2 + north**2))
        ax_scatter.annotate(
            f"horizontal RMS {rms:.1f} m\nvertical RMS {np.sqrt(np.mean(up**2)):.1f} m",
            xy=(0.03, 0.97), xycoords="axes fraction", va="top", fontsize=9,
            bbox=dict(boxstyle="round", fc="white", ec="gray", alpha=0.8),
        )

    ax_scatter.axhline(0, color="gray", lw=0.6)
    ax_scatter.axvline(0, color="gray", lw=0.6)
    ax_scatter.set_xlabel("East [m]")
    ax_scatter.set_ylabel("North [m]")
    ax_scatter.set_aspect("equal", adjustable="datalim")
    ax_scatter.grid(True)
    if ax_scatter.get_legend_handles_labels()[0]:
        ax_scatter.legend(fontsize=8, loc="lower right")

    t = np.arange(len(enu_m)) if time_s is None else np.asarray(time_s)
    for column, label, color in ((0, "East", "tab:blue"), (1, "North", "tab:orange"),
                                 (2, "Up", "tab:green")):
        ax_time.plot(t[good], enu_m[good, column], lw=1.2, label=label, color=color)
    ax_time.axhline(0, color="gray", lw=0.6)
    ax_time.set_xlabel("Time [s]" if time_s is not None else "Epoch")
    ax_time.set_ylabel("Offset from reference [m]")
    ax_time.grid(True)
    ax_time.legend(fontsize=9)

    fig.suptitle(title if title is not None else "Position solution")
    return ax_scatter


def plot_clock_solution(
    fig: Figure | SubFigure,
    time_s: np.ndarray,
    clock_bias_m: np.ndarray,
    *,
    title: Optional[str] = None,
) -> Axes:
    """
    The receiver clock: its offset from GPS time, and what is left after a straight
    line is removed.

    The line is the whole point.  The receiver's clock here is the sample counter,
    so a constant slope in the bias is the front end's oscillator running at the
    wrong rate -- a real measurement of the hardware, quoted in ppm.  The residual
    below says how much of the trace that line does *not* explain, which is where
    measurement noise and any real clock instability show up.
    """
    time_s = np.asarray(time_s, dtype=float)
    bias = np.asarray(clock_bias_m, dtype=float)
    good = np.isfinite(bias)

    axes = fig.subplots(2, 1, sharex=True, height_ratios=[2, 1])
    ax_bias, ax_residual = axes

    ax_bias.plot(time_s[good], bias[good] * 1e-3, lw=1.5, color="tab:blue", label="Clock bias")
    ax_bias.set_ylabel("Clock bias [km]")
    ax_bias.grid(True)

    if good.sum() > 1:
        t = time_s[good] - time_s[good][0]
        slope, intercept = np.polyfit(t, bias[good], 1)
        ax_bias.plot(time_s[good], (slope * t + intercept) * 1e-3, lw=1.2, ls="--",
                     color="tab:red", label="Linear fit")
        drift_ppm = slope / 2.99792458e8 * 1e6
        residual = bias[good] - (slope * t + intercept)
        ax_bias.annotate(
            f"drift {drift_ppm:+.3f} ppm ({slope:+.1f} m/s)\n"
            f"residual RMS {np.sqrt(np.mean(residual**2)):.2f} m",
            xy=(0.03, 0.05), xycoords="axes fraction", fontsize=9,
            bbox=dict(boxstyle="round", fc="white", ec="gray", alpha=0.8),
        )
        ax_residual.plot(time_s[good], residual, lw=1.0, color="tab:purple")
        ax_residual.axhline(0, color="gray", lw=0.6)

    ax_bias.legend(fontsize=9)
    ax_residual.set_ylabel("Residual [m]")
    ax_residual.set_xlabel("Time [s]")
    ax_residual.grid(True)

    fig.suptitle(title if title is not None else "Receiver clock solution")
    return ax_bias


def plot_pseudorange_residuals(
    fig: Figure | SubFigure,
    time_s: np.ndarray,
    residuals_m: np.ndarray,
    sat_ids: Sequence[str],
    *,
    title: Optional[str] = None,
) -> Axes:
    """
    Post-fit residuals per satellite -- the honest measure of whether the
    corrections are doing their job.

    Read the *structure*, not just the size.  Residuals that scatter about zero are
    measurement noise; one satellite sitting consistently off is an ephemeris or
    multipath problem on that satellite; all of them drifting together means
    something common-mode is unmodelled, and that is usually the clock.

    With exactly four satellites these are zero by construction and mean nothing;
    the caller should say so rather than let a flat line read as a good fix.
    """
    time_s = np.asarray(time_s, dtype=float)
    residuals_m = np.atleast_2d(residuals_m)

    ax = fig.add_subplot(1, 1, 1)
    for j, sat_id in enumerate(sat_ids):
        column = residuals_m[:, j]
        good = np.isfinite(column)
        if good.any():
            ax.plot(time_s[good], column[good], lw=1.0, marker=".", ms=3, label=sat_id)

    ax.axhline(0, color="gray", lw=0.8)
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Post-fit residual [m]")
    ax.grid(True)
    if ax.get_legend_handles_labels()[0]:
        ax.legend(fontsize=8, ncol=2)

    finite = residuals_m[np.isfinite(residuals_m)]
    if finite.size:
        ax.set_title(
            (title if title is not None else "Pseudorange residuals")
            + f"  (RMS {np.sqrt(np.mean(finite**2)):.2f} m)"
        )
    else:
        ax.set_title(title if title is not None else "Pseudorange residuals")
    return ax


def plot_correction_magnitudes(
    fig: Figure | SubFigure,
    terms: dict[str, np.ndarray],
    sat_ids: Sequence[str],
    *,
    title: Optional[str] = None,
) -> Axes:
    """
    What each correction is worth, on a log scale because the range is enormous.

    The satellite clock is tens of kilometres, Sagnac tens of metres, the
    troposphere a few metres, the ionosphere a few more.  Seeing five orders of
    magnitude side by side is the point -- it explains why an uncorrected fix is
    not merely inaccurate but nonsensical, and why the last correction is worth far
    less effort than the first.
    """
    ax = fig.add_subplot(1, 1, 1)
    labels, magnitudes = [], []
    for name, values in terms.items():
        if name.endswith("_deg"):  # geometry, not a range correction
            continue
        finite = np.abs(np.asarray(values)[np.isfinite(values)])
        if finite.size:
            labels.append(name.replace("_", " "))
            magnitudes.append(finite.mean())

    if labels:
        # Ascending, because barh draws the first entry at the bottom -- so
        # ascending order puts the largest correction at the TOP of the chart,
        # which is where a reader looks first.
        order = np.argsort(magnitudes)
        labels = [labels[i] for i in order]
        magnitudes = [magnitudes[i] for i in order]
        bars = ax.barh(labels, magnitudes, color="tab:blue", alpha=0.8)
        ax.set_xscale("log")
        for bar, value in zip(bars, magnitudes):
            ax.annotate(f"{value:,.2f} m", (value, bar.get_y() + bar.get_height() / 2),
                        xytext=(6, 0), textcoords="offset points", va="center", fontsize=9)
        ax.set_xlim(right=max(magnitudes) * 8)

    ax.set_xlabel("Mean absolute correction [m]")
    ax.grid(True, axis="x", which="both")
    ax.set_title(title if title is not None else "Pseudorange corrections")
    return ax


def plot_orbit_and_clock_differences(
    fig: Figure | SubFigure,
    time: np.ndarray,
    position_diff_m: np.ndarray,
    clock_diff_m: np.ndarray,
    sat_ids: Sequence[str],
    *,
    title: Optional[str] = None,
    xlabel: str = "GPS time [hours of day]",
    sp3_epochs: Optional[np.ndarray] = None,
    ephemeris_toes: Optional[np.ndarray] = None,
    ephemeris_toe_marks: Optional[np.ndarray] = None,
    mark_time: Optional[float] = None,
) -> tuple[Axes, Axes]:
    """
    Broadcast ephemeris against precise orbits, in the two quantities it predicts.

    Both panels are in metres of range so they can be read against each other, and
    against the residuals in sections 8 and 9 of notebook 02.  They are not equally
    costly, though, and the plot cannot show that: only the component of an orbit
    error along the line of sight reaches the pseudorange, and the radial direction
    is the poorly observed one from the ground, whereas a clock error is a pure
    range error and arrives in full.

    `position_diff_m` and `clock_diff_m` are `(epochs, satellites)`.  Both are
    expected to arrive as magnitudes the caller has already reduced -- in
    particular with the clock's common time-scale offset removed, since that offset
    is absorbed whole by the receiver clock solution and plotting it would bury the
    part that is not.

    The x axis is GPS time of week -- absolute, so it can be read against a `toe`
    (which is stated in seconds, so divide by 3600) rather than being relative to a
    collect the plot happens to be centred on.  That is what makes the two optional
    overlays worth having:

    * `ephemeris_changes`, an `(epochs, satellites)` boolean, marks where the
      broadcast record in use changed.  Every step in a trace should sit on one of
      these; a step that does not is an interpolation artefact rather than an
      upload.
    * `sp3_epochs` draws the precise product's tabulated instants as ticks along
      the top.  Their density against the sparse ephemeris markers is the point:
      one is a table sampled every few minutes, the other a prediction reissued
      every couple of hours.

    `mark_time` draws the collect itself, the one instant a fix actually used.

    The time axis is expected to span hours rather than the tracked run: over a
    minute neither a broadcast orbit nor a precise one does anything, so a plot of
    the run alone is two flat lines and says nothing about either.  Over a day the
    structure appears -- error growing across each fit interval and dropping at
    every upload.
    """
    time = np.asarray(time, dtype=float)
    position_diff_m = np.atleast_2d(position_diff_m)
    clock_diff_m = np.atleast_2d(clock_diff_m)
    if ephemeris_toe_marks is not None:
        ephemeris_toe_marks = np.atleast_2d(ephemeris_toe_marks)

    top = fig.add_subplot(2, 1, 1)
    bottom = fig.add_subplot(2, 1, 2, sharex=top)

    for j, sat_id in enumerate(sat_ids):
        colour = None
        for ax, values in ((top, position_diff_m), (bottom, clock_diff_m)):
            if j >= values.shape[1]:
                continue
            column = values[:, j]
            good = np.isfinite(column)
            if not good.any():
                continue
            (line,) = ax.plot(
                time[good], column[good], lw=1.2, label=sat_id, color=colour
            )
            # Both panels describe one satellite, so they must agree on its colour
            # even when it is missing from one of them.
            colour = line.get_color()
            if ephemeris_toe_marks is not None and j < ephemeris_toe_marks.shape[1]:
                changed = ephemeris_toe_marks[:, j] & good
                if changed.any():
                    ax.plot(
                        time[changed], column[changed], linestyle="none",
                        marker="o", ms=4, mfc="none", color=colour,
                    )

    if ephemeris_toes is not None and len(ephemeris_toes):
        ephemeris_toes = np.unique(np.asarray(ephemeris_toes, dtype=float))
        inside = (ephemeris_toes >= time.min()) & (ephemeris_toes <= time.max())
        if inside.any():
            top.plot(
                ephemeris_toes[inside], np.zeros(int(inside.sum())),
                transform=top.get_xaxis_transform(), linestyle="none",
                marker="|", ms=8, color="tab:blue", alpha=0.5, clip_on=False,
                label="ephemeris toe",
            )

    if sp3_epochs is not None and len(sp3_epochs):
        sp3_epochs = np.asarray(sp3_epochs, dtype=float)
        inside = (sp3_epochs >= time.min()) & (sp3_epochs <= time.max())
        if inside.any():
            top.plot(
                sp3_epochs[inside], np.full(int(inside.sum()), 1.0),
                transform=top.get_xaxis_transform(), linestyle="none",
                marker="|", ms=6, color="gray", alpha=0.6, clip_on=False,
                label="SP3 nodes",
            )

    # An hour is the natural unit to read a `toe` against, so tick at every one --
    # unless the window is wide enough that hourly ticks would be a smear.
    span_h = float(time.max() - time.min()) if time.size else 0.0
    locator = plt.MultipleLocator(1.0) if 0.0 < span_h <= 48.0 else None

    top.set_ylabel("|position| [m]")
    bottom.set_ylabel("|clock| [m]")
    bottom.set_xlabel(xlabel)
    for ax in (top, bottom):
        ax.grid(True)
        ax.set_ylim(bottom=0.0)
        if locator is not None:
            ax.xaxis.set_major_locator(locator)
        if mark_time is not None and time.size and time.min() <= mark_time <= time.max():
            ax.axvline(mark_time, color="gray", lw=1.0, ls="--", zorder=0)
    top.tick_params(labelbottom=False)

    if top.get_legend_handles_labels()[0]:
        top.legend(fontsize=8, ncol=5, loc="upper left")
    top.set_title(title if title is not None else "Broadcast minus precise")
    return top, bottom


def plot_prefit_residuals(
    fig: Figure | SubFigure,
    time_s: np.ndarray,
    residual_m: np.ndarray,
    sat_ids: Sequence[str],
    *,
    title: Optional[str] = None,
) -> Axes:
    """
    Pseudorange minus everything that can be modelled, one line per satellite.

    The corrections in section 7 remove the *errors* -- satellite clock,
    relativity, Sagnac, atmosphere -- but not the **geometry**, which is not an
    error but the signal itself: 20,200 km at the zenith to 25,800 km near the
    horizon.  That 5,600 km spread is why a plot of corrected pseudoranges shows
    satellites thousands of kilometres apart and nothing else.  Subtract the
    modelled range to the a-priori position as well, and what is left is what a
    position solve actually works on.

    Two things remain, and they are visually distinct:

    * **The bundle**, common to every satellite: the receiver clock bias.  Its
      *level* is arbitrary -- `form_observables` picks a nominal receive time so
      pseudoranges land near their true magnitude -- but its *slope* is not, and is
      the oscillator's rate error.  A tenth of a ppm is 30 m/s here, so over a few
      minutes the bundle can travel kilometres.
    * **The spread within the bundle**: the a-priori position error projected onto
      each line of sight, plus ephemeris error, multipath and noise.  Tens of
      metres with an unsurveyed reference, varying smoothly with the satellite's
      direction because a position error projects as a cosine.

    Note the scale between them.  A tenth of a ppm of clock drift is 30 m/s, so a
    few minutes of run puts kilometres of ramp on an axis where the spread is tens
    of metres -- on a long run the lines look like one line.  That is not the plot
    failing; it is the honest ratio of the two terms, and it is why section 9 holds
    the position fixed and plots the spread on its own axis.

    Sections 8 and 9 are exactly the business of separating those two.
    """
    time_s = np.asarray(time_s, dtype=float)
    residual_m = np.atleast_2d(residual_m)

    ax = fig.add_subplot(1, 1, 1)
    for j, sat_id in enumerate(sat_ids):
        if j >= residual_m.shape[1]:
            continue
        column = residual_m[:, j]
        good = np.isfinite(column)
        if good.any():
            ax.plot(time_s[good], column[good], lw=1.4, label=sat_id)

    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Pseudorange - modelled range [m]")
    ax.grid(True)
    if ax.get_legend_handles_labels()[0]:
        ax.legend(fontsize=8, ncol=4)
    ax.set_title(title if title is not None else "Pre-fit residuals")
    return ax
