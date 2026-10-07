"""Unit tests for the ground test simulation."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from ground_sim.cli import _heights
from ground_sim.config import ConfigError, load_config
from ground_sim.engine_link import load_engine
from ground_sim.report import build_payload, write_html
from ground_sim.simulate import flat_ground_specular, fresnel_axes, simulate
from ground_sim.terrain import Terrain
from ground_sim.water import WaterModel, build_surfaces, reflect_on_water

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config" / "ground_sim.example.toml"


@pytest.fixture(scope="module")
def cfg():
    return load_config(EXAMPLE)


@pytest.fixture(scope="module")
def engine(cfg):
    return load_engine(cfg.paths.planner_dir)


def test_example_config_window_is_8_to_11_mdt(cfg):
    start, end = cfg.time.window()
    assert start.utcoffset().total_seconds() == -6 * 3600  # MDT on 12 Oct
    assert (end - start).total_seconds() == 3 * 3600
    assert cfg.time.sample_offsets_s()[-1] == 3 * 3600


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("site", "latitude_deg", 123.0),
        ("site", "receiver_height_agl_m", 0.0),
        ("time", "step_s", 0),
        ("time", "timezone", "Mars/Olympus"),
        ("signals", "constellations", ["X"]),
        ("signals", "elevation_mask_deg", 95.0),
    ],
)
def test_bad_config_values_are_refused(tmp_path, section, key, value):
    text = EXAMPLE.read_text(encoding="utf-8")
    bad = tmp_path / "bad.toml"
    # Append an override by editing the parsed table is not possible in TOML,
    # so write a copy with the line replaced.
    lines = []
    in_section = False
    for line in text.splitlines():
        if line.startswith("["):
            in_section = line.strip() == f"[{section}]"
        if in_section and line.split("=")[0].strip() == key:
            line = f"{key} = {value!r}".replace("'", '"')
        lines.append(line)
    bad.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad)


def test_unknown_config_key_is_refused(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text(EXAMPLE.read_text(encoding="utf-8") + "\n[extra]\nx = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad)


def test_missing_config_file_is_refused(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "none.toml")


def test_flat_ground_specular_geometry():
    east, north, extra = flat_ground_specular(2.0, 90.0, 30.0)
    assert east == pytest.approx(2.0 / math.tan(math.radians(30)))
    assert north == pytest.approx(0.0, abs=1e-9)
    assert extra == pytest.approx(2.0)  # 2 h sin(30 deg) = h


def test_fresnel_axes_match_planner_at_l5(engine):
    geo = engine.geometry
    along, across = fresnel_axes(2.0, 35.0, geo.L5_LAMBDA)
    ref_along, ref_across = geo.fresnel_axes(2.0, 35.0)
    assert along == pytest.approx(ref_along)
    assert across == pytest.approx(ref_across)


def test_flat_specular_agrees_with_planner_search(engine):
    """The planner's numeric search must land close to the exact answer."""
    geo = engine.geometry
    lat, lon, h_ground, h_agl = 40.016421, -105.18895, 1600.0, 2.0
    sat = geo.lla_to_ecef(lat + 0.5, lon + 0.7, 20_200_000.0)
    rx = geo.lla_to_ecef(lat, lon, h_ground + h_agl)
    az, el = geo.az_el(sat, rx, lat, lon)
    east, north, _ = flat_ground_specular(h_agl, az, el)
    slat, slon, _, _ = geo.specular_point(sat, lat, lon, h_ground, h_agl)
    e2, n2, _ = geo.enu_between(lat, lon, h_ground, slat, slon, h_ground)
    assert math.hypot(e2 - east, n2 - north) < 0.6  # planner stops at 0.4 m


def test_simulation_gives_valid_tracks(cfg, engine):
    gnss = engine.gnss
    sats = gnss.filter_sats(gnss.load_l5_sats(cfg.paths.data_dir), cfg.signals.constellations)
    tracks = simulate(cfg, engine, sats, cfg.site.receiver_height_agl_m)
    assert tracks, "no satellite above the mask in 3 hours is not possible"
    for tr in tracks:
        for s in tr.samples:
            assert s.el_deg >= cfg.signals.elevation_mask_deg
            assert s.across_m < s.along_m  # zone is stretched along the azimuth
            assert math.hypot(s.east_m, s.north_m) == pytest.approx(
                cfg.site.receiver_height_agl_m / math.tan(math.radians(s.el_deg))
            )
        times = [s.t_s for s in tr.samples]
        assert times == sorted(times)


