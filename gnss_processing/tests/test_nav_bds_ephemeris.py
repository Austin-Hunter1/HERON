"""
BeiDou ephemeris: the orbit and clock algorithms of BDS-SIS-ICD-B2a-1.0 (7.5, 7.6,
7.7), the assembly of an ephemeris from decoded B-CNAV2 frames, the RINEX 3 reader,
and SP3 files that hold BeiDou satellites.

What is NOT tested here: agreement with a real SP3 or RINEX file.  The cloud session
that wrote these tests cannot reach CDDIS.  `scripts/compare_bds_orbits.py` makes
that comparison on a machine that can.

The orbit tests compare the module with a short independent calculation written in
this file: it solves Kepler's equation by bisection and builds the position with
rotation matrices, where the module uses Newton iteration and expanded formulas.
"""

from __future__ import annotations

import dataclasses
import gzip
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from utils import observables, rinex3_nav, time_systems
from utils.nav import bcnav2
from utils.nav import primitives as prim
from utils.nav.ephemeris import (
    BDS_A_REF_M,
    BDS_MU_EARTH,
    BDS_OMEGA_E_DOT,
    BDS_PI,
    BDS_RELATIVISTIC_F,
    BDS_SAT_TYPE_GEO,
    BDS_SAT_TYPE_IGSO,
    BDS_SAT_TYPE_MEO,
    BdsEphemeris,
    CnavEphemeris,
)

FRAMES_PATH = Path(__file__).parent / "data" / "bcnav2_real_frames_20261014.json"


def make_ephemeris(**overrides) -> BdsEphemeris:
    """A circular MEO orbit with every correction zero, unless overridden."""
    fields = dict(
        sat_id="C13", week=1084, toe=262_800.0, sat_type=BDS_SAT_TYPE_MEO,
        delta_a=0.0, a_dot=0.0, delta_n0=0.0, delta_n0_dot=0.0,
        M0=0.3, e=0.0, omega=0.1, Omega0=-0.4, Omega_dot=-2.6e-9, i0=0.3,
        i_dot=0.0, Cis=0.0, Cic=0.0, Crs=0.0, Crc=0.0, Cus=0.0, Cuc=0.0,
        toc=262_800.0,
    )
    fields.update(overrides)
    return BdsEphemeris(**fields)


# --- an independent orbit calculation --------------------------------------

def _kepler_by_bisection(mean_anomaly: float, e: float) -> float:
    m = (mean_anomaly + np.pi) % (2 * np.pi) - np.pi
    low, high = -np.pi, np.pi
    for _ in range(80):
        mid = 0.5 * (low + high)
        if mid - e * np.sin(mid) < m:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high) + (mean_anomaly - m)


def _rz(angle):
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def _rx(angle):
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def reference_position(eph: BdsEphemeris, t: float) -> np.ndarray:
    """ICD Table 7-9 with explicit rotations."""
    tk = time_systems.wrap_time_of_week(t - eph.toe)
    a0 = BDS_A_REF_M[eph.sat_type] + eph.delta_a
    ak = a0 + eph.a_dot * tk
    n = np.sqrt(BDS_MU_EARTH / a0**3) + (eph.delta_n0 + 0.5 * eph.delta_n0_dot * tk) * BDS_PI
    mean = eph.M0 * BDS_PI + n * tk
    big_e = _kepler_by_bisection(mean, eph.e)
    nu = np.arctan2(np.sqrt(1 - eph.e**2) * np.sin(big_e), np.cos(big_e) - eph.e)
    phi = nu + eph.omega * BDS_PI
    u = phi + eph.Cus * np.sin(2 * phi) + eph.Cuc * np.cos(2 * phi)
    r = ak * (1 - eph.e * np.cos(big_e)) + eph.Crs * np.sin(2 * phi) + eph.Crc * np.cos(2 * phi)
    i = eph.i0 * BDS_PI + eph.i_dot * BDS_PI * tk + eph.Cis * np.sin(2 * phi) + eph.Cic * np.cos(2 * phi)
    node = eph.Omega0 * BDS_PI + (eph.Omega_dot * BDS_PI - BDS_OMEGA_E_DOT) * tk - BDS_OMEGA_E_DOT * eph.toe
    return _rz(node) @ _rx(i) @ np.array([r * np.cos(u), r * np.sin(u), 0.0])


# --- orbit -----------------------------------------------------------------

