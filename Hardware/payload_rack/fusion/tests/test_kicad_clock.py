"""Tests for kicad_clock: compare with the frozen heron_rack.py functions."""
import math
import os

import pytest

import kicad_clock as kc

hr = pytest.importorskip("heron_rack")


def test_read_matches_heron_rack():
    mine = kc.read_clock_board(hr.CLK_PCB)
    ref = hr._clk_read()
    assert ref, "KiCad file not found by heron_rack"
    assert set(mine) == set(ref)
    for key, (x, y, fp) in ref.items():
        assert (mine[key].kx, mine[key].ky, mine[key].footprint) == (x, y, fp)
    assert len(mine) == 10


def test_kind_matches():
    for ref, (x, y, fp) in hr._clk_read().items():
        assert kc.jack_kind(ref, fp) == hr.clk_jack_kind(ref, fp)


def test_mate_pairs_match_heron_rack():
    jacks = kc.read_clock_board(hr.CLK_PCB)
    x0, _x1, _z0, z1 = hr.clk_outline()
    axes = [(hr.SLOTS[i][0], k, (hr.sdr_sma_x(i), hr.Z_BOARD + hr.SDR_REAR_SMA[k]))
            for i in hr.sdr_indices() for k in ("ref_a", "ref_b")]
    mine = kc.mate_pairs(jacks, axes, x0, z1)
    ref = hr.mate_pairs()
    assert len(mine) == len(ref) == 8
    for a, b in zip(mine, ref):
        assert a["sdr"] == b["sdr"] and a["key"] == b["key"] and a["jack"] == b["jack"]
        assert math.dist(a["axis_xz"], b["axis_xz"]) < 1e-9
        assert math.dist(a["jack_xz"], b["jack_xz"]) < 1e-9


def test_missing_file_gives_empty_and_axis_fallback():
    assert kc.read_clock_board("/no/such.kicad_pcb") == {}
    out = kc.mate_pairs({}, [("S", "ref_a", (1.0, 2.0))], 0.0, 0.0)
    assert out[0]["jack"] is None and out[0]["jack_xz"] == (1.0, 2.0)


def test_jack_params_order_and_names():
    names = list(kc.jack_params(kc.read_clock_board(hr.CLK_PCB)))
    assert names[:4] == ["CLK_J1_KX", "CLK_J1_KY", "CLK_J2_KX", "CLK_J2_KY"]
    assert names[-1] == "CLK_J10_KY"


def test_jack_groups_match_heron_rack():
    """J1 is the JST box, J3..J10 the SMP jacks, J2 the SMA jack (clock_board_ref)."""
    jacks = kc.read_clock_board(hr.CLK_PCB)
    jst, smp, sma = kc.jack_groups(jacks)
    assert jst == ["J1"] and sma == ["J2"]
    assert smp == ["J%d" % n for n in range(3, 11)]
    for ref in smp:
        assert hr.clk_jack_kind(ref, jacks[ref].footprint) == "smp"
