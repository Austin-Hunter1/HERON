"""
A position fix from GPS L1C, with the ephemerides decoded from CNAV-2 (D-079).

Why: notebook 02 had no L1C path.  This builds a sky, sends each satellite's orbit and
clock through the CNAV-2 subframe 2 encoder, the symbol stream and the decoder, makes
the pseudoranges those DECODED ephemerides produce, and requires the solver to give back
the receiver position.  A wrong field scale, a lost sign, the L1C group delay, or a TOI
and ITOW mix-up all break the recovery.
"""

from __future__ import annotations

import numpy as np
import pytest

from utils import multi_signal, navigation as nav, observables as obs
from utils.nav import cnav2
from utils.nav import primitives as prim
from utils.nav import symbols as nav_symbols
from utils.nav.ephemeris import CNAV_A_REF, CNAV_OMEGA_DOT_REF, SPEED_OF_LIGHT

from .test_navigation import REFERENCE_ECEF, TOE, visible_constellation
from .test_navigation_multisystem import RECEIVER_BIAS_M, _transit_solution

C = SPEED_OF_LIGHT


def _subframe_2_values(eph) -> dict[str, float]:
    return {
        "wn": eph.week % 8192, "itow": int(TOE // 7200) % 84, "top": TOE, "toe": TOE,
        "delta_a": eph.sqrt_a**2 - CNAV_A_REF, "M0": eph.M0, "e": eph.e, "omega": eph.omega,
        "Omega0": eph.Omega0, "i0": eph.i0,
        "delta_Omega_dot": eph.Omega_dot - CNAV_OMEGA_DOT_REF,
        "af0": eph.af0, "tgd": 3e-9, "isc_l1cp": 1e-9, "isc_l1cd": -2e-9,
    }


def _stream_for(eph, rng):
    frames = []
    for k in range(3):
        sf2 = 1.0 - 2.0 * cnav2.build_subframe_2(_subframe_2_values(eph))
        sf3 = 1.0 - 2.0 * prim.crc24q_append(rng.integers(0, 2, 250).astype(np.uint8))
        frames.append(cnav2.build_frame(
            toi=10 + k,
            subframe_2=np.concatenate([sf2, rng.normal(0, 1, 600)]),
            subframe_3=np.concatenate([sf3, rng.normal(0, 1, 274)]),
        ))
    soft = np.concatenate([rng.normal(0, 1, 321)] + frames)
    return nav_symbols.SymbolStream(
        values=soft.astype(complex), uptime_ms=10.0 * np.arange(len(soft)),
        code_phase_ms=10.0 * np.arange(len(soft)), symbol_period_ms=10,
        signal_type_id="GPS_L1C", epochs_per_symbol=1, dropped_epochs=0,
        bit_sync=None, data_in_quadrature=False,
    )


@pytest.fixture(scope="module")
def decoded_sky():
    rng = np.random.default_rng(7)
    truth = visible_constellation(6)
    decoded = {}
    for sat_id, eph in truth.items():
        channel = multi_signal.decode_cnav2_stream(_stream_for(eph, rng), sat_id)
        assert channel.failure is None, channel.failure
        decoded[sat_id] = channel.ephemeris
    return truth, decoded


def test_decoded_orbits_are_close_to_the_transmitted_ones(decoded_sky):
    truth, decoded = decoded_sky
    for sat_id, eph in truth.items():
        want = eph.orbit_state(TOE + 600.0).position_ecef_m
        got = decoded[sat_id].orbit_state(TOE + 600.0).position_ecef_m
        assert np.linalg.norm(want - got) < 0.5, sat_id


def test_a_fix_from_decoded_l1c_ephemerides_recovers_the_receiver(decoded_sky):
    _, decoded = decoded_sky
    sat_ids = sorted(decoded)
    epochs = 3
    true_receive = TOE + np.arange(epochs) * 1.0
    transmit = np.zeros((epochs, len(sat_ids)))
    for i in range(epochs):
        for j, sat_id in enumerate(sat_ids):
            eph = decoded[sat_id]
            t_tx = _transit_solution(eph, true_receive[i], native_offset_s=0.0)
            # What the satellite stamps: its clock error and the L1C pilot group delay.
            transmit[i, j] = t_tx + eph.clock_correction_s(t_tx) + obs.signal_group_delay_s(eph, "GPS_L1C")
    nominal_receive = true_receive + RECEIVER_BIAS_M / C
    observables = obs.Observables(
        epoch_uptime_ms=np.arange(epochs) * 1000.0, receive_time_s=nominal_receive, sat_ids=sat_ids,
        transmit_time_s=transmit, pseudorange_m=C * (nominal_receive[:, None] - transmit),
        week=2257, time_system="GPST", signal_type_ids={s: "GPS_L1C" for s in sat_ids},
    )
    settings = nav.CorrectionSettings(
        satellite_clock=True, group_delay=True, sagnac=True, troposphere=False, ionosphere=False
    )
    series = nav.solve_series(
        observables, decoded, signal_type_id="GPS_L1C", settings=settings,
        initial_position_ecef_m=REFERENCE_ECEF,
    )
    assert series.valid.all()
    assert np.linalg.norm(series.position_ecef_m - REFERENCE_ECEF, axis=1).max() < 1.0
    assert series.clock_bias_m == pytest.approx(RECEIVER_BIAS_M, abs=1.0)
