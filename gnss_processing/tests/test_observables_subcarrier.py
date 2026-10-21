"""
The subcarrier correction of the BOC discriminator (double estimator) in the pseudorange.

A channel tracked with the double estimator logs two delays: `code_phase_ms` from the code
loop and `subcarrier_offset_chips`, how far the subcarrier loop sits from it.  The delay the
signal shows is their sum.  Measured on real B1C data this takes the pseudorange scatter
from 5-7 m to 1.6-2.1 m, and the opposite sign makes it 9-19 m, so the sign is tested here
rather than trusted.
"""

from __future__ import annotations

import numpy as np
import pytest

from utils import observables
from utils.tracking_channel import TapLayout, double_estimator_tap_layout, epl_tap_layout

B1C_CODE_RATE = 1.023e6  # chips per second


class _Outputs:
    """The few fields of `SignalTrackingOutputs` that the observables read."""

    def __init__(self, layout, offset_chips, n=50):
        self.tap_layout = layout
        self.valid = slice(0, n)
        self.uptime_epoch_ms = np.arange(n) * 10.0
        self.code_phase_ms = 3.0 + np.arange(n) * 10.0
        self.subcarrier_offset_chips = np.full(n, float(offset_chips))


def test_the_offset_is_added_with_a_plus_sign_and_converted_from_chips_to_milliseconds():
    outputs = _Outputs(double_estimator_tap_layout(0.5, 0.1), offset_chips=0.1023)
    refined = observables.refined_code_phase_ms(outputs, B1C_CODE_RATE)
    # 1.023 Mcps is 1023 chips per ms, so 0.1023 chip is 1e-4 ms (about 30 m of range).
    assert refined - outputs.code_phase_ms == pytest.approx(np.full(50, 1e-4))


def test_a_channel_without_a_subcarrier_loop_is_untouched():
    outputs = _Outputs(epl_tap_layout(0.5), offset_chips=0.2)
    assert np.array_equal(observables.refined_code_phase_ms(outputs, B1C_CODE_RATE), outputs.code_phase_ms)


def test_without_a_code_rate_the_code_phase_is_used_as_logged():
    outputs = _Outputs(double_estimator_tap_layout(0.5, 0.1), offset_chips=0.2)
    assert np.array_equal(observables.refined_code_phase_ms(outputs, None), outputs.code_phase_ms)


def test_interpolation_uses_the_refined_code_phase():
    outputs = _Outputs(double_estimator_tap_layout(0.5, 0.1), offset_chips=0.1023)
    grid = np.array([105.0, 205.0])
    plain = observables._interpolate_code_phase(outputs, grid)
    refined = observables._interpolate_code_phase(outputs, grid, code_rate_chips_per_sec=B1C_CODE_RATE)
    assert refined - plain == pytest.approx(np.full(2, 1e-4))


def test_form_observables_takes_one_rate_or_one_per_satellite():
    from utils.observables import TimeAnchor

    outputs = {"C01": _Outputs(double_estimator_tap_layout(0.5, 0.1), 0.1023),
               "C02": _Outputs(double_estimator_tap_layout(0.5, 0.1), 0.1023)}
    anchors = {sid: TimeAnchor(sat_id=sid, tow_s=1000.0, code_phase_ms=3.0, week=1084, time_system="BDT")
               for sid in outputs}
    grid = np.array([100.0, 200.0, 300.0])
    plain = observables.form_observables(outputs, anchors, epoch_uptime_ms=grid)
    one = observables.form_observables(outputs, anchors, epoch_uptime_ms=grid, code_rate_chips_per_sec=B1C_CODE_RATE)
    each = observables.form_observables(
        outputs, anchors, epoch_uptime_ms=grid,
        code_rate_chips_per_sec={"C01": B1C_CODE_RATE, "C02": None},
    )
    # 1e-4 ms later on the satellite clock is 1e-7 s, so a transmit time 1e-7 s later.
    assert one.transmit_time_s - plain.transmit_time_s == pytest.approx(np.full((3, 2), 1e-7))
    assert each.transmit_time_s[:, 0] - plain.transmit_time_s[:, 0] == pytest.approx(np.full(3, 1e-7))
    assert np.array_equal(each.transmit_time_s[:, 1], plain.transmit_time_s[:, 1])
