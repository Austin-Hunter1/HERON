"""Unit tests for link_budget.py. They use small configs, not the real design values."""

from __future__ import annotations

import copy
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import link_budget as lb  # noqa: E402

EXAMPLE = Path(__file__).resolve().parents[1] / "rf_chain.example.toml"

BASE = {
    "environment": {"reference_temperature_k": 290.0, "case": "typical"},
    "bands": {"B": {"frequency_hz": 1e9, "noise_bandwidth_hz": 1e6, "min_signal_dbm": -100.0}},
    "components": {
        "lna": {"gain_db": {"B": 30.0}, "gain_db_worst": {"B": 28.0}, "nf_db": {"B": 2.0}},
        "loss": {"gain_db": {"B": -3.0}},
        "sdr": {"nf_db": {"B": 10.0}, "max_input_dbm": -15.0},
    },
    "chains": [{
        "name": "t", "bands": ["B"], "antenna_gain_dbic": {"B": 0.0},
        "stages": ["lna", "loss"], "sdr": "sdr", "sdr_gain_db": 40.0,
    }],
}


def make(**changes):
    """Return a validated config built from BASE with top-level changes."""
    raw = copy.deepcopy(BASE)
    raw.update(changes)
    return lb.Config.model_validate(raw)


def test_thermal_noise_density_at_290k():
    assert lb.noise_dbm_per_hz(290.0) == pytest.approx(-174.0, abs=0.05)


def test_cascade_matches_friis_by_hand():
    res = lb.budget(make(), make().chains[0], "B")
    f = 10 ** 0.2 + (10 ** 0.3 - 1) / 10 ** 3 + (10 ** 1.0 - 1) / (10 ** 3 * 10 ** -0.3)
    assert res.nf_total_db == pytest.approx(10 * math.log10(f), abs=1e-6)
    assert res.gain_to_sdr_input_db == pytest.approx(27.0)
    assert res.total_gain_db == pytest.approx(67.0)


def test_pure_loss_uses_nf_equal_to_loss():
    assert make().stage_values("loss", "B") == (-3.0, 3.0)


def test_zero_db_link_needs_no_nf():
    raw = copy.deepcopy(BASE)
    raw["components"]["link"] = {"gain_db": {"B": 0.0}}
    assert lb.Config.model_validate(raw).stage_values("link", "B") == (0.0, 0.0)


def test_cn0_and_headroom():
    cfg = make()
    res = lb.budget(cfg, cfg.chains[0], "B")
    assert res.cn0_dbhz == pytest.approx(-100.0 - lb.noise_dbm_per_hz(290.0) - res.nf_total_db)
    assert res.headroom_db == pytest.approx(-15.0 - res.noise_at_sdr_input_dbm)


def test_worst_case_uses_worst_map():
    cfg = make()
    cfg.environment.case = "worst"
    assert cfg.stage_values("lna", "B")[0] == 28.0


def test_unknown_component_is_an_error():
    raw = copy.deepcopy(BASE)
    raw["chains"][0]["stages"] = ["lna", "nope"]
    with pytest.raises(ValueError, match="unknown component 'nope'"):
        lb.Config.model_validate(raw)


def test_missing_band_data_is_an_error():
    raw = copy.deepcopy(BASE)
    raw["bands"]["C"] = {"frequency_hz": 2e9, "noise_bandwidth_hz": 1e6, "min_signal_dbm": -100.0}
    raw["chains"][0]["bands"] = ["B", "C"]
    raw["chains"][0]["antenna_gain_dbic"]["C"] = 0.0
    with pytest.raises(ValueError, match="no gain_db for band C"):
        lb.Config.model_validate(raw)


def test_gain_stage_without_nf_is_an_error():
    raw = copy.deepcopy(BASE)
    del raw["components"]["lna"]["nf_db"]
    with pytest.raises(ValueError, match="no nf_db"):
        lb.Config.model_validate(raw)


def test_unknown_key_is_an_error():
    raw = copy.deepcopy(BASE)
    raw["components"]["lna"]["gian_db"] = {"B": 1.0}
    with pytest.raises(ValueError):
        lb.Config.model_validate(raw)


