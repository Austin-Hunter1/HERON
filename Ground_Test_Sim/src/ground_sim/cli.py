"""Command line entry point: `ground-sim --config <file> [--fetch-dem]`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ground_sim.config import ConfigError, SimConfig, load_config
from ground_sim.engine_link import load_engine
from ground_sim.report import build_payload, write_html, write_samples_csv, write_tracks_csv
from ground_sim.simulate import sky_passes
from ground_sim.terrain import apply_terrain, fetch_dem, load_terrain
from ground_sim.water import WaterModel, build_surfaces, map_features, reflect_on_water


def _heights(cfg: SimConfig, water: WaterModel):
    """Return (terrain, antenna_m, surfaces, geometry).

    With a `[terrain]` table: the antenna stands `receiver_height_agl_m` above
    the DEM at the receiver, and each water body has its own DEM level.
    Without it: flat ground, and the antenna height above every water body is
    `receiver_height_agl_m`.
    """
    if cfg.terrain is None:
        terrain, ground, antenna = None, None, cfg.site.receiver_height_agl_m
    else:
        terrain = load_terrain(cfg)
        ground = float(terrain.elevation_en(0.0, 0.0))
        antenna = ground + cfg.site.receiver_height_agl_m
    surfaces = build_surfaces(water, terrain, cfg)
    if not surfaces:
        raise ValueError("no water body with a known level near the receiver")
    geometry = {
        "terrain": terrain is not None,
        "reflect_height_m": antenna - surfaces[0].level_m,  # nearest body
        "ground_elev_m": None if ground is None else round(ground, 1),
        "antenna_elev_m": antenna,
        "bodies": [
            {
                "name": s.name,
                "level_m": round(s.level_m, 1),
                "height_m": round(antenna - s.level_m, 1),
                "distance_m": round(s.distance_m),
            }
            for s in surfaces
        ],
    }
    return terrain, antenna, surfaces, geometry


def main(argv: list[str] | None = None) -> int:
    """Run the simulation and write the HTML and CSV files. Return an exit code."""
    parser = argparse.ArgumentParser(description="HERON ground test simulation")
    parser.add_argument("--config", type=Path, required=True, help="TOML config file")
    parser.add_argument(
        "--fetch-dem", action="store_true", help="download the elevation grid, then stop"
    )
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
        if args.fetch_dem:
            print(f"Wrote {fetch_dem(cfg)}")
            return 0
        engine = load_engine(cfg.paths.planner_dir)
        water = WaterModel(engine, cfg.paths.water_geojson, cfg)
        terrain, antenna, surfaces, geometry = _heights(cfg, water)
    except (ConfigError, FileNotFoundError, ValueError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2

    gnss = engine.gnss
    sats = gnss.filter_sats(gnss.load_l5_sats(cfg.paths.data_dir), cfg.signals.constellations)
    if not sats:
        print(f"ERROR: no satellites loaded from {cfg.paths.data_dir}", file=sys.stderr)
        return 2

    start_local, _ = cfg.time.window()
    age = gnss.almanac_age_days(sats, start_local)
    if age > 30:
        print(f"WARNING: the almanac is {age:.0f} days from the test date.", file=sys.stderr)

    tracks = sky_passes(cfg, engine, sats)
    if not tracks:
        print("ERROR: no satellite is above the mask in this window.", file=sys.stderr)
        return 2
    tracks = reflect_on_water(tracks, surfaces, antenna, water, cfg)
    if terrain is not None:
        tracks = apply_terrain(tracks, terrain, cfg, antenna)
    payload = build_payload(
        cfg, tracks, age, map_features(surfaces), water.nearest_water_m(), geometry
    )

    out = cfg.paths.output_dir
    out.mkdir(parents=True, exist_ok=True)
    stem = f"ground_sim_{cfg.time.date:%Y%m%d}"
    write_html(payload, out / f"{stem}.html")
    write_tracks_csv(payload, out / f"{stem}_tracks.csv")
    write_samples_csv(payload, out / f"{stem}_samples.csv")
    mask = cfg.signals.elevation_mask_deg
    wins = payload["windows"]
    for b in geometry["bodies"][:5]:
        print(
            f"  {b['name']}: {b['distance_m']} m away, level {b['level_m']} m, "
            f"antenna {b['height_m']} m above it"
        )
    print(f"{len(sats)} satellites, {len(tracks)} passes above {mask} deg")
    print(f"{len(wins)} reflection windows on water ({len({w['sid'] for w in wins})} satellites)")
    if not wins:
        print("WARNING: no reflection is available on water. Check the site and the water map.")
    print(f"Wrote {out / (stem + '.html')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
