"""
Tests for the BOC(6,1) toggle plumbing (D-077, Phase T1).

Why: the default of every signal must stay as it was, and a variant must stay
apart from the default in its name, its policy row and its file.
"""

from __future__ import annotations

import pytest

from utils import catalog, tracking_io
from utils.signal_interfaces import (
    ACQUISITION_POLICIES,
    TRACKING_POLICIES,
    BdsB1C,
    BdsB2a,
    GpsL1C,
    GpsL1CA,
    SignalOptions,
    policy_for,
)


@pytest.mark.parametrize("signal_type", [BdsB1C, GpsL1C, GpsL1CA, BdsB2a])
@pytest.mark.parametrize("options", [None, SignalOptions(), SignalOptions(boc61=None)])
def test_default_options_return_the_same_class(signal_type, options):
    """No options, or `None`, must not make a new class: a default run is unchanged."""
    assert signal_type.with_options(options) is signal_type
    assert signal_type.variant_tag() == ""
    assert signal_type.policy_key() == signal_type.signal_type_id


def test_option_equal_to_the_default_returns_the_class():
    assert BdsB1C.with_options(SignalOptions(boc61=False)) is BdsB1C
    assert GpsL1C.with_options(SignalOptions(boc61=True)) is GpsL1C


def test_b1c_wide_band_adds_a_third_component():
    wide = BdsB1C.with_options(SignalOptions(boc61=True))
    assert wide is not BdsB1C
    assert wide.signal_type_id == "BDS_B1C"
    assert wide.variant_tag() == "wb"
    assert wide(7).component_names == ("B1CD", "B1CP", "B1CP61")
    assert BdsB1C(7).component_names == ("B1CD", "B1CP")


def test_l1c_narrow_band_keeps_the_component_names_but_drops_the_pattern():
    narrow = GpsL1C.with_options(SignalOptions(boc61=False))
    assert narrow.variant_tag() == "nb"
    assert narrow(7).component_names == GpsL1C(7).component_names
    pilot_wide = GpsL1C(7).code_set.components[1].subcarrier
    pilot_narrow = narrow(7).code_set.components[1].subcarrier
    assert pilot_wide.pattern is not None
    assert pilot_narrow.pattern is None


def test_signal_without_boc61_rejects_the_option():
    with pytest.raises(ValueError, match="no BOC\\(6,1\\) part"):
        GpsL1CA.with_options(SignalOptions(boc61=True))


def test_variant_policy_falls_back_to_the_type_row():
    """A variant needs its own row only when it must differ."""
    narrow = GpsL1C.with_options(SignalOptions(boc61=False))
    assert policy_for(TRACKING_POLICIES, narrow) is TRACKING_POLICIES["GPS_L1C"]
    assert policy_for(ACQUISITION_POLICIES, narrow) is ACQUISITION_POLICIES["GPS_L1C"]
    wide = BdsB1C.with_options(SignalOptions(boc61=True))
    assert policy_for(TRACKING_POLICIES, wide) is TRACKING_POLICIES["BDS_B1C:wb"]
    assert policy_for(TRACKING_POLICIES, BdsB1C) is TRACKING_POLICIES["BDS_B1C"]


def test_default_file_name_does_not_change_and_variant_is_a_suffix():
    base = tracking_io.tracking_filename("RX7", "BDS_B1C", 0.0, 60_000.0)
    assert base == "RX7_BDS_B1C_0-60s.h5"
    tagged = tracking_io.tracking_filename("RX7", "BDS_B1C", 0.0, 60_000.0, variant="wb")
    assert tagged == "RX7_BDS_B1C_0-60s_wb.h5"


def test_catalog_reads_the_variant_back(tmp_path):
    for variant in ("", "nb", "wb"):
        name = tracking_io.tracking_filename("RX7", "GPS_L1C", 0.0, 60_000.0, variant=variant)
        match = catalog._TRACKING_FILENAME.match(name)
        assert match is not None, name
        assert match["signal"] == "GPS_L1C"
        assert (match["variant"] or "") == variant
