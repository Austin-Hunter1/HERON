"""
GPS L1C with the pilot modelled as BOC(1,1) only (narrow band, D-077, Phase T3).

Why: the default L1C model is the full TMBOC pilot.  The narrow-band variant
leaves out the 4 BOC(6,1) chips in every 33.  These tests show that it still
locks on the full signal, and that its pilot prompt is 29/33 of the full model's:
the BOC(6,1) chips do not correlate with a BOC(1,1) replica.  Runs at 25 Msps.
"""

from __future__ import annotations

import numpy as np
import pytest

from utils import gps_l1c, tracking_channel
from utils.signal_interfaces import GpsL1C, SignalOptions

from .test_l1c import _track

NARROW = GpsL1C.with_options(SignalOptions(boc61=False))


def _pilot_prompt_and_state(signal_type):
    channel = _track(200, nav_bits=False, signal_type=signal_type)
    outputs = channel.outputs
    magnitudes = np.abs(outputs.prompt_corr[outputs.valid, :])[-50:].mean(axis=0)
    return channel, magnitudes


def test_narrow_band_model_locks_on_the_full_tmboc_signal():
    channel = _track(300, signal_type=NARROW)
    outputs = channel.outputs
    assert channel.loop_state.mode is tracking_channel.TrackingLoopMode.PLL
    assert outputs.doppler_freq_hz[outputs.output_index - 1] == pytest.approx(800.0, abs=0.5)


def test_narrow_band_pilot_prompt_is_the_boc11_share_of_the_full_prompt():
    _, wide = _pilot_prompt_and_state(GpsL1C)
    _, narrow = _pilot_prompt_and_state(NARROW)
    share = 1.0 - len(gps_l1c.TMBOC_PATTERN_INDICES) / gps_l1c.TMBOC_PATTERN_LENGTH
    # The data component is BOC(1,1) throughout, so it must not change.
    assert narrow[0] == pytest.approx(wide[0], rel=0.02)
    assert narrow[1] / wide[1] == pytest.approx(share, rel=0.03)
