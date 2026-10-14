"""
Decoding a B2a tracking channel into an anchor and an ephemeris (`utils.multi_signal`),
and the common epoch grid that joins two tracking runs.

The symbol stream is built directly from encoded frames, so these tests need no signal
generator and no tracker: they check the decode, the matching rules (D-055) and the
failure messages, which is what the notebook relies on.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from utils import multi_signal, observables
from utils.nav import bcnav2
from utils.nav import primitives as prim
from utils.nav import symbols as nav_symbols


def _frame(prn: int, message_type: int, sow_count: int, fields=()) -> np.ndarray:
    message = bcnav2.build_message(prn=prn, message_type=message_type, sow_count=sow_count)
    for start, length, raw in fields:
        message = bcnav2.set_data_field(message, start, length, raw)
    return bcnav2.encode_frame(prim.crc24q_append(message[:264]))


def _stream(frames: list[np.ndarray], lead: int = 7) -> nav_symbols.SymbolStream:
    bits = np.concatenate(frames) if frames else np.zeros(0)
    soft = np.concatenate([np.ones(lead), bcnav2.soft_symbols(bits)])
    # Data is on the quadrature axis on B2a, as `symbols.extract` returns it.
    return nav_symbols.SymbolStream(
        values=1j * soft,
        uptime_ms=1000.0 + 5.0 * np.arange(len(soft)),
        code_phase_ms=2000.0 + 5.0 * np.arange(len(soft)),
        symbol_period_ms=5,
        signal_type_id="BDS_B2A",
        epochs_per_symbol=1,
        dropped_epochs=0,
        bit_sync=None,
        data_in_quadrature=True,
    )


def _set_of_three(prn=7, iode=0x6B, iodc=0x26B):
    return [
        _frame(prn, 10, 5000, [(54, 8, iode), (73, 2, 3)]),
        _frame(prn, 11, 5001),
        _frame(prn, 30, 5002, [(112, 10, iodc)]),
    ]


@pytest.fixture
def patched_extract(monkeypatch):
    streams = {}
    monkeypatch.setattr(multi_signal.nav_symbols, "extract", lambda outputs, signal_type_id: streams[outputs])
    return streams


def test_a_good_channel_gives_an_anchor_an_ephemeris_and_the_uptime(patched_extract):
    patched_extract["ch7"] = _stream(_set_of_three())
    decoded = multi_signal.decode_bcnav2_channel("ch7", "C07")
    assert decoded.failure is None
    assert decoded.ephemeris.sat_id == "C07"
    assert decoded.anchor.sat_id == "C07"
    assert decoded.anchor.time_system == "BDT"
    assert decoded.anchor.tow_s == 5000 * 3.0
    assert decoded.anchor.week == decoded.ephemeris.week
    # The anchor is the first decoded frame: symbol 7, so uptime 1000 + 7 * 5 ms.
    assert decoded.anchor_uptime_ms == pytest.approx(1035.0)
    assert decoded.anchor.code_phase_ms == pytest.approx(2035.0)


def test_a_channel_with_no_symbols_is_reported(patched_extract):
    patched_extract["ch"] = nav_symbols.SymbolStream(
        values=np.zeros(0, dtype=complex), uptime_ms=np.zeros(0), code_phase_ms=np.zeros(0),
        symbol_period_ms=5, signal_type_id="BDS_B2A", epochs_per_symbol=0, dropped_epochs=0,
        bit_sync=None, data_in_quadrature=True,
    )
    decoded = multi_signal.decode_bcnav2_channel("ch", "C07")
    assert decoded.anchor is None
    assert "never reached" in decoded.failure and decoded.failure.startswith("C07")


def test_a_channel_with_noise_only_has_no_frame(patched_extract):
    noise = np.random.default_rng(0).normal(0, 1, 2000)
    stream = _stream([])
    stream = nav_symbols.SymbolStream(
        values=1j * noise, uptime_ms=np.arange(2000) * 5.0, code_phase_ms=np.arange(2000) * 5.0,
        symbol_period_ms=5, signal_type_id="BDS_B2A", epochs_per_symbol=1, dropped_epochs=0,
        bit_sync=None, data_in_quadrature=True,
    )
    patched_extract["noise"] = stream
    decoded = multi_signal.decode_bcnav2_channel("noise", "C07")
    assert decoded.anchor is None and "no frame survived the CRC" in decoded.failure


def test_frames_without_a_matched_clock_give_no_anchor(patched_extract):
    """Types 10 and 11 alone place no satellite: the clock of type 30 is needed (D-055)."""
    patched_extract["ch"] = _stream(_set_of_three()[:2])
    decoded = multi_signal.decode_bcnav2_channel("ch", "C07")
    assert decoded.anchor is None and decoded.ephemeris is None
    assert "no matched type 10 + 11 + 30 set" in decoded.failure


def test_a_clock_with_the_wrong_iodc_gives_no_anchor(patched_extract):
    patched_extract["ch"] = _stream(_set_of_three(iode=0x6B, iodc=0x26C))
    assert multi_signal.decode_bcnav2_channel("ch", "C07").anchor is None


def test_the_wrong_prn_is_not_accepted(patched_extract):
    patched_extract["ch"] = _stream(_set_of_three(prn=9))
    decoded = multi_signal.decode_bcnav2_channel("ch", "C07")
    assert decoded.anchor is None  # the channel tracked C07 but the frames say 9


def test_a_run_collects_anchors_ephemerides_and_failures(patched_extract):
    patched_extract["a"] = _stream(_set_of_three(prn=7))
    patched_extract["b"] = _stream(_set_of_three(prn=9, iode=0x10, iodc=0x110), lead=3)
    patched_extract["c"] = _stream(_set_of_three(prn=11)[:2])
    channels = {
        sid: SimpleNamespace(outputs=key)
        for sid, key in {"C07": "a", "C09": "b", "C11": "c"}.items()
    }

    class FakeRun(dict):
        """Minimal stand-in for a tracking run: a mapping plus `signal_ids`."""

        signal_ids = list(channels)

    run = FakeRun(channels)
    decoded = multi_signal.decode_bcnav2_run(run)
    assert sorted(decoded.anchors) == ["C07", "C09"]
    assert sorted(decoded.ephemerides) == ["C07", "C09"]
    assert decoded.anchor_uptime_ms["C09"] == pytest.approx(1000.0 + 5.0 * 3)
    assert len(decoded.failures) == 1 and decoded.failures[0].startswith("C11")


def test_common_epoch_grid_is_the_span_every_channel_shares():
    def outputs(start, stop):
        uptime = np.arange(start, stop, 5.0)
        return SimpleNamespace(valid=slice(0, len(uptime)), uptime_epoch_ms=uptime)

    run_a = {"G01": outputs(100.0, 5000.0), "G02": outputs(300.0, 4000.0)}
    run_b = {"C01": outputs(200.0, 4500.0)}
    grid = observables.common_epoch_grid([run_a, run_b], epoch_interval_ms=100.0)
    assert grid[0] == 300.0
    assert grid[-1] < 4000.0 and grid[-1] >= 3900.0
    assert np.allclose(np.diff(grid), 100.0)
    with pytest.raises(ValueError, match="do not overlap"):
        observables.common_epoch_grid([{"a": outputs(0.0, 100.0)}, {"b": outputs(200.0, 300.0)}])
    with pytest.raises(ValueError, match="enough epochs"):
        observables.common_epoch_grid([{}])
