"""Tests for rack_config: validation of the seed and Python evaluation."""
import copy
import os

import pytest

import rack_config as rc

SEED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rack_seed.toml")


def _good():
    return {
        "params": {"PA": {"value": 1.0, "unit": "mm", "comment": "a"},
                   "PB": {"expr": "PA * 2", "unit": "mm", "comment": "b"}},
        "materials": {"laser": "x", "rigid": "y", "flex": "z"},
        "slots": [{"name": "S", "kind": "nuc", "pitch": "PA"}],
        "kicad": {"pcb": "x.kicad_pcb"},
        "ref_look": {"library": "L", "base": "B", "name": "N", "red": 0.2, "green": 0.55, "blue": 0.3, "alpha": 0.6},
    }


def test_good_config_passes():
    rc.validate_config(_good())


@pytest.mark.parametrize("mutate,text", [
    (lambda c: c["params"]["PA"].pop("unit"), "missing 'unit'"),
    (lambda c: c["params"]["PA"].update(unit="furlong"), "unknown unit"),
    (lambda c: c["params"]["PA"].update(expr="1"), "exactly one"),
    (lambda c: c["params"]["PA"].pop("value"), "exactly one"),
    (lambda c: c["params"]["PB"].update(expr="PA * PC"), "'PC'"),
    (lambda c: c["params"].update(T={"value": 1, "unit": "mm"}), "Fusion refuses"),
    (lambda c: c["params"]["PA"].update(value="x"), "must be a number"),
    (lambda c: c["slots"][0].update(kind="radar"), "kind must be"),
    (lambda c: c["slots"][0].update(pitch="NOPE"), "pitch parameter"),
    (lambda c: c["materials"].pop("flex"), "flex"),
    (lambda c: c.pop("kicad"), "kicad"),
    (lambda c: c.pop("ref_look"), "[ref_look] missing text value 'base'"),
    (lambda c: c["ref_look"].update(alpha=1.5), "'alpha' must be a number from 0 to 1"),
])
def test_bad_config_fails_with_clear_message(mutate, text):
    cfg = copy.deepcopy(_good())
    mutate(cfg)
    with pytest.raises(rc.ConfigError) as err:
        rc.validate_config(cfg)
    assert text in str(err.value)


def test_error_lists_all_problems():
    cfg = _good()
    cfg["params"]["PA"].pop("unit")
    cfg["slots"][0]["kind"] = "radar"
    with pytest.raises(rc.ConfigError) as err:
        rc.validate_config(cfg)
    assert "missing 'unit'" in str(err.value) and "kind must be" in str(err.value)


def test_missing_file_message():
    with pytest.raises(rc.ConfigError, match="not found"):
        rc.load_config("/no/such/file.toml")


def test_evaluate_expression_units_and_min():
    env = {"X": 10.0}
    assert rc.evaluate_expression("X * 2 + 1 mm", env) == 21.0
    assert rc.evaluate_expression("min(5 mm; X)", env) == 5.0
    assert abs(rc.evaluate_expression("5.54 mm / cos(30 deg)", env) - 6.397040982621054) < 1e-12


def test_seed_loads_and_evaluates():
    cfg = rc.load_config(SEED)
    values = rc.evaluate_params(rc.param_specs(cfg))
    assert abs(values["W_"] - 166.0) < 1e-9
    assert abs(values["ADAPTER_HEX_ANGLE"] - 30.0) < 1e-9


def test_seed_matches_cadquery_reference():
    """Every seed value that has a heron_rack.py twin must equal it."""
    pytest.importorskip("cadquery")
    from ref_names import reference_values
    cfg = rc.load_config(SEED)
    values = rc.evaluate_params(rc.param_specs(cfg))
    ref = reference_values()
    bad = {n: (values[n], r) for n, r in ref.items() if n in values and abs(values[n] - r) > 1e-9}
    assert not bad, bad
    # the ones that matter most must be present
    for name in ("W_", "D", "CLK_Y0", "REAR_Y0", "SSD_Y0", "STRIP_Y1", "TRAY_Z0", "LATCH_Z",
                 "TRAY_X_B200_2", "REAR_SCREW_X1", "CLK_X0"):
        assert name in values and name in ref
