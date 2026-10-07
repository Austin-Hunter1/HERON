# Ground_Test_Sim

Ground test simulation for HERON (D-031). It shows where a fixed receiver
on a tripod sees GNSS reflections on flat ground. It also shows the size
of the first Fresnel zone at each reflection point.

The orbit and geometry code is from Jack Abrams' `Specular_Planner/engine`.
This tool reads that folder. It does not copy it.

## Run

```bash
cd Ground_Test_Sim
uv sync
uv run ground-sim --config config/ground_sim.example.toml
```

The tool writes three files to `output/`:

- `ground_sim_<date>.html` — interactive page. Open it in any browser.
  The map needs internet (Leaflet and Esri tiles). The sky plot,
  timeline and table work offline.
- `ground_sim_<date>_tracks.csv` — one row for each reflection window.
- `ground_sim_<date>_samples.csv` — one row for each satellite and time.

## Water, terrain and height

A reflection counts only when its specular point is inside a mapped water
body. The tool uses the planner's own file and test
(`Specular_Planner/data/waterbodies.geojson`, `point_in_polygon`), so the
ground test and the flight plan agree.

If the antenna stands on a hill, the height that matters is the height
above the **water surface**. With a `[terrain]` table the tool:

1. downloads one USGS 3DEP elevation grid for the site (`--fetch-dem`,
   once; saved in `data/dem.npz`, not committed);
2. takes the ground height under the antenna from the grid, adds
   `receiver_height_agl_m` (the mast), and subtracts the water level (the
   median grid height in the nearest water body, or `water_level_m`);
3. checks three rays for every sample: antenna to satellite, antenna to
   specular point, and specular point to satellite. If the ground blocks
   one, the reflection is "blocked" (amber on the page).

Without `[terrain]` the tool assumes flat ground and uses
`receiver_height_agl_m` as the height above the water.

```bash
uv run ground-sim --config config/ground_sim.example.toml --fetch-dem
uv run ground-sim --config config/ground_sim.example.toml
```

## What the page shows

- **Satellite map.** Esri imagery (needs internet), the water bodies, and
  the specular tracks. Each ellipse is a first Fresnel zone at the clock
  time: colour = available on water, amber = on water but blocked by
  terrain, grey dashed = not on water.
- **Sky plot.** Tracks in colour where the reflection is available, amber
  where blocked, grey where the satellite is above the mask but not over
  water.
- **Availability timeline.** One row per satellite. Click to set the clock.
- **Table.** One row for each reflection window: start and stop (local
  time), duration, elevation and incidence angle, azimuth, water share of
  the zone, largest Fresnel zone. Click a column to sort.

Incidence angle = 90 deg minus elevation, from the water normal.

## Maths

For an antenna at height `h` above the water and a satellite at elevation
`e` and azimuth `a`:

- Specular point: `h / tan(e)` from the receiver, towards `a`.
- Extra path of the reflection: `2 h sin(e)`.
- First Fresnel zone semi-axes: across `sqrt(L h / sin e)`, along
  `sqrt(L h / sin^3 e)`, where `L` is the carrier wavelength (the
  planner's formula, `engine/geometry.py`). It ignores the `L^2/4` term.
  Below 8 deg elevation the size is held at its 8 deg value, as in the
  planner, so zones at 5-8 deg are larger than shown.

## Limits

- Each water body is flat at one level: the median DEM height inside it.
  A body whose DEM heights vary a lot (Valmont: 1570-1608 m) gets a rough
  level. Set `water_level_m` in `[terrain]` to force one level for all.
- `elevation_mask_deg` must be 5 or more, so water farther than about
  `h / tan(5 deg)` from the antenna (about 480 m at 42 m height) is out of reach.
- Bare-earth terrain only. Trees and buildings are not in 3DEP.
- Rays that leave the DEM grid see no terrain.
- No antenna pattern. Every satellite above the mask is shown.

## Config

All values are in the TOML file (site, date, time window, step,
constellations, carrier, elevation mask, folders). The tool stops with a
clear message when a value is bad. To plan another test, copy the example
file and change it.

## Tests

```bash
uv run pytest
uv run ruff check .
```