def test_circular_orbit_has_the_reference_radius_and_period():
    eph = make_ephemeris()
    radii = [np.linalg.norm(eph.orbit_state(eph.toe + dt).position_ecef_m) for dt in (0, 900, 3000)]
    assert radii == pytest.approx([27_906_100.0] * 3, abs=1e-6)
    # In the orbital plane a full revolution takes 2 pi / n0.
    period = 2 * np.pi / np.sqrt(BDS_MU_EARTH / 27_906_100.0**3)
    assert 12.5 * 3600 < period < 13.2 * 3600  # BeiDou MEO is about 12.9 h


@pytest.mark.parametrize("dt", [0.0, 600.0, -1500.0, 3600.0])
@pytest.mark.parametrize("sat_type", [BDS_SAT_TYPE_MEO, BDS_SAT_TYPE_IGSO])
def test_orbit_matches_the_independent_calculation(sat_type, dt):
    eph = make_ephemeris(
        sat_type=sat_type, delta_a=-1234.5, a_dot=2e-3, delta_n0=3e-12, delta_n0_dot=-4e-20,
        e=0.0123, M0=1.1, omega=-0.7, i0=0.31, i_dot=1.5e-13, Omega_dot=-1.4e-9,
        Cis=-1e-7, Cic=2e-7, Crs=45.0, Crc=-30.0, Cus=3e-6, Cuc=-2e-6,
    )
    got = eph.orbit_state(eph.toe + dt).position_ecef_m
    assert got == pytest.approx(reference_position(eph, eph.toe + dt), abs=1e-6)


def test_kepler_case_with_a_large_eccentricity():
    eph = make_ephemeris(e=0.4, M0=2.0)
    got = eph.orbit_state(eph.toe + 1000.0).position_ecef_m
    assert got == pytest.approx(reference_position(eph, eph.toe + 1000.0), abs=1e-5)
    # r = a (1 - e cos E): the perigee distance is smaller than the apogee one.
    near = np.linalg.norm(eph.orbit_state(eph.toe).position_ecef_m)
    assert 27_906_100.0 * (1 - 0.4) - 1 <= near <= 27_906_100.0 * (1 + 0.4) + 1


def test_orbit_uses_bdcs_constants_not_gps_ones():
    """A rotation rate or mu from GPS would move the satellite by about a decimetre."""
    eph = make_ephemeris(Omega_dot=0.0)
    state = eph.orbit_state(eph.toe + 3000.0)
    # Same orbit with GPS's earth rotation rate: the node differs by (7.2921151467 -
    # 7.2921150) e-5 rad/s x 3000 s = 4.4e-8 rad -> metres at 2.8e7 m.
    from utils.nav import ephemeris as e

    assert e.OMEGA_E_DOT != BDS_OMEGA_E_DOT
    gps_like = e._orbit_from_kepler_elements(
        semi_major_axis_m=27_906_100.0,
        corrected_mean_motion=np.sqrt(BDS_MU_EARTH / 27_906_100.0**3),
        tk=3000.0, M0=0.3 * BDS_PI, e=0.0, omega=0.1 * BDS_PI, i0=0.3 * BDS_PI, i_dot=0.0,
        Omega0=-0.4 * BDS_PI, Omega_dot=0.0, toe=eph.toe,
        Cus=0, Cuc=0, Crs=0, Crc=0, Cis=0, Cic=0,
    )
    difference = np.linalg.norm(state.position_ecef_m - gps_like.position_ecef_m)
    assert 0.5 < difference < 20.0


def test_time_from_toe_folds_across_the_week_rollover():
    eph = make_ephemeris(toe=600.0)
    state = eph.orbit_state(604_700.0)  # 100 s before the week ends: 700 s before toe
    assert state.time_from_ephemeris_s == pytest.approx(-700.0)
    assert state.position_ecef_m == pytest.approx(eph.orbit_state(-100.0).position_ecef_m, abs=1e-6)
    late = make_ephemeris(toe=604_500.0).orbit_state(100.0)  # 400 s after toe, next week
    assert late.time_from_ephemeris_s == pytest.approx(400.0)


def test_geo_and_reserved_orbit_types_are_rejected():
    with pytest.raises(ValueError, match="GEO"):
        make_ephemeris(sat_type=BDS_SAT_TYPE_GEO)
    with pytest.raises(ValueError, match="reserved"):
        make_ephemeris(sat_type=0)


# --- clock -----------------------------------------------------------------

