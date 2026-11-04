"""
Galileo F/NAV decoding on E5a (D-081): coding checked against the ICD Annex D.1 example,
page fields round trip, the stream decoder (sync, polarity, CRC), the ephemeris assembly,
and a position fix from decoded Galileo ephemerides.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from utils import multi_signal, navigation as nav, observables as obs
from utils.nav import fnav
from utils.nav import primitives as prim
from utils.nav import symbols as nav_symbols
from utils.nav.ephemeris import GAL_E5A_BGD_FACTOR, SPEED_OF_LIGHT

from .test_navigation import REFERENCE_ECEF, TOE, visible_constellation
from .test_navigation_multisystem import RECEIVER_BIAS_M, _transit_solution

C = SPEED_OF_LIGHT


@pytest.fixture(scope="module")
def annex_d():
    from utils import gal_e5a

    return gal_e5a.load_code_tables()["fnav_fec_example"]


def _bits(text: str) -> np.ndarray:
    return np.array([int(c) for c in text], dtype=np.uint8)


# --- channel coding: the ICD's own example -------------------------------------------

def test_encoder_reproduces_the_annex_d_example(annex_d):
    info = _bits(annex_d["input_244_bits"])
    symbols = fnav.encode_page(info)
    assert "".join(map(str, symbols[:12])) == fnav.SYNC_PATTERN
    assert "".join(map(str, symbols[12:])) == annex_d["interleaved_488_symbols"]


def test_decoder_inverts_the_annex_d_example(annex_d):
    coded = fnav.soft_symbols(_bits(annex_d["interleaved_488_symbols"]))
    bits = fnav.decode_coded_symbols(coded)
    assert "".join(map(str, bits)) == annex_d["input_244_bits"]


def test_the_annex_d_page_is_a_coding_example_not_a_crc_example(annex_d):
    """
    The example is a dummy page (type 63) whose 24 "CRC" bits are an arbitrary pattern,
    not a CRC.  So the CRC convention is NOT checked by the ICD example; it is the CRC-24Q
    of GPS CNAV (checked there) and of the MATLAB receiver (same polynomial, seed 0).
    This test records that, so nobody relies on the example for the CRC.
    """
    info = _bits(annex_d["input_244_bits"])
    assert prim.int_from_bits(info[:6]) == 63
    assert not prim.crc24q_check(info[: fnav.PAGE_BITS])


def test_viterbi_corrects_errors_in_the_symbols(annex_d):
    coded = fnav.soft_symbols(_bits(annex_d["interleaved_488_symbols"]))
    rng = np.random.default_rng(2)
    coded[rng.choice(488, 12, replace=False)] *= -1.0  # 12 wrong symbols
    bits = fnav.decode_coded_symbols(coded)
    assert "".join(map(str, bits)) == annex_d["input_244_bits"]


# --- page fields ---------------------------------------------------------------------

def test_the_layout_table_has_no_overlap_and_ends_inside_the_214_bits():
    for page_type, table in fnav.FIELDS.items():
        spans = sorted((start, start + width - 1, name) for name, (start, width, *_r) in table.items())
        for (_, end, name), (start, _, nxt) in zip(spans, spans[1:]):
            assert end < start, (page_type, name, nxt)
        assert spans[-1][1] <= fnav.CRC_COVERED_BITS


def test_page_fields_round_trip():
    values = {"svid": 11, "iod_nav": 517, "t0c": 36000.0, "af0": -2.1e-4, "af1": 3.0e-12,
              "af2": 0.0, "bgd_e1e5a": 4.0e-9, "wn": 1200, "tow": 345678, "e5a_hs": 0, "e5a_dvs": 0}
    bits = fnav.build_page(1, values)
    assert prim.crc24q_check(bits[: fnav.PAGE_BITS])
    parsed = fnav.parse_page(bits)
    for name, value in values.items():
        scale = fnav.FIELDS[1][name][3]
        assert parsed[name] == pytest.approx(value, abs=scale), name


def test_a_field_that_does_not_fit_is_refused():
    with pytest.raises(ValueError, match="does not fit"):
        fnav.build_page(1, {"svid": 100})


# --- the stream decoder --------------------------------------------------------------

def _stream_of(pages_info, lead=137, sign=1.0, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    soft = np.concatenate(
        [rng.normal(0, 1, lead)] + [fnav.soft_symbols(fnav.encode_page(p)) for p in pages_info]
    )
    soft = sign * (soft + noise * rng.normal(0, 1, len(soft)))
    return nav_symbols.SymbolStream(
        values=1j * soft, uptime_ms=500.0 + 20.0 * np.arange(len(soft)),
        code_phase_ms=900.0 + 20.0 * np.arange(len(soft)), symbol_period_ms=20,
        signal_type_id="GAL_E5A", epochs_per_symbol=1, dropped_epochs=0, bit_sync=None,
        data_in_quadrature=True,
    )


def _ephemeris_pages(iod=77, tow=1000, **overrides):
    p1 = {"svid": 5, "iod_nav": iod, "t0c": 7200.0, "af0": 1e-5, "bgd_e1e5a": 3e-9, "wn": 1250, "tow": tow}
    p2 = {"iod_nav": iod, "M0": 0.25, "e": 0.0002, "sqrt_a": 5440.6, "Omega0": -0.4, "Omega_dot": -5e-9 / np.pi}
    p3 = {"iod_nav": iod, "i0": 0.31, "omega": 0.2, "t0e": 7200.0, "Cuc": 1e-6, "Crs": 20.0}
    p4 = {"iod_nav": iod, "Cic": 2e-7, "Cis": -1e-7}
    p1.update(overrides)
    return [fnav.build_page(t, v) for t, v in ((1, p1), (2, p2), (3, p3), (4, p4))]


@pytest.mark.parametrize("sign", [1.0, -1.0])
def test_pages_are_found_in_either_polarity(sign):
    stream = _stream_of(_ephemeris_pages(), sign=sign)
    result = fnav.decode(stream.soft)
    assert [p.page_type for p in result.pages] == [1, 2, 3, 4]
    assert [p.symbol_index for p in result.pages] == [137 + 500 * k for k in range(4)]
    assert result.inverted == (sign < 0)


def test_noise_gives_no_page_and_no_guess():
    noise = np.random.default_rng(4).normal(0, 1, 6000)
    assert fnav.decode(noise).pages == []


def test_a_corrupted_page_is_dropped_and_the_others_survive():
    infos = _ephemeris_pages()
    stream = _stream_of(infos)
    soft = stream.soft.copy()
    start = 137 + 500  # page 2: wreck the coded symbols so the CRC cannot pass
    soft[start + 20 : start + 400] = np.random.default_rng(9).normal(0, 1, 380)
    result = fnav.decode(soft)
    assert [p.page_type for p in result.pages] == [1, 3, 4]


def test_an_ephemeris_needs_all_four_pages_with_one_iod():
    pages = _ephemeris_pages()
    result = fnav.decode(_stream_of(pages[:3]).soft)
    assert fnav.assemble_ephemeris(result, "E05") is None
    mixed = pages[:3] + _ephemeris_pages(iod=78)[3:]
    assert fnav.assemble_ephemeris(fnav.decode(_stream_of(mixed).soft), "E05") is None


def test_a_good_channel_gives_an_anchor_in_gps_time_and_an_ephemeris():
    stream = _stream_of(_ephemeris_pages(tow=1000))
    decoded = multi_signal.decode_fnav_stream(stream, "E05")
    assert decoded.failure is None, decoded.failure
    assert decoded.anchor.time_system == "GPST"
    assert decoded.anchor.tow_s == 1000
    assert decoded.anchor.week == 1250 + 1024
    assert decoded.anchor_uptime_ms == pytest.approx(500.0 + 20.0 * 137)
    assert decoded.anchor.code_phase_ms == pytest.approx(900.0 + 20.0 * 137)
    eph = decoded.ephemeris
    assert eph.sat_id == "E05" and eph.toe == 7200.0 and eph.toc == 7200.0
    assert eph.sqrt_a == pytest.approx(5440.6, abs=2.0**-19)
    assert eph.bgd_e1e5a == pytest.approx(3e-9, abs=2.0**-32)


def test_the_decoder_follows_the_signal_type(monkeypatch):
    stream = _stream_of(_ephemeris_pages())
    monkeypatch.setattr(multi_signal.nav_symbols, "extract", lambda outputs, signal_type_id: stream)
    assert multi_signal.decode_beidou_channel("x", "E05", "GAL_E5A").anchor.tow_s == 1000


def test_time_of_week_consistency_follows_the_symbol_spacing():
    # Two page-1 pages 2 pages (1000 symbols = 20 s) apart must be 20 s apart in time.
    pages = [_ephemeris_pages(tow=1000)[0], _ephemeris_pages()[1], _ephemeris_pages(tow=1020)[0]]
    assert fnav.decode(_stream_of(pages).soft).sow_is_consistent()
    wrong = [_ephemeris_pages(tow=1000)[0], _ephemeris_pages()[1], _ephemeris_pages(tow=1030)[0]]
    assert not fnav.decode(_stream_of(wrong).soft).sow_is_consistent()


# --- a fix from decoded Galileo ephemerides ------------------------------------------

def _page_values(eph, iod=9):
    from utils.nav.ephemeris import SEMICIRCLES_TO_RADIANS  # noqa: F401

    p1 = {"svid": int(eph.sat_id[1:]), "iod_nav": iod, "t0c": eph.toc, "af0": eph.af0,
          "bgd_e1e5a": 4e-9, "wn": 1233, "tow": int(TOE)}
    p2 = {"iod_nav": iod, "M0": eph.M0, "e": eph.e, "sqrt_a": eph.sqrt_a,
          "Omega0": eph.Omega0, "Omega_dot": eph.Omega_dot, "i_dot": eph.i_dot}
    p3 = {"iod_nav": iod, "i0": eph.i0, "omega": eph.omega, "t0e": eph.toe, "deln": eph.deln}
    p4 = {"iod_nav": iod}
    return [fnav.build_page(t, v) for t, v in ((1, p1), (2, p2), (3, p3), (4, p4))]


def test_a_fix_from_decoded_galileo_ephemerides_recovers_the_receiver():
    truth = visible_constellation(6)
    decoded = {}
    for k, (sat_id, eph) in enumerate(sorted(truth.items())):
        galileo_id = f"E{k + 1:02d}"
        channel = multi_signal.decode_fnav_stream(
            _stream_of(_page_values(type(eph)(**{**eph.__dict__, "sat_id": galileo_id})), seed=k), galileo_id
        )
        assert channel.failure is None, channel.failure
        decoded[galileo_id] = channel.ephemeris
    sat_ids = sorted(decoded)
    epochs = 3
    true_receive = TOE + np.arange(epochs) * 1.0
    transmit = np.zeros((epochs, len(sat_ids)))
    for i in range(epochs):
        for j, sat_id in enumerate(sat_ids):
            eph = decoded[sat_id]
            t_tx = _transit_solution(eph, true_receive[i], native_offset_s=0.0)
            transmit[i, j] = t_tx + eph.clock_correction_s(t_tx) + obs.signal_group_delay_s(eph, "GAL_E5A")
    nominal_receive = true_receive + RECEIVER_BIAS_M / C
    observables = obs.Observables(
        epoch_uptime_ms=np.arange(epochs) * 1000.0, receive_time_s=nominal_receive, sat_ids=sat_ids,
        transmit_time_s=transmit, pseudorange_m=C * (nominal_receive[:, None] - transmit),
        week=2257, time_system="GPST", signal_type_ids={s: "GAL_E5A" for s in sat_ids},
    )
    settings = nav.CorrectionSettings(
        satellite_clock=True, group_delay=True, sagnac=True, troposphere=False, ionosphere=False
    )
    series = nav.solve_series(
        observables, decoded, signal_type_id="GAL_E5A", settings=settings,
        initial_position_ecef_m=REFERENCE_ECEF,
    )
    assert series.valid.all()
    assert np.linalg.norm(series.position_ecef_m - REFERENCE_ECEF, axis=1).max() < 1.0


def test_the_e5a_group_delay_is_the_bgd_scaled_by_the_frequency_ratio():
    from utils.nav.ephemeris import GalEphemeris

    decoded = fnav.assemble_ephemeris(fnav.decode(_stream_of(_ephemeris_pages()).soft), "E05")
    assert isinstance(decoded, GalEphemeris)
    assert obs.signal_group_delay_s(decoded, "GAL_E5A") == pytest.approx(-GAL_E5A_BGD_FACTOR * 3e-9, abs=GAL_E5A_BGD_FACTOR * 2.0**-32)
    assert GAL_E5A_BGD_FACTOR == pytest.approx(1.7933, abs=1e-3)
    with pytest.raises(ValueError, match="E5a only"):
        obs.signal_group_delay_s(decoded, "GPS_L5")
