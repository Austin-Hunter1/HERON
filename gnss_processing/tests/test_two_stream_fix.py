"""
One fix from two radios that do not share a clock (D-070).

The 2026-10-14 recording has an L1 radio and an L5 radio, started 3.57 s apart and free
running.  Each has its own receiver clock, and the same GPS satellite is tracked twice
(L1 C/A and L5), as is the same BeiDou satellite (B1C and B2a).  The solver handles this
with one bias per stream and constellation, "bias group", named by the column id
`<sat>@<stream label>`.

The method is that of `test_navigation_multisystem.py`: take a world with a known receiver
position, give each stream a clock offset of its own, and require the position back.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from utils import navigation as nav
from utils import observables as obs

from .test_navigation import REFERENCE_ECEF
from .test_navigation_multisystem import (
    INTER_SYSTEM_BIAS_M,
    SETTINGS,
    build_two_system_truth,
    first_beidou_id,
)

# Epoch-by-epoch offsets of the second stream: its clock does not run like the first one.
SECOND_STREAM_GPS_OFFSET_M = np.array([2.0e5, 2.1e5, 2.2e5, 2.3e5])
SECOND_STREAM_BDS_OFFSET_M = np.array([2.5e5, 2.6e5, 2.7e5, 2.8e5])


def _part(observables, letter, offset_m):
    columns = [j for j, s in enumerate(observables.sat_ids) if s.startswith(letter)]
    return obs.Observables(
        epoch_uptime_ms=observables.epoch_uptime_ms,
        receive_time_s=observables.receive_time_s,
        sat_ids=[observables.sat_ids[j] for j in columns],
        transmit_time_s=observables.transmit_time_s[:, columns],
        pseudorange_m=observables.pseudorange_m[:, columns] + offset_m[:, None],
        time_system="GPST",
    )


def _two_streams():
    """Four parts: GPS and BeiDou of stream L1, then the same satellites again on stream L5."""
    ephemerides, observables = build_two_system_truth()
    zero = np.zeros(observables.num_epochs)
    parts = [
        _part(observables, "G", zero),
        _part(observables, "C", zero),
        _part(observables, "G", SECOND_STREAM_GPS_OFFSET_M),
        _part(observables, "C", SECOND_STREAM_BDS_OFFSET_M),
    ]
    types = ["GPS_L5", "BDS_B2A", "GPS_L5", "BDS_B2A"]
    labels = ["L1", "L1", "L5", "L5"]
    combined = obs.combine_observables(parts, types, labels=labels)
    both = {}
    for sat_id, eph in ephemerides.items():
        for label in ("L1", "L5"):
            both[f"{sat_id}@{label}"] = eph
    return both, combined


def test_a_bias_group_is_the_constellation_and_the_stream():
    assert nav.system_letter("G05") == "G"
    assert nav.system_letter("C27") == "C"
    assert nav.system_letter("G05@L1") == "G@L1"
    assert nav.system_letter("C27@L5") == "C@L5"


def test_the_reference_group_is_the_first_gps_one():
    assert nav.default_reference_system(["C@L1", "G@L5", "G@L1"]) == "G@L5"
    assert nav.default_reference_system(["C@L1", "C@L5"]) == "C@L1"
    assert nav.default_reference_system(["G"]) == "G"


def test_labels_make_the_same_satellite_on_two_signals_two_columns():
    _, combined = _two_streams()
    ids = combined.sat_ids
    assert len(ids) == len(set(ids))
    assert first_beidou_id() + "@L1" in ids and first_beidou_id() + "@L5" in ids
    assert combined.signal_type_ids[first_beidou_id() + "@L5"] == "BDS_B2A"


def test_labels_rename_the_anchors_too():
    ephemerides, observables = build_two_system_truth()
    first_id = [s for s in observables.sat_ids if s.startswith("G")][0]
    anchor = obs.TimeAnchor(sat_id=first_id, tow_s=1.0, code_phase_ms=0.0)
    zero = np.zeros(observables.num_epochs)
    part = _part(observables, "G", zero)
    first = part.sat_ids[0]
    part = dataclasses.replace(part, anchors={first: anchor})
    other = _part(observables, "G", zero)
    combined = obs.combine_observables([part, other], ["GPS_L5", "GPS_L1CA"], labels=["L1", "L5"])
    assert set(combined.anchors) == {first + "@L1"}
    assert combined.anchors[first + "@L1"].sat_id == first + "@L1"


def test_without_labels_a_repeated_satellite_is_still_refused():
    _, observables = build_two_system_truth()
    zero = np.zeros(observables.num_epochs)
    part = _part(observables, "G", zero)
    with pytest.raises(ValueError, match="more than one part"):
        obs.combine_observables([part, part], ["GPS_L5", "GPS_L1CA"])


def test_one_label_for_each_part():
    _, observables = build_two_system_truth()
    zero = np.zeros(observables.num_epochs)
    part = _part(observables, "G", zero)
    with pytest.raises(ValueError, match="one label"):
        obs.combine_observables([part, _part(observables, "C", zero)], ["GPS_L5", "BDS_B2A"], labels=["L1"])


def test_two_clocks_and_four_bias_groups_give_the_true_position():
    ephemerides, combined = _two_streams()
    series = nav.solve_series(
        combined, ephemerides, signal_type_id="GPS_L5", settings=SETTINGS,
        initial_position_ecef_m=REFERENCE_ECEF,
    )
    assert series.valid.all()
    assert np.abs(series.position_ecef_m - REFERENCE_ECEF).max() < 0.05
    assert np.all(series.num_satellites == 22)
    assert np.nanmax(np.abs(series.residuals_m)) < 0.05


def test_each_group_bias_is_what_that_stream_added():
    ephemerides, combined = _two_streams()
    series = nav.solve_series(
        combined, ephemerides, signal_type_id="GPS_L5", settings=SETTINGS,
        initial_position_ecef_m=REFERENCE_ECEF,
    )
    biases = series.inter_system_bias_m
    # The reference is the first GPS group, "G@L1".  The others are quoted against it.
    assert set(biases) == {"C@L1", "G@L5", "C@L5"}
    assert biases["C@L1"] == pytest.approx(np.full(4, INTER_SYSTEM_BIAS_M), abs=0.05)
    assert biases["G@L5"] == pytest.approx(SECOND_STREAM_GPS_OFFSET_M, abs=0.05)
    assert biases["C@L5"] == pytest.approx(INTER_SYSTEM_BIAS_M + SECOND_STREAM_BDS_OFFSET_M, abs=0.05)


def test_a_bad_satellite_on_one_signal_is_dropped_and_its_twin_kept():
    """RAIM names the column, not the satellite: the same satellite on the other signal stays."""
    ephemerides, combined = _two_streams()
    bad = first_beidou_id() + "@L5"
    column = combined.sat_ids.index(bad)
    pseudorange = combined.pseudorange_m.copy()
    pseudorange[:, column] += 400.0
    combined = dataclasses.replace(combined, pseudorange_m=pseudorange)
    series = nav.solve_series(
        combined, ephemerides, signal_type_id="GPS_L5", settings=SETTINGS,
        initial_position_ecef_m=REFERENCE_ECEF, residual_gate_m=100.0,
    )
    assert series.excluded_sat_ids[0] == [bad]
    assert np.abs(series.position_ecef_m - REFERENCE_ECEF).max() < 0.1