def test_clock_is_the_polynomial_plus_the_relativistic_term():
    eph = make_ephemeris(e=0.0075, a0=1.0e-4, a1=2.0e-12, a2=-3.0e-20, toc=262_000.0, M0=0.8)
    t = 263_500.0
    state = eph.orbit_state(t)
    dt = t - 262_000.0
    polynomial = 1.0e-4 + 2.0e-12 * dt - 3.0e-20 * dt * dt
    assert eph.clock_polynomial_s(t) == pytest.approx(polynomial, rel=1e-12)
    relativistic = BDS_RELATIVISTIC_F * 0.0075 * np.sqrt(eph.semi_major_axis_at_toe_m) * np.sin(
        state.eccentric_anomaly_rad
    )
    assert eph.relativistic_correction_s(state) == pytest.approx(relativistic, rel=1e-12)
    assert eph.clock_correction_s(t) == pytest.approx(polynomial + relativistic, rel=1e-12)
    assert abs(relativistic) < 2.0e-8  # tens of nanoseconds for e ~ 0.0075


def test_relativistic_constant_is_minus_two_sqrt_mu_over_c_squared():
    assert BDS_RELATIVISTIC_F == pytest.approx(-4.442807309e-10, rel=1e-9)


def test_clock_polynomial_folds_across_the_week():
    eph = make_ephemeris(toc=100.0, a0=0.0, a1=1.0e-9)
    assert eph.clock_polynomial_s(604_790.0) == pytest.approx(1.0e-9 * (604_790.0 - 604_800.0 - 100.0))


def test_group_delay_signs_follow_icd_equations_7_4_and_7_5():
    eph = make_ephemeris(tgd_b2ap=5.0e-9, isc_b2ad=-2.0e-9)
    assert eph.group_delay_s("B2ap") == pytest.approx(-5.0e-9)
    assert eph.group_delay_s("B2ad") == pytest.approx(-5.0e-9 + 2.0e-9)
    # B1C components (B1C ICD 7-4 and 7-5) use their own delay and the B1C data-pilot term.
    eph = make_ephemeris(tgd_b2ap=5.0e-9, isc_b2ad=-2.0e-9, tgd_b1cp=3.0e-9, isc_b1cd=1.0e-9)
    assert eph.group_delay_s("B1Cp") == pytest.approx(-3.0e-9)
    assert eph.group_delay_s("B1Cd") == pytest.approx(-3.0e-9 - 1.0e-9)
    with pytest.raises(ValueError):
        eph.group_delay_s("E5a")


def test_observables_group_delay_branch_for_beidou():
    eph = make_ephemeris(tgd_b2ap=5.0e-9)
    assert observables.signal_group_delay_s(eph, "BDS_B2A") == pytest.approx(-5.0e-9)
    with pytest.raises(ValueError, match="B2a and B1C only"):
        observables.signal_group_delay_s(eph, "GPS_L5")


def test_observables_clock_correction_runs_for_a_beidou_ephemeris():
    eph = make_ephemeris(a0=1.0e-4, tgd_b2ap=5.0e-9)
    obs = observables.Observables(
        epoch_uptime_ms=np.array([0.0]), receive_time_s=np.array([262_900.0]),
        sat_ids=["C13"], transmit_time_s=np.array([[262_900.0]]),
        pseudorange_m=np.array([[2.2e7]]), week=1084, anchors={},
    )
    got = observables.satellite_clock_corrections(obs, {"C13": eph}, signal_type_id="BDS_B2A")
    assert got[0, 0] == pytest.approx(eph.clock_correction_s(262_900.0) - 5.0e-9)


def test_cnav_anchor_prefix_is_configurable():
    from types import SimpleNamespace

    stream = SimpleNamespace(code_phase_ms=np.arange(10, dtype=float))
    message = SimpleNamespace(prn=7, symbol_index=3, tow_at_message_start_s=6.0, message_type=11, tow_count=1)
    assert observables.anchor_from_cnav(stream, message).sat_id == "G07"
    assert observables.anchor_from_cnav(stream, message, sat_id_prefix="E").sat_id == "E07"


# --- assembly from decoded frames (D-055) ----------------------------------

def _frames_for(sat: str, *, symbol_indexes: dict[int, int] | None = None) -> list[bcnav2.Bcnav2Frame]:
    data = json.loads(FRAMES_PATH.read_text())
    frames = []
    for record in data["sats"][sat]["frames"]:
        bits = prim.bits_from_int(int(record["bits_hex"], 16), 288)
        index = (symbol_indexes or {}).get(record["type"], record["symbol_index"])
        frames.append(
            bcnav2.Bcnav2Frame(
                prn=int(prim.unpack_field(bits, 1, 6)),
                message_type=int(prim.unpack_field(bits, 7, 6)),
                sow_count=int(prim.unpack_field(bits, 13, 18)),
                bits=bits, symbol_index=index, inverted=True, ldpc_iterations=0, symbols_corrected=0,
            )
        )
    return sorted(frames, key=lambda f: f.symbol_index)


