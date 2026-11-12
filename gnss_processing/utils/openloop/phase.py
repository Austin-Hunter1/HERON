"""
Data wipe, residual phase, unwrapping, coherence, and the SCANF filter
(plan section 10).

Residual phase. The OL replica has the model phase phi_R~. The waveform at the
reflection lag is Z ~ exp(j 2 pi (phi_R - phi_R~)), so the residual phase
(cycles) is (D~ - D) / lambda + const, and the measured excess path is

    D^ = D~ - lambda * dphi / (2 pi)        (dphi in rad, unwrapped)

Unwrapping uses a prediction (the filter's, not only the previous value):

    m_k = round((dphi_pred - dphi_wrapped) / 2 pi)

SCANF for a drone (plan 10.3, after Wang et al. 2021). State: the excess-path
residual r and its rate (one non-dispersive state for all bands, because the
ionospheric excess is zero, plan 7.1). Each band i measures

    y_i = -lambda_i (w_i / 2 pi + m_i) - b_i = r + v_i

with w_i the wrapped phase, m_i an integer slip estimate, and b_i a constant
band offset (set at the start). The variance of v_i comes from C/N0 (Wang eq. 17):

    sigma_i^2 = (lambda_i / 2 pi)^2 (1 / (2 T C/N0)) (1 + 1 / (2 T C/N0))
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .correlator import OpenLoopOutput


# --------------------------------------------------------------------------
# Data and overlay wipe
# --------------------------------------------------------------------------

def wipe(ol: OpenLoopOutput, signal, direct, component: int,
         overlay_offset_periods: int = 0) -> np.ndarray:
    """
    Waveforms (K, M, J) of one component with the data symbol and the overlay
    chip removed.

    The data symbol of period p comes from the direct channel (`direct.symbol`).
    The overlay chip is overlay[(p + offset) mod L]. The offset is 0 when the
    code phase counts periods from the overlay start; `search_overlay_offset`
    finds it when that is not known (concern C-3).
    """
    comp = signal.code_set.components[component]
    z = ol.waveforms[:, :, :, component].astype(np.complex128)
    signs = np.ones(ol.num_blocks)
    if comp.symbol_period_ms:
        signs = signs * direct.symbol(component, ol.periods)
    if comp.overlay is not None:
        signs = signs * comp.overlay[(ol.periods + overlay_offset_periods) % len(comp.overlay)]
    return z * signs[:, None, None]


def search_overlay_offset(ol: OpenLoopOutput, signal, direct, component: int,
                          lag_index: int, coherent_periods: int | None = None) -> int:
    """
    The overlay offset that makes the coherent sum largest at one lag.

    For a pilot with an overlay of length L, only the right alignment adds up
    coherently over L periods.
    """
    comp = signal.code_set.components[component]
    if comp.overlay is None:
        return 0
    length = len(comp.overlay)
    n = coherent_periods or length
    best, best_power = 0, -1.0
    for off in range(length):
        z = wipe(ol, signal, direct, component, off)[:, 0, lag_index]
        k = (len(z) // n) * n
        power = float(np.sum(np.abs(z[:k].reshape(-1, n).sum(axis=1)) ** 2))
        if power > best_power:
            best, best_power = off, power
    return best


# --------------------------------------------------------------------------
# Residual phase, C/N0, coherence
# --------------------------------------------------------------------------

@dataclass
class PhaseSeries:
    """Coherent-block series at one lag, for one band and channel."""

    uptime_ms: np.ndarray
    prompt: np.ndarray            # coherent sums at the reflection lag
    wrapped_rad: np.ndarray
    cn0_dbhz: np.ndarray
    model_excess_m: np.ndarray
    wavelength_m: float
    coherent_ms: float


def coherent_series(ol: OpenLoopOutput, wiped: np.ndarray, lag_index: int,
                    coherent_blocks: int, noise_mask: np.ndarray,
                    doppler_index: int = 0) -> PhaseSeries:
    """
    Sum `coherent_blocks` blocks at the reflection lag and estimate C/N0 per sum:

        C/N0 = (|Z|^2 - P_n) / (P_n T)

    with P_n the mean |Z|^2 at the noise lags (same coherent time).
    """
    from .acquisition import coherent_sum

    z = coherent_sum(wiped[:, doppler_index, :], coherent_blocks)
    noise = np.mean(np.abs(z[:, noise_mask]) ** 2, axis=1)
    noise = np.full_like(noise, np.median(noise))  # one floor per run is steadier
    t_coh = float(np.median(ol.duration_ms)) * coherent_blocks * 1e-3
    p = z[:, lag_index]
    cn0 = np.maximum(np.abs(p) ** 2 - noise, 1e-12) / (noise * t_coh)
    k = len(p) * coherent_blocks
    # Label each coherent sum with its CENTRE time, the time that the averaged
    # model value below belongs to. A start-time label is late by half the sum:
    # 10 ms at 20 ms, which is 14 mm of excess path for a drone that climbs at
    # 1.2 m/s (seen in the first synthetic run).
    u = (ol.uptime_ms[:k].reshape(-1, coherent_blocks).mean(axis=1)
         + 0.5 * float(np.median(ol.duration_ms)))
    excess = ol.model_excess_m[:k].reshape(-1, coherent_blocks).mean(axis=1)
    from . import frames

    return PhaseSeries(u, p, np.angle(p), 10 * np.log10(cn0), excess,
                       frames.SPEED_OF_LIGHT / ol.carrier_freq_hz, t_coh * 1e3)


def unwrap_simple(wrapped_rad: np.ndarray) -> np.ndarray:
    """Unwrap against the previous value (Bai 2012 eq. 8; Wang 2021 eq. 9)."""
    return np.unwrap(wrapped_rad)


def coherence_metric(prompt: np.ndarray, window: int) -> np.ndarray:
    """
    kappa = |sum Z|^2 / (N sum |Z|^2) over a sliding window (plan 10.4).

    1 for a steady phasor; about 1/N for noise or diffuse scattering. The
    residual phase of a good model changes slowly, so a window of a few hundred
    ms is short enough.
    """
    z = np.asarray(prompt)
    n = len(z)
    out = np.full(n, np.nan)
    half = window // 2
    for i in range(n):
        a, b = max(0, i - half), min(n, i + half + 1)
        seg = z[a:b]
        out[i] = np.abs(seg.sum()) ** 2 / (len(seg) * np.sum(np.abs(seg) ** 2) + 1e-30)
    return out


def phase_variance_m2(cn0_dbhz, t_coh_s: float, wavelength_m: float) -> np.ndarray:
    """Measurement variance of the excess path (Wang 2021 eq. 17), metres^2."""
    cn0 = 10 ** (np.asarray(cn0_dbhz) / 10)
    x = 1.0 / (2.0 * t_coh_s * cn0)
    return (wavelength_m / (2 * np.pi)) ** 2 * x * (1.0 + x)


# --------------------------------------------------------------------------
# SCANF (adapted)
# --------------------------------------------------------------------------

@dataclass
class ScanfResult:
    """Filter output on the common time grid."""

    uptime_ms: np.ndarray
    residual_m: np.ndarray          # filtered r
    rate_mps: np.ndarray
    slips: np.ndarray               # (K, B) integer m_i: wrap count plus corrected slips
    measured_m: np.ndarray          # (K, B) y_i with the slips applied
    band_offset_m: np.ndarray       # (B,)
    innovation_m: np.ndarray        # (K, B)
    # (K, B) bool: the filter chose an integer that is NOT the one that plain
    # continuity with the previous epoch gives (a corrected cycle slip). A
    # normal wrap of w_i through +-pi changes m_i but is not an event (C-17).
    slip_events: np.ndarray
    excess_path_m: np.ndarray       # model excess + filtered residual


def scanf(series: list[PhaseSeries], *, process_accel_mps2: float = 0.05,
          slip_search: int = 2, min_cn0_dbhz: float = 25.0) -> ScanfResult:
    """
    Run the adapted SCANF over one or more bands on the SAME time grid.

    Two bands (L1 and L5) must come from runs with the same block grid and the
    same coherent time; `align_series` makes that true. With one band this is a
    single-frequency filter with slip estimation.

    The model excess of the first series is the reference: r is the correction
    to it, so D^ = D~ + r.
    """
    b = len(series)
    k = len(series[0].wrapped_rad)
    for s in series[1:]:
        if len(s.wrapped_rad) != k:
            raise ValueError("all bands must have the same number of epochs (use align_series)")
    t = series[0].uptime_ms * 1e-3
    lam = np.array([s.wavelength_m for s in series])
    w = np.stack([s.wrapped_rad for s in series], axis=1)
    cn0 = np.stack([s.cn0_dbhz for s in series], axis=1)
    t_coh = series[0].coherent_ms * 1e-3

    # The models of different bands describe the same geometry; their excess
    # paths differ only through the band-specific parts (none in Mode A), so the
    # first band's model is the common reference. The per-band difference goes
    # into the band offset b_i at the start.
    model = series[0].model_excess_m
    model_diff = np.stack([s.model_excess_m - model for s in series], axis=1)

    x = np.zeros(2)
    p = np.diag([1.0, 1.0])
    m = np.zeros(b)
    # Raw measurement y_i(m) = -lambda_i (w_i/2pi + m_i) + model_diff_i: the
    # residual of the band's own model, moved onto the common model.
    y0 = -lam * (w[0] / (2 * np.pi)) + model_diff[0]
    offset = y0 - y0[0]  # band offsets; band 0 defines r(0) = y0[0]
    x[0] = y0[0]

    out_r = np.zeros(k)
    out_v = np.zeros(k)
    out_m = np.zeros((k, b))
    out_y = np.zeros((k, b))
    out_innov = np.zeros((k, b))
    events = np.zeros((k, b), dtype=bool)
    # Unwrapped cycles w/2pi + m of the last good epoch, per band. Plain
    # continuity picks the m that keeps the new value nearest to it.
    prev_cycles = w[0] / (2 * np.pi)
    h = np.ones((b, 1)) @ np.array([[1.0, 0.0]])
    for i in range(k):
        dt = t[i] - t[i - 1] if i > 0 else 0.0
        a = np.array([[1.0, dt], [0.0, 1.0]])
        q = process_accel_mps2**2 * np.array([[dt**4 / 4, dt**3 / 2], [dt**3 / 2, dt**2]])
        x = a @ x
        p = a @ p @ a.T + q
        pred = h @ x
        var = phase_variance_m2(cn0[i], t_coh, lam)
        good = cn0[i] >= min_cn0_dbhz
        y = np.zeros(b)
        for j in range(b):
            base = -lam[j] * (w[i, j] / (2 * np.pi)) + model_diff[i, j] - offset[j]
            cands = m[j] + np.arange(-slip_search, slip_search + 1)
            vals = base - lam[j] * cands
            best = int(np.argmin(np.abs(vals - pred[j])))
            if good[j]:
                frac = w[i, j] / (2 * np.pi)
                m_cont = round(prev_cycles[j] - frac)
                events[i, j] = cands[best] != m_cont
                m[j] = cands[best]
                prev_cycles[j] = frac + m[j]
            y[j] = base - lam[j] * m[j]
        r_cov = np.diag(np.where(good, var, 1e6))
        s_cov = h @ p @ h.T + r_cov
        gain = p @ h.T @ np.linalg.inv(s_cov)
        innov = y - pred
        x = x + gain @ innov
        p = (np.eye(2) - gain @ h) @ p
        out_r[i], out_v[i] = x
        out_m[i] = m
        out_y[i] = y
        out_innov[i] = innov
    # D^ = D~ - lambda dphi/2pi: the filtered residual r has that sign already.
    return ScanfResult(series[0].uptime_ms, out_r, out_v, out_m, out_y, offset, out_innov,
                       events, model + out_r)


def align_series(a: PhaseSeries, b: PhaseSeries, tolerance_ms: float = 0.6
                 ) -> tuple[PhaseSeries, PhaseSeries]:
    """Keep the epochs of two bands whose start times agree within `tolerance_ms`."""
    j = np.searchsorted(b.uptime_ms, a.uptime_ms)
    j = np.clip(j, 1, len(b.uptime_ms) - 1)
    left = b.uptime_ms[j - 1]
    pick = np.where(np.abs(a.uptime_ms - left) < np.abs(a.uptime_ms - b.uptime_ms[j]), j - 1, j)
    ok = np.abs(a.uptime_ms - b.uptime_ms[pick]) <= tolerance_ms
    ia, ib = np.nonzero(ok)[0], pick[ok]

    def take(s: PhaseSeries, idx):
        return PhaseSeries(s.uptime_ms[idx], s.prompt[idx], s.wrapped_rad[idx], s.cn0_dbhz[idx],
                           s.model_excess_m[idx], s.wavelength_m, s.coherent_ms)

    return take(a, ia), take(b, ib)
