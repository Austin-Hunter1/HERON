"""
The open-loop correlator loop (plan sections 8.4 and 10.1).

For each code period p of the reflected replica (block k), the model gives:

    phi_k = phi_R(u_k)             carrier phase at the first sample of the block
    f_k   = f_R(u_k + T/2)         one Doppler for the block (mid-block)
    chi_k = chi_R(u_k)             code phase at the first sample of the block
    rate  = R_c (1 + f_k / f_c)    carrier-aided code rate

and `bpsk_correlation.correlate__multicomponent` correlates the block at a set of
delay lags. Nothing feeds back from the data into the model: that is what open
loop means here.

Lags are EXCESS DELAY in chips: a positive lag correlates with a signal that is
later (longer path) than the model. The kernel's `bin_offsets_chips` move the
replica code phase, which is the other sign, so the lags go in negated.

Outputs (per block): the complex waveform Z[k, lag, component], the model values,
and the sample index range. Data and overlay are NOT wiped here; `phase.wipe`
does that, so the raw waveforms stay available for notebook 04 (D-101).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from utils.bpsk_correlation import correlate__multicomponent

from .model import ReflectionModel


@dataclass
class OpenLoopOutput:
    """The open-loop history of one satellite on one nadir channel."""

    periods: np.ndarray            # (K,) code period index p
    uptime_ms: np.ndarray          # (K,) actual block start (first sample)
    duration_ms: np.ndarray        # (K,)
    lags_chips: np.ndarray         # (J,)
    doppler_offsets_hz: np.ndarray  # (M,)
    waveforms: np.ndarray          # (K, M, J, C) complex64
    model_code_phase_ms: np.ndarray
    model_carrier_cycles: np.ndarray
    model_doppler_hz: np.ndarray
    model_excess_m: np.ndarray
    component_names: tuple[str, ...]
    carrier_freq_hz: float
    code_rate_chips_per_sec: float
    extras: dict = field(default_factory=dict)

    @property
    def num_blocks(self) -> int:
        return len(self.periods)

    def waveform(self, doppler_index: int = 0, component: int = 0) -> np.ndarray:
        """Z[k, lag] for one Doppler bin and one component."""
        return self.waveforms[:, doppler_index, :, component]


def run_open_loop(
    samples: np.ndarray,
    *,
    samp_rate: float,
    start_uptime_ms: float,
    signal,
    model: ReflectionModel,
    lags_chips: np.ndarray,
    doppler_offsets_hz: np.ndarray | None = None,
    first_uptime_ms: float | None = None,
    last_uptime_ms: float | None = None,
) -> OpenLoopOutput:
    """
    Correlate `samples` (complex64, starting at `start_uptime_ms`) block by block.

    `doppler_offsets_hz` adds Doppler bins around the model (for the small DDM of
    the acquisition step, plan 9.2); the default is one bin at the model.
    """
    samples = np.ascontiguousarray(samples, dtype=np.complex64)
    lags = np.asarray(lags_chips, dtype=float)
    offsets = np.zeros(1) if doppler_offsets_hz is None else np.asarray(doppler_offsets_hz, float)
    nominal = float(signal.tracking_code_rate_chips_per_sec)
    fc = float(signal.carrier_freq_hz)
    period_ms = float(signal.primary_period_ms)

    stream_end_ms = start_uptime_ms + 1e3 * len(samples) / samp_rate
    lo = max(start_uptime_ms, model.direct.uptime_ms[0], model.geometry.uptime_ms[0])
    hi = min(stream_end_ms, model.direct.uptime_ms[-1], model.geometry.uptime_ms[-1])
    if first_uptime_ms is not None:
        lo = max(lo, first_uptime_ms)
    if last_uptime_ms is not None:
        hi = min(hi, last_uptime_ms)
    periods, starts = model.period_starts(lo, hi, period_ms)
    if len(periods) < 2:
        raise ValueError("no whole code period of the model falls inside the samples")

    num_blocks = len(periods) - 1
    n_comp = signal.code_set.num_components
    waveforms = np.zeros((num_blocks, len(offsets), len(lags), n_comp), dtype=np.complex64)
    first_idx = np.ceil((starts - start_uptime_ms) * samp_rate / 1e3).astype(np.int64)
    first_idx = np.clip(first_idx, 0, len(samples))
    actual_start = start_uptime_ms + first_idx * 1e3 / samp_rate
    durations = np.diff(actual_start)
    mid = actual_start[:-1] + 0.5 * durations
    chi = model.code_phase_ms(actual_start[:-1])
    phi = model.carrier_cycles(actual_start[:-1])
    f_mid = model.doppler_hz(mid)
    excess = model.excess_m(actual_start[:-1])
    bins = -lags  # excess delay -> replica code-phase offset

    out = np.zeros((len(lags), n_comp), dtype=np.complex64)
    for k in range(num_blocks):
        block = samples[first_idx[k]:first_idx[k + 1]]
        if len(block) == 0:
            continue
        code_rate = nominal * (1.0 + f_mid[k] / fc)
        for m, df in enumerate(offsets):
            out[:] = 0
            correlate__multicomponent(
                block, samp_rate, float(phi[k]), float(f_mid[k] + df), signal.code_set,
                code_rate, float(chi[k] * 1e-3 * nominal), bins, out,
            )
            waveforms[k, m] = out

    return OpenLoopOutput(
        periods=periods[:-1], uptime_ms=actual_start[:-1], duration_ms=durations,
        lags_chips=lags, doppler_offsets_hz=offsets, waveforms=waveforms,
        model_code_phase_ms=chi, model_carrier_cycles=phi, model_doppler_hz=f_mid,
        model_excess_m=excess, component_names=tuple(signal.component_names),
        carrier_freq_hz=fc, code_rate_chips_per_sec=nominal,
        extras={"samp_rate": samp_rate, "start_uptime_ms": start_uptime_ms},
    )


def concatenate(outputs: list[OpenLoopOutput]) -> OpenLoopOutput:
    """Join consecutive runs (chunks) of one satellite into one output."""
    first = outputs[0]
    cat = {name: np.concatenate([getattr(o, name) for o in outputs]) for name in (
        "periods", "uptime_ms", "duration_ms", "waveforms", "model_code_phase_ms",
        "model_carrier_cycles", "model_doppler_hz", "model_excess_m")}
    return OpenLoopOutput(lags_chips=first.lags_chips,
                          doppler_offsets_hz=first.doppler_offsets_hz,
                          component_names=first.component_names,
                          carrier_freq_hz=first.carrier_freq_hz,
                          code_rate_chips_per_sec=first.code_rate_chips_per_sec,
                          extras=dict(first.extras), **cat)


def read_samples(filepath, sample_params, start_sample: int, count: int) -> np.ndarray:
    """
    Read `count` complex64 samples from a raw file, starting at `start_sample`.

    Uses `utils.sample_streaming.FileSampleStream`, so every sample format that
    notebook 01 reads works here too. `start_sample` is snapped down to a byte
    boundary for sub-byte formats; the snap is returned in the caller's offset.
    """
    from utils import sample_streaming

    gran = sample_streaming.samples_per_byte_boundary(sample_params.bit_depth,
                                                      sample_params.is_complex)
    start = start_sample - start_sample % gran
    total = count + (start_sample - start)
    total += (-total) % gran
    with sample_streaming.FileSampleStream(filepath, sample_params, total, start) as stream:
        for buffer in stream.sample_buffer_generator():
            return buffer[start_sample - start:start_sample - start + count].copy()
    raise ValueError(f"{filepath}: fewer than {count} samples after sample {start_sample}")


def run_open_loop_file(
    filepath,
    sample_params,
    *,
    samp_rate: float,
    file_start_uptime_ms: float,
    signal,
    model: ReflectionModel,
    lags_chips: np.ndarray,
    first_uptime_ms: float,
    last_uptime_ms: float,
    chunk_ms: float = 1000.0,
    doppler_offsets_hz: np.ndarray | None = None,
    progress=None,
) -> OpenLoopOutput:
    """
    Run the OL correlator over a long raw file in chunks of `chunk_ms`.

    `file_start_uptime_ms` is the uptime of the file's first sample (notebook 01's
    START_OFFSET_MS is the uptime origin there; the tracking run's uptime 0 is the
    sample at START_OFFSET_MS). Each chunk starts exactly where the last block of
    the previous chunk ended, so no code period is lost or doubled at the joins.
    """
    pad_ms = 2.0
    pieces = []
    lo = first_uptime_ms
    while lo < last_uptime_ms - 2.0:
        hi = min(lo + chunk_ms, last_uptime_ms)
        a = int(np.floor((lo - pad_ms - file_start_uptime_ms) * samp_rate / 1e3))
        a = max(a, 0)
        b = int(np.ceil((hi + pad_ms - file_start_uptime_ms) * samp_rate / 1e3))
        samples = read_samples(filepath, sample_params, a, b - a)
        piece = run_open_loop(samples, samp_rate=samp_rate,
                              start_uptime_ms=file_start_uptime_ms + a * 1e3 / samp_rate,
                              signal=signal, model=model, lags_chips=lags_chips,
                              doppler_offsets_hz=doppler_offsets_hz,
                              first_uptime_ms=lo, last_uptime_ms=hi)
        pieces.append(piece)
        # Next chunk: one sample before the end of this chunk's last block. The
        # block end is the first sample AT OR AFTER the next period start, so it
        # can be up to one sample late; stepping back one sample makes the first
        # period found there the one right after this chunk's last block.
        lo = float(piece.uptime_ms[-1] + piece.duration_ms[-1] - 1e3 / samp_rate - 1e-6)
        if progress is not None:
            progress(lo)
    return concatenate(pieces)


def lag_grid(start_chips: float, stop_chips: float, step_chips: float) -> np.ndarray:
    """Lags from config (`openloop.lags_chips`), both ends included."""
    n = int(round((stop_chips - start_chips) / step_chips)) + 1
    return start_chips + step_chips * np.arange(n)


def lag_window_for_height(h_max_m: float, chip_length_m: float, margin_chips: float,
                          step_chips: float, noise_chips: tuple[float, float]) -> np.ndarray:
    """
    The lag window of D-099 (plan 8.6): from the noise lags (before the direct
    leakage) to 2 h_max / chip length + margin (after the latest reflection).
    """
    stop = 2.0 * h_max_m / chip_length_m + margin_chips
    start = min(noise_chips[0], -margin_chips)
    return lag_grid(start, stop, step_chips)