def _result(frames) -> bcnav2.Bcnav2DecodeResult:
    return bcnav2.Bcnav2DecodeResult(frames=list(frames))


def test_real_frames_assemble_into_an_ephemeris():
    data = json.loads(FRAMES_PATH.read_text())
    assert len(data["sats"]) == 9
    for sat, record in data["sats"].items():
        eph = bcnav2.assemble_ephemeris(_result(_frames_for(sat)))
        assert eph is not None, sat
        assert eph.sat_id == sat
        assert eph.week == 1084
        assert eph.iode == eph.iodc & 0xFF
        assert eph.health == 0
        assert eph.sat_type == (BDS_SAT_TYPE_IGSO if sat == "C06" else BDS_SAT_TYPE_MEO)


def test_type_11_may_come_before_type_10():
    frames = _frames_for("C13")
    by_type = {f.message_type: f for f in frames}
    swapped = {10: by_type[11].symbol_index, 11: by_type[10].symbol_index}
    eph = bcnav2.assemble_ephemeris(_result(_frames_for("C13", symbol_indexes=swapped)))
    assert eph is not None


def test_frames_that_are_not_adjacent_are_not_paired():
    gap = {11: _frames_for("C13")[-1].symbol_index + 1200}  # 11 moved two frames away
    frames = _frames_for("C13", symbol_indexes=gap)
    assert bcnav2.assemble_ephemeris(_result(frames)) is None


def test_a_clock_with_a_different_iodc_is_not_used():
    frames = _frames_for("C13")
    clock = next(f for f in frames if f.message_type == 30)
    bits = clock.bits.copy()
    bits = bcnav2.set_data_field(bits, 112, 10, 0x3FF)  # IODC low byte no longer matches IODE
    frames = [dataclasses.replace(f, bits=bits) if f is clock else f for f in frames]
    assert bcnav2.assemble_ephemeris(_result(frames)) is None


def test_the_newest_matching_pair_is_used():
    old = _frames_for("C13")
    new_index = {10: old[1].symbol_index + 6000, 11: old[1].symbol_index + 6600}
    newer = _frames_for("C13", symbol_indexes=new_index)
    newer_30 = [f for f in newer if f.message_type == 30]
    # Give the newer type 10/11 pair a changed eccentricity that is still matched by IODC.
    newer_10 = next(f for f in newer if f.message_type == 10)
    changed = dataclasses.replace(newer_10, bits=bcnav2.set_data_field(newer_10.bits, 199, 33, 1))
    frames = old + [changed] + [f for f in newer if f.message_type == 11] + newer_30
    eph = bcnav2.assemble_ephemeris(_result(sorted(frames, key=lambda f: f.symbol_index)))
    assert eph.e == pytest.approx(1 * 2.0**-34)


def test_real_orbits_are_physically_plausible():
    """Not a truth comparison (see the module docstring), but a wrong layout or scale fails here."""
    for sat in json.loads(FRAMES_PATH.read_text())["sats"]:
        eph = bcnav2.assemble_ephemeris(_result(_frames_for(sat)))
        a = eph.semi_major_axis_at_toe_m
        # The IGSO satellite sits 6.9 km off the nominal 42 162 km in the real data;
        # 20 km separates a right scale from a wrong one (e.g. a mis-scaled delta A).
        assert a == pytest.approx(BDS_A_REF_M[eph.sat_type], abs=20_000.0)
        assert 0.0 <= eph.e < 0.01
        inclination_deg = eph.i0 * 180.0
        assert 53.0 < inclination_deg < 60.0, (sat, inclination_deg)
        radii = []
        for dt in (0.0, 600.0, 1800.0, -1800.0):
            position = eph.orbit_state(eph.toe + dt).position_ecef_m
            radii.append(np.linalg.norm(position))
            # |z| cannot exceed r sin(i).
            assert abs(position[2]) <= np.linalg.norm(position) * np.sin(eph.i0 * BDS_PI) + 1.0e3
        assert max(radii) - min(radii) < 0.05 * a
        # ECEF speed: the inertial speed sqrt(mu/a) minus or plus the earth's rotation.
        p0 = eph.orbit_state(eph.toe).position_ecef_m
        p1 = eph.orbit_state(eph.toe + 10.0).position_ecef_m
        speed = np.linalg.norm(p1 - p0) / 10.0
        inertial = np.sqrt(BDS_MU_EARTH / a)
        assert abs(speed - inertial) < 0.6 * BDS_OMEGA_E_DOT * a
        assert abs(eph.clock_correction_s(eph.toe)) < 1.0e-3
        assert abs(eph.tgd_b2ap) < 5.0e-8