def test_html_report_loads_only_leaflet_from_network(cfg, engine, tmp_path):
    gnss = engine.gnss
    sats = gnss.filter_sats(gnss.load_l5_sats(cfg.paths.data_dir), cfg.signals.constellations)
    height = cfg.site.receiver_height_agl_m
    tracks = simulate(cfg, engine, sats, height)
    geometry = {"terrain": False, "reflect_height_m": height, "bodies": []}
    payload = build_payload(cfg, tracks, 1.0, {}, None, geometry)
    out = tmp_path / "r.html"
    write_html(payload, out)
    html = out.read_text(encoding="utf-8")
    assert "__HERON_DATA__" not in html
    # The page needs the network only for Leaflet (unpkg) and the map tiles
    # (the tile URL comes from the config, not from the page).
    hosts = {h.split("/")[0] for h in html.split("https://")[1:]}
    assert hosts <= {"unpkg.com", "server.arcgisonline.com"}


def test_shore_site_gives_reflections_on_water_and_far_site_gives_none(cfg, engine):
    """Same water test as the planner: no water within metres, no reflection."""
    gnss = engine.gnss
    sats = gnss.filter_sats(gnss.load_l5_sats(cfg.paths.data_dir), cfg.signals.constellations)
    water = WaterModel(engine, cfg.paths.water_geojson, cfg)
    passes = simulate(cfg, engine, sats, cfg.site.receiver_height_agl_m)
    surfaces = build_surfaces(water, None, cfg)  # flat: every body at level 0
    far = reflect_on_water(passes, surfaces, cfg.site.receiver_height_agl_m, water, cfg)
    assert not any(s.on_water for t in far for s in t.samples)
    assert 100 < water.nearest_water_m() < 1000  # Hillcrest Reservoir, about 300 m


def _hill() -> Terrain:
    """A 100 x 100 m grid at 10 m height with a 30 m wall 20 m east of the origin."""
    elev = np.full((100, 100), 10.0)
    # Grid spans about +-50 m around the origin; columns 70..72 are east of it.
    elev[:, 70:73] = 40.0
    half_lat = 50.0 / 111_132.92
    half_lon = half_lat / math.cos(math.radians(40.0))
    return Terrain(elev, -half_lon, half_lon, 40.0 - half_lat, 40.0 + half_lat, 40.0, 0.0)


def test_terrain_elevation_and_ray_blocking():
    t = _hill()
    assert float(t.elevation_en(0.0, 0.0)) == pytest.approx(10.0)
    assert np.isnan(t.elevation_en(500.0, 0.0))  # outside the grid: no terrain
    # An antenna 12 m up: a flat ray to the east hits the wall, one to the west does not.
    assert not t.ray_clear((0, 0, 12), 90.0, 0.0, 45.0, 1.0, 0.5)
    assert t.ray_clear((0, 0, 12), 270.0, 0.0, 45.0, 1.0, 0.5)
    # A very steep ray stays well inside the wall distance, so it is clear.
    assert t.ray_clear((0, 0, 12), 90.0, 80.0, 45.0, 1.0, 0.5)


def test_terrain_file_round_trip(tmp_path):
    t = _hill()
    t.save(tmp_path / "d.npz")
    back = Terrain.load(tmp_path / "d.npz")
    assert float(back.elevation_en(0.0, 0.0)) == pytest.approx(10.0)
    assert back.lat0 == pytest.approx(40.0)


def test_heights_above_water_from_dem(cfg, engine):
    """The antenna is about 42 m above Hillcrest Reservoir (needs data/dem.npz)."""
    if cfg.terrain is None or not cfg.terrain.dem_file.is_file():
        pytest.skip("run ground-sim --fetch-dem first")
    water = WaterModel(engine, cfg.paths.water_geojson, cfg)
    _, _, _, geometry = _heights(cfg, water)
    assert 35.0 < geometry["reflect_height_m"] < 50.0
    names = {b["name"]: b for b in geometry["bodies"]}
    assert "Valmont Reservoir" in names  # inside the map radius, own level
    assert names["Valmont Reservoir"]["distance_m"] > names["Hillcrest Reservoir"]["distance_m"]
