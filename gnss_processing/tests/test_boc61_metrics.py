"""
Tests for the BOC(6,1) comparison numbers (D-077, Phase T5).

The unit tests give the metrics a signal with a known answer.  The last test runs
the whole chain: track a wide-band B1C signal with both models, save, load,
compare.
"""

from __future__ import annotations

import numpy as np
import pytest

from utils import boc61_metrics, tracking_io
from utils.boc61_metrics import MetricSettings

CARRIER_HZ = 1_575_420_000.0
C = boc61_metrics.SPEED_OF_LIGHT_M_PER_S


def _synthetic_pair(noise_m: float, *, seconds: float = 60.0, step_ms: float = 20.0, seed: int = 1):
    """Code and carrier of a satellite coming closer at 300 m/s, with code noise `noise_m`."""
    rng = np.random.default_rng(seed)
    uptime_ms = np.arange(0.0, seconds * 1e3, step_ms)
    range_change_m = -300.0 * uptime_ms * 1e-3 + 0.5 * 0.2 * (uptime_ms * 1e-3) ** 2
    wavelength = C / CARRIER_HZ
    carrier_cycles = -range_change_m / wavelength
    code_range = range_change_m + rng.normal(0.0, noise_m, size=uptime_ms.size)
    code_phase_ms = uptime_ms - code_range / (C * 1e-3)
    return uptime_ms, code_phase_ms, carrier_cycles


@pytest.mark.parametrize("noise_m", [0.5, 2.0])
def test_cmc_scatter_recovers_the_code_noise(noise_m):
    uptime, code, carrier = _synthetic_pair(noise_m)
    std, count = boc61_metrics.cmc_std_m(uptime, code, carrier, CARRIER_HZ)
    assert std == pytest.approx(noise_m, rel=0.1)
    assert count > 1000


def test_cmc_ignores_a_slow_drift():
    """Ionosphere divergence is slow; the local fit must take it out."""
    uptime, code, carrier = _synthetic_pair(0.0)
    drift_m = 0.05 * uptime * 1e-3  # 5 cm per second
    code = code - drift_m / (C * 1e-3) * -1.0
    std, _ = boc61_metrics.cmc_std_m(uptime, code, carrier, CARRIER_HZ)
    assert std < 1e-3


def test_cmc_with_too_few_epochs_is_nan():
    uptime, code, carrier = _synthetic_pair(0.5, seconds=0.2)
    std, count = boc61_metrics.cmc_std_m(uptime, code, carrier, CARRIER_HZ)
    assert np.isnan(std) and count == 0


def test_a_mutated_sign_of_the_carrier_term_would_be_caught():
    """
    A range change that is too fast for the local fit (a 3 s swing of 50 m) cancels
    between code and carrier only when they are combined with the right sign.
    """
    uptime, code, carrier = _synthetic_pair(0.5)
    swing_m = 50.0 * np.sin(2 * np.pi * uptime * 1e-3 / 3.0)
    code = code - swing_m / (C * 1e-3)
    carrier = carrier - swing_m / (C / CARRIER_HZ)
    right, _ = boc61_metrics.cmc_std_m(uptime, code, carrier, CARRIER_HZ)
    wrong, _ = boc61_metrics.cmc_std_m(uptime, code, -carrier, CARRIER_HZ)
    assert right == pytest.approx(0.5, rel=0.15)
    assert wrong > 20 * right


def test_lock_losses_count_pll_to_fll_changes_only():
    mode = np.array([0, 1, 1, 0, 0, 1, 0, 1, 1], dtype=bool)
    assert boc61_metrics.count_lock_losses(mode) == 2
    assert boc61_metrics.count_lock_losses(np.zeros(5, dtype=bool)) == 0


def test_settings_are_arguments_not_constants():
    uptime, code, carrier = _synthetic_pair(1.0)
    coarse = MetricSettings(cmc_window_s=30.0)
    std, _ = boc61_metrics.cmc_std_m(uptime, code, carrier, CARRIER_HZ, coarse)
    assert std == pytest.approx(1.0, rel=0.15)


def test_compare_refuses_runs_of_different_signals(tmp_path):
    from .test_tracking_io import FakeChannel, make_outputs

    paths = []
    for index, signal_type_id in enumerate(("GPS_L1C", "BDS_B1C")):
        paths.append(tracking_io.save_tracking_results(
            tmp_path / f"{index}.h5", {"G01": FakeChannel(make_outputs(seed=1))},
            collect_id="RX", signal_type_id=signal_type_id, samp_rate=25e6,
        ))
    runs = [tracking_io.load_tracking_results(p) for p in paths]
    with pytest.raises(ValueError, match="different signals"):
        boc61_metrics.compare_runs(runs[0], runs[1])


def test_wide_band_model_gains_pilot_cn0_on_a_wide_band_signal(tmp_path):
    """End to end: the same wide-band B1C samples, tracked by both models."""
    from .test_boc61_b1c import SIGNAL_ID, _track

    runs = {}
    for name, wide in (("default", False), ("variant", True)):
        adapter = _track(model_wide=wide, signal_wide=True, spacing=0.5, noise=4.0, buffers=60)
        path = tracking_io.save_tracking_results(
            tmp_path / f"{name}.h5", {SIGNAL_ID: adapter},
            collect_id="RX", signal_type_id="BDS_B1C", samp_rate=16e6,
            variant="wb" if wide else "",
        )
        runs[name] = tracking_io.load_tracking_results(path)
    assert runs["variant"].variant == "wb"
    comparison = boc61_metrics.compare_runs(
        runs["default"], runs["variant"], MetricSettings(cn0_skip_s=1.0, cmc_window_s=2.0)
    )
    assert [item.signal_id for item in comparison] == [SIGNAL_ID]
    item = comparison[0]
    assert np.isfinite(item.default.cn0_mean_dbhz) and np.isfinite(item.variant.cn0_mean_dbhz)
    text = boc61_metrics.format_comparison(comparison, "nb", "wb")
    assert SIGNAL_ID in text
    print(text)
    # The pilot gains up to 10*log10(33/29) = 0.56 dB (it keeps the BOC(6,1) 4/33 of its
    # power).  At 16 Msps part of that is lost to sampling: 0.33 dB measured.  The run
    # is seeded, so the bounds can be tight; a model that ignores the part gains nothing.
    assert 0.1 < item.cn0_change_db < 0.7


def test_compare_script_prints_a_table(tmp_path, capsys):
    import importlib.util
    from pathlib import Path

    from .test_tracking_io import FakeChannel, make_outputs

    paths = [
        tracking_io.save_tracking_results(
            tmp_path / f"{name}.h5", {"C01": FakeChannel(make_outputs(epochs=400, seed=seed))},
            collect_id="RX", signal_type_id="BDS_B1C", samp_rate=16e6, variant=variant,
        )
        for name, seed, variant in (("a", 1, ""), ("b", 2, "wb"))
    ]
    spec = importlib.util.spec_from_file_location(
        "compare_boc61", Path(__file__).resolve().parent.parent / "scripts" / "compare_boc61.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main([str(paths[0]), str(paths[1])]) == 0
    out = capsys.readouterr().out
    assert "BDS_B1C: default against wb" in out
    assert "C01" in out