# --- RINEX 3 ---------------------------------------------------------------

def _fortran(value: float) -> str:
    return f"{value:19.12E}".replace("E", "D")


def rinex_record(eph: BdsEphemeris, *, prefix: str = "C", epoch=datetime(2026, 10, 14, 1, 0, 0)) -> list[str]:
    """Write `eph` as a RINEX 3 BeiDou record (8 lines), the way a broadcast file holds it."""
    a = eph.semi_major_axis_at_toe_m
    first = (
        f"{prefix}{int(eph.sat_id[1:]):02d} {epoch:%Y %m %d %H %M %S}"
        + _fortran(eph.a0) + _fortran(eph.a1) + _fortran(eph.a2)
    )
    rows = [
        (float(eph.iode or 0), eph.Crs, eph.delta_n0 * BDS_PI, eph.M0 * BDS_PI),
        (eph.Cuc, eph.e, eph.Cus, np.sqrt(a)),
        (eph.toe, eph.Cic, eph.Omega0 * BDS_PI, eph.Cis),
        (eph.i0 * BDS_PI, eph.Crc, eph.omega * BDS_PI, eph.Omega_dot * BDS_PI),
        (eph.i_dot * BDS_PI, 0.0, float(eph.week), 0.0),
        (2.0, float(eph.health), 1.0e-9, 2.0e-9),
        (eph.toe, float(eph.iodc or 0), 0.0, 0.0),
    ]
    return [first + "\n"] + ["    " + "".join(_fortran(v) for v in row) + "\n" for row in rows]


def rinex_file(*records: list[str], version: str = "3.04") -> list[str]:
    header = [
        f"{version:>9}           N: GNSS NAV DATA    M: MIXED            RINEX VERSION / TYPE\n",
        " " * 60 + "END OF HEADER\n",
    ]
    return header + [line for record in records for line in record]


def test_rinex_record_round_trips_the_orbit():
    eph = make_ephemeris(
        delta_a=-950.0, e=0.0021, M0=0.77, omega=0.31, Omega0=-0.52, Omega_dot=-1.5e-9, i0=0.305,
        i_dot=2.0e-13, delta_n0=1.0e-12, Cis=-1e-7, Cic=1e-7, Crs=40.0, Crc=-20.0, Cus=2e-6, Cuc=-1e-6,
        a0=3.0e-4, a1=1.0e-12, iode=17, iodc=17, health=0,
    )
    records, skipped = rinex3_nav.parse_bds_records(rinex_file(rinex_record(eph)))
    assert skipped == []
    (parsed,) = records["C13"]
    assert parsed.week == 1084 and parsed.sat_type == BDS_SAT_TYPE_MEO
    assert parsed.orbit_state(eph.toe + 300.0).position_ecef_m == pytest.approx(
        eph.orbit_state(eph.toe + 300.0).position_ecef_m, abs=1e-3
    )
    assert parsed.clock_polynomial_s(262_900.0) == pytest.approx(eph.clock_polynomial_s(262_900.0), rel=1e-9)
    assert (parsed.iode, parsed.iodc) == (17, 17)


def test_rinex_igso_is_recognised_by_its_semi_major_axis():
    eph = make_ephemeris(sat_id="C06", sat_type=BDS_SAT_TYPE_IGSO, i0=0.31)
    (parsed,) = rinex3_nav.parse_bds_records(rinex_file(rinex_record(eph)))[0]["C06"]
    assert parsed.sat_type == BDS_SAT_TYPE_IGSO
    assert parsed.semi_major_axis_at_toe_m == pytest.approx(eph.semi_major_axis_at_toe_m)


def test_rinex_geo_records_are_skipped_and_reported():
    geo = make_ephemeris(sat_id="C03", sat_type=BDS_SAT_TYPE_IGSO, i0=0.01)  # GEO plane: inclination ~ 0
    meo = make_ephemeris()
    records, skipped = rinex3_nav.parse_bds_records(rinex_file(rinex_record(geo), rinex_record(meo)))
    assert skipped == ["C03"]
    assert list(records) == ["C13"]


