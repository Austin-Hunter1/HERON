"""
The headless notebook runner must accept the BeiDou signals (`--set SIGNAL_ID=BdsB2a` and
`BdsB1C`).  The test patches the real notebooks without running them: it checks that the
override lands in the config cell, that the class name exists, and that the notebook
holds a table row for the signal in every per-signal dictionary that the config cell
indexes with `SIGNAL_ID`.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from utils import signal_interfaces

ROOT = Path(__file__).resolve().parent.parent


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_notebook", ROOT / "scripts" / "run_notebook.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load_runner()


def _notebook(number: str) -> dict:
    path = ROOT / "notebooks" / runner.NOTEBOOKS[number]
    return json.loads(path.read_text(encoding="utf-8"))


def _code(nb: dict) -> str:
    return "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")


@pytest.mark.parametrize("number", ["01", "02"])
@pytest.mark.parametrize("signal", ["BdsB2a", "BdsB1C", "GalE5a"])
def test_the_runner_can_set_a_beidou_signal(number, signal):
    nb = _notebook(number)
    applied = runner.patch_notebook(nb, {"SIGNAL_ID": signal})
    assert applied == {"SIGNAL_ID"}
    lines = [l for l in _code(nb).split("\n") if l.startswith("SIGNAL_ID =")]
    assert len(lines) == 1 and lines[0].startswith(f"SIGNAL_ID = {signal}")
    assert lines[0].count("#") == 1, "the trailing comment is kept"
    assert hasattr(signal_interfaces, signal)


@pytest.mark.parametrize("number", ["01", "02"])
@pytest.mark.parametrize("signal", ["BdsB2a", "BdsB1C", "GalE5a"])
def test_every_per_signal_table_of_the_notebook_has_the_signal(number, signal, request):
    if signal == "GalE5a" and number == "02":
        # Notebook 02 gets E5a with the F/NAV decoder (Phase E6 of the L1C/E5a plan).
        request.applymarker(pytest.mark.xfail(strict=True, reason="E5a message decoding is Phase E4 to E6"))
    """A dict indexed by SIGNAL_ID that lacks a signal fails with a KeyError at run time."""
    code = _code(_notebook(number))
    # Each `{...}[SIGNAL_ID]` or `{...}[SIGNAL_ID.signal_type_id]` block names the signal.
    blocks = []
    for marker in ("}[SIGNAL_ID]", "}[BASE_SIGNAL]", "}[SIGNAL_ID.signal_type_id]"):
        start = 0
        while (end := code.find(marker, start)) != -1:
            begin = code.rfind("= {", 0, end)
            blocks.append(code[begin:end])
            start = end + len(marker)
    assert blocks, "no per-signal tables found: the test needs an update"
    names = {signal, signal_interfaces.SIGNALS[
        {"BdsB2a": "BDS_B2A", "BdsB1C": "BDS_B1C", "GalE5a": "GAL_E5A"}[signal]
    ].signal_type_id}
    for block in blocks:
        assert any(name in block for name in names), block[:80]


def test_notebook_01_tracks_b1c_with_the_boc_discriminator():
    """
    A single delay loop with 0.5 chip taps sits off the peak of a BOC(1,1) and loses about
    11 dB (D-070), so the B1C entry must enable the double estimator: both subcarrier values
    positive, and accepted by the loop parameters.
    """
    from utils import tracking_channel

    code = _code(_notebook("01"))
    start = code.index("DLL_SETTINGS = {")
    block = code[start:code.index("}[BASE_SIGNAL]", start)]
    entry = block[block.index("BdsB1C:"):]
    body = entry.split("dict(", 1)[1]
    depth, end = 1, 0
    for end, char in enumerate(body):
        depth += (char == "(") - (char == ")")
        if depth == 0:
            break
    arguments = body[:end]
    settings = eval("dict(" + arguments + ")")
    assert settings["subcarrier_bandwidth_hz"] > 0.0 and settings["subcarrier_chip_spacing"] > 0.0
    params = tracking_channel.TrackingLoopParameters(
        PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0, coherent_duration_ms=5, **settings
    )
    assert params.tracks_subcarrier
