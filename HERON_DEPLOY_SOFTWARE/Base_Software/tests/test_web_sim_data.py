"""Keep the herongnss.org simulation in step with the real ground station.

The in-browser simulation (website/sim, D-103) is a JavaScript port. It
reads two JSON files that website/tools/make_sim_data.py makes from this
code. These tests fail when the files are out of date, so a change to
the config templates or to the dashboard snapshot cannot silently break
the public simulation. To fix a failure, run in HERON_DEPLOY_SOFTWARE/:

    uv run python ../website/tools/make_sim_data.py

and check website/sim/heron_sim.js for the changed rule or field.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "website" / "tools" / "make_sim_data.py"
SIM = REPO / "website" / "sim"
DASHBOARD = (
    REPO / "HERON_DEPLOY_SOFTWARE" / "Base_Software" / "src" / "heron_base" / "web" / "index.html"
)

pytestmark = pytest.mark.skipif(not TOOL.exists(), reason="website/ is not in this checkout")


def _tool():
    """Import the generator script as a module."""
    spec = importlib.util.spec_from_file_location("make_sim_data", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _no_data_keys(snapshot: dict) -> dict:
    """Empty the dicts whose keys are data, not layout, before a shape compare.

    RTCM message numbers and temperature sensor names change with the
    machine and the moment. sim_shim.js empties the same two dicts.
    """
    out = dict(snapshot)
    out["gnss"] = dict(out["gnss"], rtcm_types={})
    out["telemetry"] = dict(out["telemetry"], temps_c={})
    return out


def test_sim_config_matches_templates():
    """sim_config.json holds the current values of the config templates."""
    committed = json.loads((SIM / "sim_config.json").read_text(encoding="utf-8"))
    assert committed == _tool().build_config()


def test_snapshot_template_matches_dashboard():
    """snapshot_template.json has the key layout of a real /api/state snapshot."""
    tool = _tool()
    committed = json.loads((SIM / "snapshot_template.json").read_text(encoding="utf-8"))
    fresh = tool.build_snapshot_template()
    assert tool.shape(_no_data_keys(committed)) == tool.shape(_no_data_keys(fresh))


def test_dashboard_page_has_sim_hooks():
    """The site build injects the sim before </head> and replaces these fetch calls."""
    page = DASHBOARD.read_text(encoding="utf-8")
    for hook in ("</head>", 'fetch("/api/state"', 'fetch("/api/command"'):
        assert hook in page