def test_rinex_other_systems_are_ignored():
    gps = rinex_record(make_ephemeris(), prefix="G")
    records, skipped = rinex3_nav.parse_bds_records(rinex_file(gps, rinex_record(make_ephemeris())))
    assert list(records) == ["C13"] and skipped == []


def test_rinex_version_and_header_are_checked():
    with pytest.raises(ValueError, match="not supported"):
        rinex3_nav.parse_bds_records(rinex_file(rinex_record(make_ephemeris()), version="2.11"))
    with pytest.raises(ValueError, match="not supported"):
        rinex3_nav.parse_bds_records(rinex_file(rinex_record(make_ephemeris()), version="4.00"))
    with pytest.raises(ValueError, match="END OF HEADER"):
        rinex3_nav.parse_bds_records(["no header\n"])


def test_rinex_truncated_last_record_is_dropped():
    lines = rinex_file(rinex_record(make_ephemeris()), rinex_record(make_ephemeris(toe=266_400.0)))
    records, _ = rinex3_nav.parse_bds_records(lines[:-3])
    assert len(records["C13"]) == 1


def test_rinex_gzip_file_is_loaded(tmp_path):
    path = tmp_path / "brdc.rnx.gz"
    with gzip.open(path, "wt") as handle:
        handle.writelines(rinex_file(rinex_record(make_ephemeris())))
    records, _ = rinex3_nav.load_bds_ephemerides(path)
    assert "C13" in records


def test_select_ephemeris_takes_the_nearest_toe_within_the_age_limit():
    early = make_ephemeris(toe=259_200.0)
    late = make_ephemeris(toe=262_800.0)
    assert rinex3_nav.select_ephemeris([early, late], 1084, 262_000.0) is late
    assert rinex3_nav.select_ephemeris([early, late], 1084, 259_300.0) is early
    assert rinex3_nav.select_ephemeris([early, late], 1084, 300_000.0) is None
    # Across the week boundary: a record from the end of week 1083 serves the start of 1084.
    end_of_week = make_ephemeris(week=1083, toe=604_500.0)
    assert rinex3_nav.select_ephemeris([end_of_week], 1084, 300.0) is end_of_week


def test_brdc3_path_follows_the_template(tmp_path):
    url, compressed, decompressed = rinex3_nav.brdc3_url_and_path(datetime(2026, 10, 14), tmp_path)
    assert url.endswith("/gnss/data/daily/2026/brdc/BRDC00IGS_R_20262870000_01D_MN.rnx.gz")
    assert decompressed.name == "BRDC00IGS_R_20262870000_01D_MN.rnx"
    assert compressed.parent == tmp_path / "gnss/data/daily/2026/brdc"


# --- SP3 with BeiDou satellites --------------------------------------------

def _write_sp3(path: Path, positions: dict[str, np.ndarray], epochs: np.ndarray) -> None:
    """A minimal SP3 file for the loader the notebooks use, with the given satellites."""
    from gnss_tools.orbits import parse_sp3 as sp3

    header = sp3.SP3Header(
        version="c", position_velocity_flag="P", start_time=float(epochs[0]),
        number_of_epochs=len(epochs), data_used="ORBIT", coordinate_sys="IGb14", orbit_type="HLM",
        agency="TST", gps_week=2392, seconds_of_week=float(epochs[0] % 604800),
        epoch_interval=300.0, mod_jul_day_start=61327, fractional_day=0.0,
    )
    records = [
        sp3.SP3Record(
            epoch=float(t),
            p_entries={sat: (*pos[k], 12.0 + k) for sat, pos in positions.items()},
            v_entries={},
        )
        for k, t in enumerate(epochs)
    ]
    dataset = sp3.Dataset()
    dataset.header, dataset.records = header, records
    with open(path, "w") as handle:
        dataset.save_file(handle)