def test_dc_report_is_tbd_without_supply_voltage():
    cfg = make()
    cfg.chains[0].active_antenna = "lna"
    assert "TBD" in lb.dc_report(cfg, cfg.chains[0])[0]


def test_dc_report_flags_out_of_range_supply():
    raw = copy.deepcopy(BASE)
    raw["components"]["lna"].update(supply_min_v=3.0, supply_max_v=5.0, supply_current_ma=30.0)
    raw["components"]["tee"] = {"gain_db": {"B": -0.5}, "dc_resistance_ohm": 5.0,
                                "dc_max_v": 30.0, "dc_max_ma": 500.0}
    raw["chains"][0].update(active_antenna="lna", bias_tee="tee", supply_voltage_v=12.0)
    cfg = lb.Config.model_validate(raw)
    text = "\n".join(lb.dc_report(cfg, cfg.chains[0]))
    assert "OUT OF RANGE" in text and "0.15 V" in text


def test_example_config_loads_and_renders():
    cfg = lb.load_config(EXAMPLE)
    report = lb.render(cfg)
    assert "Patch (ANT-03)" in report and "Flight (ANT-04)" in report
    assert "(provisional)" in report


def test_main_reports_bad_file(tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text("[environment]\n", encoding="utf-8")
    assert lb.main([str(bad)]) == 2
    assert "bad config" in capsys.readouterr().err


def test_json_output_has_config_and_results(tmp_path):
    out = tmp_path / "budget.json"
    assert lb.main([str(EXAMPLE), "--json", str(out), "--output", str(tmp_path / "r.md")]) == 0
    data = __import__("json").loads(out.read_text(encoding="utf-8"))
    assert set(data) == {"config", "results", "requirements"}
    assert "L1" in data["results"]["Patch (ANT-03)"]


def with_requirements(target, losses=()):
    """BASE config plus a [requirements] section."""
    raw = copy.deepcopy(BASE)
    raw["requirements"] = {"target_cn0_dbhz": target, "max_cn0_loss_db": list(losses)}
    return lb.Config.model_validate(raw)


def test_required_gain_reproduces_the_target_when_applied():
    cfg = with_requirements(70.0)
    chain = cfg.chains[0]
    res = lb.requirement(cfg, chain, "B")
    assert res.required_gain_db is not None
    # Put the required gain into the LNA and check that the budget now gives the target.
    cfg.components["lna"].gain_db["B"] = res.required_gain_db
    assert lb.budget(cfg, chain, "B").cn0_dbhz == pytest.approx(70.0, abs=1e-6)


def test_required_gain_status_meets_and_short():
    cfg = with_requirements(70.0)
    assert lb.requirement(cfg, cfg.chains[0], "B").status == "meets the target"
    cfg.components["lna"].gain_db["B"] = 1.0
    assert lb.requirement(cfg, cfg.chains[0], "B").status == "short of the target"


def test_target_above_ideal_receiver_is_reported():
    cfg = with_requirements(90.0)  # BASE signal is -100 dBm: ideal C/N0 is 74 dB-Hz.
    res = lb.requirement(cfg, cfg.chains[0], "B")
    assert res.required_gain_db is None and "ideal receiver" in res.status


def test_target_below_first_stage_nf_needs_no_finite_gain():
    cfg = with_requirements(72.5)  # Allowed NF 1.5 dB is below the LNA's 2 dB.
    res = lb.requirement(cfg, cfg.chains[0], "B")
    assert res.required_gain_db is None and "no gain is enough" in res.status


def test_gain_for_cn0_loss_matches_cascade():
    cfg = with_requirements(70.0, losses=[0.5])
    chain = cfg.chains[0]
    res = lb.requirement(cfg, chain, "B")
    gain = res.gain_for_loss_db["0.5"]
    assert gain is not None
    cfg.components["lna"].gain_db["B"] = gain
    nf = lb.budget(cfg, chain, "B").nf_total_db
    assert nf == pytest.approx(2.0 + 0.5, abs=1e-6)  # LNA NF + the allowed loss


def test_example_report_has_requirement_section():
    report = lb.render(lb.load_config(EXAMPLE))
    assert "Gain requirement for 43 dB-Hz" in report
