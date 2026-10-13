"""
`utils.gap_consensus`: channels of one recording agree on the time its holes
dropped.

The scenarios are the ones seen on a real L5 collect (20 ms code period): two
groups of channels split the same overflows differently, and one group's wrap of
a true 10.65 ms gap lands on the wrong whole-period branch.  Their episode totals
still agree modulo the period, and the vote moves the minority branch.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from utils import gap_consensus as gc
from utils import tracking_channel, tracking_supervisor

PERIOD = 20.0
CFG = gc.GapConsensusConfig()


def hole(sid, segment, start, end, gap, period=PERIOD):
    return gc.Hole(sid, segment, start, end, gap, period)


def split_episode():
    """
    Four channels see the overflows as holes of 0.015 and 7.620 ms (total
    7.635).  Four see them as -9.348 and -3.017 ms (total -12.365): the same
    time, 20 ms apart, because a true gap of 10.652 ms wrapped to -9.348.
    """
    holes = []
    for sid in ("G03", "G10", "G23", "G32"):
        holes += [hole(sid, 1, 86.3e3, 86.9e3, 0.015), hole(sid, 2, 87.2e3, 87.9e3, 7.620)]
    for sid in ("G01", "G13", "G25", "G28"):
        holes += [hole(sid, 1, 86.3e3, 87.0e3, -9.348), hole(sid, 2, 87.6e3, 88.2e3, -3.017)]
    return holes


# --------------------------------------------------------------------------
# find_episodes
# --------------------------------------------------------------------------


def test_overlapping_holes_of_different_channels_form_one_episode():
    holes = [hole("G01", 1, 10e3, 11e3, 0.3), hole("G02", 1, 10.2e3, 11.4e3, 0.3)]
    assert len(gc.find_episodes(holes, 0.0)) == 1


def test_separate_overflows_are_separate_episodes():
    holes = [hole("G01", 1, 10e3, 11e3, 0.3), hole("G01", 2, 20e3, 21e3, 0.2)]
    assert len(gc.find_episodes(holes, 0.0)) == 2


def test_pad_chains_nearby_episodes():
    holes = [hole("G01", 1, 10e3, 11e3, 0.3), hole("G02", 1, 11.2e3, 12e3, 0.2)]
    assert len(gc.find_episodes(holes, 0.0)) == 2
    assert len(gc.find_episodes(holes, 250.0)) == 1


def test_a_hole_inside_a_long_one_does_not_end_the_episode():
    holes = [
        hole("G01", 1, 10e3, 20e3, 0.3),
        hole("G02", 1, 11e3, 12e3, 0.3),
        hole("G03", 1, 15e3, 16e3, 0.3),
    ]
    assert len(gc.find_episodes(holes, 0.0)) == 1


# --------------------------------------------------------------------------
# resolve_episode
# --------------------------------------------------------------------------


def test_agreeing_channels_are_left_alone():
    episode = [hole(f"G{i:02d}", 1, 10e3, 11e3, 0.322) for i in range(1, 6)]
    result = gc.resolve_episode(episode, CFG)
    assert result.shift_ms == {}
    assert result.dropped_segments == {}
    assert result.outliers == []


def test_minority_branch_is_moved_a_whole_period():
    episode = [
        hole("G01", 1, 10e3, 11e3, 0.3),
        hole("G02", 1, 10e3, 11e3, 0.3),
        hole("G03", 1, 10e3, 11e3, 0.3),
        hole("G04", 1, 10e3, 11e3, 0.3 - PERIOD),
    ]
    result = gc.resolve_episode(episode, CFG)
    assert result.shift_ms == {"G04": PERIOD}
    assert result.first_shifted_segment == {"G04": 1}


def test_the_real_l5_split_moves_the_negative_group_onto_the_positive_one():
    result = gc.resolve_episode(split_episode(), CFG)
    assert set(result.shift_ms) == {"G01", "G13", "G25", "G28"}
    assert all(shift == PERIOD for shift in result.shift_ms.values())
    # The shift applies from the segment after the LAST hole of the episode.
    assert set(result.first_shifted_segment.values()) == {2}
    # Their in-between segment (between hole 1 and 2) cannot be vouched for.
    assert result.dropped_segments == {s: [1] for s in ("G01", "G13", "G25", "G28")}
    assert result.outliers == []


def test_the_tie_goes_to_the_branch_nearest_zero():
    """Equal groups on branches 20 ms apart: the one nearer zero stays put."""
    episode = (
        [hole(f"A{i}", 1, 0, 1e3, 7.6) for i in range(2)]
        + [hole(f"B{i}", 1, 0, 1e3, 27.6) for i in range(2)]
    )
    result = gc.resolve_episode(episode, CFG)
    assert set(result.shift_ms) == {"B0", "B1"}
    assert all(shift == -PERIOD for shift in result.shift_ms.values())


def test_a_larger_group_wins_over_the_branch_nearest_zero():
    episode = (
        [hole(f"A{i}", 1, 0, 1e3, -12.0) for i in range(3)]
        + [hole(f"B{i}", 1, 0, 1e3, 8.0) for i in range(2)]
    )
    result = gc.resolve_episode(episode, CFG)
    assert set(result.shift_ms) == {"B0", "B1"}
    assert all(shift == -PERIOD for shift in result.shift_ms.values())


def test_a_channel_that_disagrees_modulo_the_period_is_an_outlier_not_shifted():
    episode = [
        hole("G01", 1, 0, 1e3, 0.3),
        hole("G02", 1, 0, 1e3, 0.3),
        hole("G03", 1, 0, 1e3, 0.3),
        hole("G04", 1, 0, 1e3, 0.9),
    ]
    result = gc.resolve_episode(episode, CFG)
    assert result.shift_ms == {}
    assert result.outliers == ["G04"]


def test_two_groups_that_disagree_modulo_the_period_make_no_change():
    episode = [
        hole("G01", 1, 0, 1e3, 0.3), hole("G02", 1, 0, 1e3, 0.3),
        hole("G03", 1, 0, 1e3, 5.0), hole("G04", 1, 0, 1e3, 5.0),
    ]
    result = gc.resolve_episode(episode, CFG)
    assert result.shift_ms == {}
    assert "no majority" in result.note


def test_one_channel_has_nothing_to_compare_against():
    result = gc.resolve_episode([hole("G01", 1, 0, 1e3, 0.3)], CFG)
    assert result.shift_ms == {}
    assert "nothing to compare" in result.note


def test_agreement_tolerance_is_a_setting():
    episode = [hole("G01", 1, 0, 1e3, 0.30), hole("G02", 1, 0, 1e3, 0.34)]
    assert gc.resolve_episode(episode, CFG).note == ""
    tight = gc.GapConsensusConfig(agreement_tol_ms=0.01)
    assert gc.resolve_episode(episode, tight).note != ""


def test_mixed_code_periods_are_not_compared():
    episode = [hole("G01", 1, 0, 1e3, 0.3, 1.0), hole("G02", 1, 0, 1e3, 0.3, 20.0)]
    assert "mixed code periods" in gc.resolve_episode(episode, CFG).note


def test_an_in_between_segment_of_an_agreeing_channel_is_kept():
    episode = [
        hole("G01", 1, 0, 1e3, 0.1), hole("G01", 2, 2e3, 3e3, 0.2),
        hole("G02", 1, 0, 3e3, 0.3),
    ]
    result = gc.resolve_episode(episode, CFG)
    assert result.shift_ms == {}
    assert result.dropped_segments == {}


# --------------------------------------------------------------------------
# Live supervisors
# --------------------------------------------------------------------------


def make_segment(start_ms, stop_ms, code_phase_offset=0.0):
    """A real SignalTrackingOutputs holding 5 ms epochs, code phase == uptime."""
    uptime = np.arange(start_ms, stop_ms, 5.0)
    outputs = tracking_channel.SignalTrackingOutputs(capacity=len(uptime) + 10)
    outputs.uptime_epoch_ms[: len(uptime)] = uptime
    outputs.code_phase_ms[: len(uptime)] = uptime + code_phase_offset
    outputs.output_index = len(uptime)
    channel = SimpleNamespace(loop_params=None, policy=None)
    return SimpleNamespace(outputs=outputs, channel=channel, signal=None)


def make_supervisor(gap_ms, hole_bounds, period=PERIOD):
    """One supervisor with a segment before, between and after its holes."""
    spans = [(0, 1000)]
    for lo, hi in hole_bounds:
        spans.append((hi, hi + 1000))
    segments, offset = [], 0.0
    for i, (a, b) in enumerate(spans):
        if i:
            offset += gap_ms[i - 1]
        segments.append(make_segment(a, b, offset))
    return SimpleNamespace(
        segments=segments,
        implied_gap_ms_by_segment=list(gap_ms),
        hole_bounds_ms_by_segment=list(hole_bounds),
        ambiguity_ms=period,
        dropped_segments=set(),
    )


def test_holes_from_supervisors_reads_what_try_reacquire_recorded():
    sup = make_supervisor([0.3], [(1000.0, 1500.0)])
    holes = gc.holes_from_supervisors({"G01": sup, "G02": SimpleNamespace(ambiguity_ms=None)})
    assert holes == [gc.Hole("G01", 1, 1000.0, 1500.0, 0.3, PERIOD)]


def test_apply_shifts_the_segments_after_the_episode_only():
    good = make_supervisor([0.3], [(1000.0, 1500.0)])
    bad = make_supervisor([0.3 - PERIOD], [(1000.0, 1500.0)])
    other = make_supervisor([0.3], [(1000.0, 1500.0)])
    before_first = bad.segments[0].outputs.code_phase_ms.copy()
    report = gc.apply_to_supervisors({"G01": good, "G02": other, "G03": bad})

    assert list(report.shifted()) == ["G03"]
    np.testing.assert_array_equal(bad.segments[0].outputs.code_phase_ms, before_first)
    after = bad.segments[1].outputs
    # After the shift the bad channel's code phase matches the good one's.
    np.testing.assert_allclose(
        after.code_phase_ms[after.valid], good.segments[1].outputs.code_phase_ms[after.valid]
    )


def test_apply_leaves_the_unused_tail_of_a_segment_untouched():
    good = make_supervisor([0.3], [(1000.0, 1500.0)])
    bad = make_supervisor([0.3 - PERIOD], [(1000.0, 1500.0)])
    other = make_supervisor([0.3], [(1000.0, 1500.0)])
    gc.apply_to_supervisors({"G01": good, "G02": other, "G03": bad})
    outputs = bad.segments[1].outputs
    assert np.all(outputs.code_phase_ms[outputs.output_index:] == 0.0)


def test_apply_marks_dropped_segments_and_merge_leaves_them_out():
    sups = {}
    for sid in ("G01", "G02", "G03"):
        sups[sid] = make_supervisor([0.3], [(1000.0, 1500.0)])
    # G03 sees the same time as two holes whose branches disagree with the total.
    sups["G03"] = make_supervisor(
        [0.2 - PERIOD, 0.1], [(1000.0, 1200.0), (1300.0, 1500.0)]
    )
    report = gc.apply_to_supervisors(sups)
    assert sups["G03"].dropped_segments == {1}
    assert sups["G01"].dropped_segments == set()
    assert report.table_rows()

    sups["G03"].adapter = sups["G03"].segments[-1]
    merged = tracking_supervisor.build_merged_adapter(sups["G03"])
    expected = sum(
        seg.outputs.output_index
        for i, seg in enumerate(sups["G03"].segments)
        if i != 1
    )
    assert merged.outputs.output_index == expected