def test_sp3_beidou_ids_load_and_interpolate(tmp_path):
    """SP3 'C' records go through the same parser and Lagrange code as 'G' ones."""
    from gnss_tools.orbits import parse_sp3 as sp3
    from utils import precise_orbits

    epochs = 1_450_000_000.0 + np.arange(0, 24) * 300.0
    eph = make_ephemeris()
    positions = {}
    for sat, shift in (("C13", 0.0), ("C06", 4000.0), ("G01", 8000.0)):
        shifted = dataclasses.replace(eph, toe=eph.toe + shift)
        positions[sat] = np.array([
            shifted.orbit_state((t + shift) % 604800.0).position_ecef_m for t in epochs
        ])
    path = tmp_path / "test.sp3"
    _write_sp3(path, positions, epochs)

    dataset = sp3.Dataset()
    with open(path) as handle:
        dataset.load_file(handle)
    arrays = dataset.get_sp3_arrays()
    assert {"C13", "C06", "G01"} <= set(arrays.position)
    # Centre of the file, off the nodes: a degree-10 polynomial through an hour of a
    # circular orbit reproduces it to well under a decimetre.
    times = epochs[10] + np.array([77.0, 150.0, 211.0])
    truth = np.array([eph.orbit_state(t % 604800.0).position_ecef_m for t in times])
    got = precise_orbits.interpolate_position_m(arrays, "C13", times)
    assert np.abs(got - truth).max() < 0.1
    # The test file's clock grows by 1 microsecond per epoch (12 + k), so 77 s after
    # epoch 10 it is 22 + 77/300 microseconds: linear, and in seconds.
    clock = precise_orbits.interpolate_clock_s(arrays, "C13", times)
    assert clock[0] == pytest.approx((22.0 + 77.0 / 300.0) * 1e-6, abs=1e-9)
    # A BeiDou id that the file does not hold gives NaN, not an error.
    assert "C99" not in arrays.position


# --- the comparison helpers used by scripts/compare_bds_orbits.py -----------

def _synthetic_sp3(eph: BdsEphemeris, sat_id: str, span_s: float = 4 * 3600.0, step_s: float = 300.0):
    from types import SimpleNamespace

    from utils import bds_orbit_check

    offsets = np.arange(-span_s, span_s + 1.0, step_s)
    epochs = np.array([bds_orbit_check.to_gps_seconds(eph.week, eph.toe + dt) for dt in offsets])
    positions = np.array([eph.orbit_state((eph.toe + dt) % 604800.0).position_ecef_m for dt in offsets])
    return SimpleNamespace(epochs=epochs, position={sat_id: positions}, clock={sat_id: np.zeros(len(epochs))})


def test_a_broadcast_orbit_compared_with_itself_as_sp3_differs_by_millimetres():
    from utils import bds_orbit_check

    eph = make_ephemeris(e=0.002, M0=0.4, omega=0.2, i0=0.305)
    offsets = np.arange(-3600.0, 3601.0, 300.0)
    comparison = bds_orbit_check.compare_with_sp3(eph, _synthetic_sp3(eph, "C13"), offsets)
    assert comparison.max_m < 0.1
    assert comparison.at_toe_m < 0.1


def test_a_14_second_time_error_shows_up_as_tens_of_kilometres():
    """
    The reason the BDT -> GPST step exists: if SP3's GPS seconds were read as BDT, a
    MEO satellite (3.8 km/s) would be 14 s x 3.8 km/s = 53 km off along its track.
    """
    from utils import bds_orbit_check

    eph = make_ephemeris(e=0.002)
    sp3 = _synthetic_sp3(eph, "C13")
    sp3.epochs = sp3.epochs - 14.0  # as if the SP3 times had been left in BDT
    comparison = bds_orbit_check.compare_with_sp3(eph, sp3, np.array([0.0]))
    assert 30_000.0 < comparison.at_toe_m < 80_000.0


def test_two_ephemerides_of_one_orbit_agree_and_a_changed_one_does_not():
    from utils import bds_orbit_check

    eph = make_ephemeris(e=0.002)
    same = dataclasses.replace(eph)
    offsets = np.array([-1800.0, 0.0, 1800.0])
    assert bds_orbit_check.compare_ephemerides(eph, same, offsets).max_m < 1e-6
    shifted = dataclasses.replace(eph, M0=eph.M0 + 1e-6)  # 1e-6 semi-circle = 1e-6 pi rad
    expected = 1e-6 * BDS_PI * 27_906_100.0  # about 88 m along the track
    got = bds_orbit_check.compare_ephemerides(eph, shifted, np.array([0.0]))
    assert got.at_toe_m == pytest.approx(expected, rel=0.05)


def test_compare_ephemerides_handles_different_weeks():
    from utils import bds_orbit_check

    eph = make_ephemeris(week=1084, toe=600.0)
    # `other` is the same orbit given as week 1083, toe 604 200: that is 1200 s before
    # `eph.toe` (600 s into week 1084), so it is a different instant.  This only checks
    # that the week arithmetic runs and gives a finite answer.
    other = dataclasses.replace(eph, week=1083, toe=604_200.0)
    comparison = bds_orbit_check.compare_ephemerides(eph, other, np.array([0.0]))
    assert np.isfinite(comparison.at_toe_m)


