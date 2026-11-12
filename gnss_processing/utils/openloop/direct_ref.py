"""
The direct-signal reference for the direct-referenced model (plan section 8.1).

Notebook 03 predicts the reflection relative to the direct signal of the same
satellite and band, tracked in closed loop by notebook 01 on the zenith channel.
The two channels share one sample clock (D-025, D-097), so the receiver clock,
the satellite clock and the ionosphere cancel.

`DirectReference` holds, at the epochs of the direct channel:

- `uptime_ms`       -- epoch start, ms from the start of the sample stream.
- `code_phase_ms`   -- received code phase (ms of code) at the epoch start.
- `carrier_cycles`  -- carrier (NCO) phase in cycles at the epoch start.
- `doppler_hz`      -- carrier Doppler in Hz.

It interpolates them with cubic Hermite splines: the code-phase rate is
1e3 (1 + f/f_c) ms/s and the carrier-phase rate is f, which is how the tracking
loop itself propagates them (`TrackingSignalState.propagate_phase`).

Data symbols. Over a UAS height the reflection is less than 1 us behind the
direct signal, so it carries the same data symbol and overlay chip ([B11]). The
symbol of code period p (p = floor of the code phase in ms) comes from the sign
of the direct prompt, projected on its long-term axis.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.interpolate import CubicHermiteSpline


@dataclass
class DirectReference:
    """Direct-signal tracking history, and the data symbol of each code period."""

    uptime_ms: np.ndarray
    code_phase_ms: np.ndarray
    carrier_cycles: np.ndarray
    doppler_hz: np.ndarray
    carrier_freq_hz: float
    symbol_by_period: dict[int, np.ndarray] = field(default_factory=dict)
    """Component index -> array of +/-1 indexed by (period - first_period)."""
    first_period: int = 0
    source: str = ""

    def __post_init__(self) -> None:
        u = self.uptime_ms * 1e-3
        self._code = CubicHermiteSpline(
            u, self.code_phase_ms, 1e3 * (1.0 + self.doppler_hz / self.carrier_freq_hz),
            extrapolate=False)
        self._carrier = CubicHermiteSpline(u, self.carrier_cycles, self.doppler_hz,
                                           extrapolate=False)

    def code_phase_at(self, uptime_ms) -> np.ndarray:
        return self._code(np.asarray(uptime_ms, dtype=float) * 1e-3)

    def carrier_at(self, uptime_ms) -> np.ndarray:
        return self._carrier(np.asarray(uptime_ms, dtype=float) * 1e-3)

    def doppler_at(self, uptime_ms) -> np.ndarray:
        return self._carrier(np.asarray(uptime_ms, dtype=float) * 1e-3, 1)

    def symbol(self, component: int, periods: np.ndarray) -> np.ndarray:
        """
        Data symbol (+/-1) of each code period for one component; 1 for a pilot
        or where nothing is known. NaN is never returned, so an unknown symbol
        costs coherence, not a crash; the caller flags those periods.
        """
        table = self.symbol_by_period.get(component)
        periods = np.asarray(periods, dtype=np.int64)
        if table is None:
            return np.ones(len(periods))
        idx = periods - self.first_period
        ok = (idx >= 0) & (idx < len(table))
        out = np.ones(len(periods))
        out[ok] = table[idx[ok]]
        return out


def symbols_from_prompt(prompt: np.ndarray) -> np.ndarray:
    """
    +/-1 data signs from complex prompt values of one component.

    The prompt of a locked channel lies on one axis (with sign = data). The axis
    angle is found from the mean of the doubled angle, which is blind to the
    data sign; the sign is then the projection on that axis. This works for any
    branch convention of the tracking loop.
    """
    prompt = np.asarray(prompt, dtype=complex)
    axis = 0.5 * np.angle(np.mean(prompt**2 / np.maximum(np.abs(prompt), 1e-30)))
    projection = np.real(prompt * np.exp(-1j * axis))
    return np.where(projection >= 0, 1.0, -1.0)


def from_tracking_result(result, signal) -> DirectReference:
    """
    Build a reference from one notebook 01 `TrackingResult` (utils.tracking_io).

    Each epoch spans `epoch_duration_ms` code periods from floor(code phase).
    The sign of the prompt of each data component fills those periods.
    Assumption (concern C-3): epoch starts are on code-period boundaries, which
    the tracking channel does once its grid is anchored (`bit_synced`).
    """
    out = result.outputs
    prompt = np.asarray(out.prompt_corr)  # (epochs, components)
    first = int(np.floor(out.code_phase_ms[0] + 1e-6))
    last = int(np.floor(out.code_phase_ms[-1] + out.epoch_duration_ms[-1] + 1e-6))
    symbols: dict[int, np.ndarray] = {}
    for c, component in enumerate(signal.code_set.components):
        if not component.symbol_period_ms:
            continue
        signs = symbols_from_prompt(prompt[:, c])
        table = np.ones(last - first + 1)
        for e in range(len(signs)):
            p0 = int(np.floor(out.code_phase_ms[e] + 1e-6)) - first
            n = int(round(out.epoch_duration_ms[e] / signal.primary_period_ms))
            table[max(p0, 0):max(p0 + n, 0)] = signs[e]
        symbols[c] = table
    return DirectReference(
        uptime_ms=np.asarray(out.uptime_epoch_ms, dtype=float),
        code_phase_ms=np.asarray(out.code_phase_ms, dtype=float),
        carrier_cycles=np.asarray(out.carr_phase_cycles, dtype=float),
        doppler_hz=np.asarray(out.doppler_freq_hz, dtype=float),
        carrier_freq_hz=float(signal.carrier_freq_hz),
        symbol_by_period=symbols,
        first_period=first,
        source=f"tracking:{result.signal_id}",
    )


def from_synthetic_truth(truth, signal, start_uptime_ms: float = 0.0) -> DirectReference:
    """
    A perfect direct reference from the synthetic truth (as if notebook 01 had
    tracked with no error). The symbols are the true ones.
    """
    code = truth.direct_code_phase_ms
    first = int(np.floor(code[0])) - 2
    last = int(np.floor(code[-1])) + 2
    periods = np.arange(first, last + 1)
    symbols = {}
    for c, component in enumerate(signal.code_set.components):
        if component.symbol_period_ms:
            per = component.symbol_period_ms // signal.primary_period_ms
            symbols[c] = truth.symbols[(periods // per) % len(truth.symbols)]
    return DirectReference(
        uptime_ms=start_uptime_ms + truth.t_s * 1e3,
        code_phase_ms=code,
        carrier_cycles=truth.direct_carrier_cycles,
        doppler_hz=truth.direct_doppler_hz,
        carrier_freq_hz=float(signal.carrier_freq_hz),
        symbol_by_period=symbols,
        first_period=first,
        source="synthetic truth",
    )
