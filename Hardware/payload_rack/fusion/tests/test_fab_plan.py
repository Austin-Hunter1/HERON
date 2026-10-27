"""Tests for fab_plan: the laser and print layouts must hold every piece and never overlap."""
import os

import pytest

import fab_plan as fp
import rack_config as rc

SEED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rack_seed.toml")

# Pieces to make, from the README "Cut and print list" (Hardware/payload_rack/README.md).
CUT_LIST = {"LZR_bottom_plate": 1, "LZR_top_plate": 1, "LZR_rear_plate": 1, "LZR_side_wall": 2,
            "LZR_tray_sdr": 4, "LZR_tray_nuc": 1}
PRINT_LIST = {"ASA_rail_strip_sdr_bot": 4, "ASA_rail_strip_sdr_top": 4, "ASA_rail_strip_nuc_bot": 1,
              "ASA_rail_strip_nuc_top": 1, "ASA_latch_sdr_preload": 8, "ASA_latch": 3,
              "ASA_ssd_cage": 1, "ASA_ssd_sled": 1, "TPU_rail_sdr": 8, "TPU_rail_nuc": 2}


def _env(**changes):
    """Evaluate the seed in internal units (mm), with some literal values changed."""
    specs = rc.param_specs(rc.load_config(SEED))
    specs = [rc.ParamSpec(s.name, f"{changes[s.name]} mm", s.unit, s.comment, True, changes[s.name])
             if s.name in changes else s for s in specs]
    env = {}
    for spec in specs:
        env[spec.name] = rc.evaluate_expression(spec.expression, env)
    return env


def test_counts_match_cut_and_print_list():
    assert fp.part_counts(fp.LASER) == CUT_LIST
    assert fp.part_counts(fp.PRINT) == PRINT_LIST


def test_every_part_has_extents_and_labels_are_unique():
    for layout in fp.LAYOUTS:
        rows = fp.placements(layout)
        assert all(r["part"] in fp.EXTENTS for r in rows)
        assert len({r["label"] for r in rows}) == len(rows)


def test_seed_names_exist():
    env = _env()
    assert "FAB_GAP" in env and "FAB_SPACE" in env
    for ext in fp.EXTENTS.values():
        for expr in ext:
            for name in rc.expression_names(expr):
                assert name in env, name


# Why these cases: the user may change the sheet thickness T_ (outer walls), and
# the slot pitches change the rack width. The layout must stay clear in each case.
@pytest.mark.parametrize("changes", [{}, {"T_": 6.0}, {"T_": 2.0}, {"PITCH_B210_1": 45.0, "H_": 150.0},
                                     {"FAB_GAP": 2.0}])
def test_no_overlap_and_flat_on_plane(changes):
    env = _env(**changes)
    for layout in fp.LAYOUTS:
        items = fp.boxes(layout, env)
        assert fp.overlaps(items, env["FAB_GAP"]) == []
        for label, box in items:
            assert box[2] == pytest.approx(0.0, abs=1e-9), label    # lowest point on the plane
            assert box[0] >= -1e-9 and box[1] >= -1e-9, label       # inside the container quadrant
        sx, sy = (rc.evaluate_expression(e, env) for e in fp.layout_size(layout))
        assert max(b[3] for _, b in items) == pytest.approx(sx)
        assert max(b[4] for _, b in items) == pytest.approx(sy)


def test_layouts_clear_of_rack_and_of_each_other():
    env = _env()
    off = {k: [rc.evaluate_expression(e, env) for e in v] for k, v in fp.container_offsets().items()}
    assert off[fp.LASER][0] - (env["W_"] + env["T_"]) == pytest.approx(env["FAB_SPACE"])
    laser_sy = rc.evaluate_expression(fp.layout_size(fp.LASER)[1], env)
    assert off[fp.PRINT][1] - laser_sy == pytest.approx(env["FAB_SPACE"])


def test_overlaps_finds_a_clash():
    assert fp.overlaps([("a", [0, 0, 0, 10, 10, 1]), ("b", [15, 0, 0, 20, 10, 1])], 10.0) == [("a", "b")]
    assert fp.overlaps([("a", [0, 0, 0, 10, 10, 1]), ("b", [20, 0, 0, 30, 10, 1])], 10.0) == []