def test_comparison_statistics_ignore_missing_reference_data():
    from utils import bds_orbit_check

    comparison = bds_orbit_check.OrbitComparison(
        offsets_s=np.array([-300.0, 0.0, 300.0]), difference_m=np.array([np.nan, 2.0, 4.0])
    )
    assert comparison.at_toe_m == 2.0
    assert comparison.max_m == 4.0
    assert comparison.rms_m == pytest.approx(np.sqrt((4.0 + 16.0) / 2))
    empty = bds_orbit_check.OrbitComparison(np.array([0.0]), np.array([np.nan]))
    assert np.isnan(empty.at_toe_m) and np.isnan(empty.rms_m) and np.isnan(empty.max_m)


# --- real data: Doppler predicted from the decoded orbit -------------------

def test_decoded_orbits_predict_the_tracked_doppler():
    """
    An archive-free check on the whole decode chain, using the recording of 2026-10-14.

    For each of the nine tracked satellites the orbit assembled from its own B-CNAV2
    frames gives a Doppler at the receiver, at the transmit time that the decoded SOW
    and the tracked code phase fix.  The tracker measured the same thing.  Both are
    affected by one error shared by every satellite -- the receiver oscillator, about
    -2.3 kHz here -- so the common value is removed at each time and what is left per
    satellite must be small.

    Measured residual RMS was 0.07 to 0.35 Hz.  What the 1 Hz bound catches: a velocity
    error of 1 m/s (3.9 Hz), and a transmit-time error of 14 s common to all satellites
    (checked by hand: the worst RMS rises to 2.8 Hz).  What it does not catch: a time
    error of about a second, because most of a common time shift is absorbed by the
    common offset.  So this check proves the orbit shape and velocity, not the time.
    """
    from utils import bds_orbit_check as check

    data = json.loads(FRAMES_PATH.read_text())
    receiver = check.geodetic_to_ecef(40.009956, -105.243613, 1655.0)
    carrier = 1176.45e6
    residual: dict[str, dict[float, float]] = {}
    for sat, record in data["sats"].items():
        eph = bcnav2.assemble_ephemeris(_result(_frames_for(sat)))
        anchor = next(f for f in record["frames"] if f["type"] == 10)
        sow0, phase0 = anchor["sow_count"] * 3.0, anchor["code_phase_ms"]
        residual[sat] = {}
        for t_target, code_phase_ms, measured_hz in record["epochs"]:
            t_transmit = sow0 + (code_phase_ms - phase0) * 1e-3
            assert check.elevation_deg(eph, receiver, t_transmit) > 0.0, (sat, "below the horizon")
            residual[sat][t_target] = measured_hz - check.predicted_doppler_hz(eph, receiver, t_transmit, carrier)

    times = sorted(set.intersection(*(set(r) for r in residual.values())))
    assert len(times) >= 15
    common = {t: np.median([residual[s][t] for s in residual]) for t in times}
    assert all(-2.5e3 < c < -2.0e3 for c in common.values())  # the receiver oscillator, a few kHz
    for sat, values in residual.items():
        left = np.array([values[t] - common[t] for t in times])
        assert np.sqrt(np.mean(left**2)) < 1.0, (sat, left)


def test_doppler_helpers_on_a_known_geometry():
    from utils import bds_orbit_check as check

    # Receiver on the equator at the foot of the satellite track: Doppler is zero at
    # closest approach and goes through zero, with opposite signs either side.
    eph = make_ephemeris(i0=0.0, Omega_dot=0.0, M0=0.0, omega=0.0, Omega0=0.0)
    receiver = check.geodetic_to_ecef(0.0, 0.0, 0.0)
    assert np.linalg.norm(receiver) == pytest.approx(6_378_137.0)
    before = check.predicted_doppler_hz(eph, receiver, eph.toe - 3000.0, 1176.45e6)
    after = check.predicted_doppler_hz(eph, receiver, eph.toe + 3000.0, 1176.45e6)
    assert abs(before) < 12_000 and abs(after) < 12_000  # a MEO pass: well under +/- 12 kHz at L5
    assert before != after
    # Straight overhead (elevation 90 deg) when the satellite is over the receiver.
    overhead = make_ephemeris(i0=0.0, Omega_dot=0.0, M0=0.0, omega=0.0, Omega0=np.degrees(0.0))
    state = overhead.orbit_state(overhead.toe)
    over = check.geodetic_to_ecef(
        0.0, np.degrees(np.arctan2(state.position_ecef_m[1], state.position_ecef_m[0])), 0.0
    )
    assert check.elevation_deg(overhead, over, overhead.toe) == pytest.approx(90.0, abs=0.5)
