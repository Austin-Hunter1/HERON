# HERON Studio

The after-the-flight app for HERON. The **Specular Planner** plans and flies a mission; **Studio**
takes what came back (GoPro video + ArduPilot log) and turns it into:

- the **AR video** with predicted specular points and Fresnel zones drawn on the ground
- a **water map** of the lake as the camera saw it, compared with the map outline the plan used
- a **3D model** of the shore, with a movable water level
- **exports**: GeoJSON, KML, GeoTIFF water mask + probability, height model GeoTIFF, reflections CSV,
  and a `.heronsite` **site package** the Planner can load to score lines against the real shoreline

The 19 Oct 2026 Boulder Reservoir flight is included as a finished example.

## Run it

```bash
cd HERON_Studio
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
brew install ffmpeg            # or any ffmpeg + ffprobe on PATH
python run.py                  # opens http://127.0.0.1:5056
```

Flights are stored in `~/HERON Studio/flights` (set `HERON_STUDIO_HOME` to move it). The Planner
uses port 5055, Studio 5056, so both can run at once.

### As a desktop app (Electron)

```bash
cd electron
npm install
npm start                      # dev: runs ../run.py with your python3 (set HERON_PYTHON to pick one)
```

`npm run dist:mac` / `npm run dist:win` freezes the Python side with PyInstaller
(`packaging/heron_studio.spec`) and builds a `.dmg` / installer with ffmpeg bundled. Build on the
OS you ship for. The packaging config is written but hasn't been run on a Mac yet.

In the desktop app, dropped files are used **where they are** (no 4 GB copy). Tick
"Copy into the Studio folder" to copy them instead, e.g. before unplugging the SD card.

## What to give it

| File | Needed | Notes |
|---|---|---|
| GoPro video (`.MP4`/`.MOV`) | yes | Any resolution, frame rate or aspect: 720p to 5.3K, 16:9, 4:3 or 8:7. Long recordings that the GoPro split into chapters (`GX010123.MP4`, `GX020123.MP4`, …) are joined automatically into one recording: video joined without re-encoding, gyro timelines offset per chapter. |
| Camera gyro | yes | Read from the original GoPro MP4 (GPMF track). For an edited clip without it, add a folder with `GYRO.csv` + `ACCL.csv` and set *Clip starts at* under Advanced. |
| Flight log (`.bin`) | yes | ArduPilot DataFlash. A folder of exported CSVs (`POS`, `ATT`, `GPS`, `IMU`, `XKF1`, `RFND`) also works. |
| Lake outline (`.geojson`/`.kml`) | near Boulder Reservoir: no | Elsewhere, add the outline the plan used. |
| Mission (`.waypoints`/`.plan`) | no | Shown for reference. |

### Different resolutions

- The **lens profile** is matched on camera model, lens mode (Wide/Linear), aspect ratio and EIS
  from the 400+ GoPro profiles in `lens_profiles/` (Gyroflow database, CC0), then scaled to the
  clip's width. A profile with a different aspect ratio is never used. You can pick another one on
  the Import screen.
- Work sizes keep the clip's aspect: the camera solve runs at up to 960 px wide, water mapping at up
  to 1920, and the AR video is rendered at *Same as video*, 1080p or 720p. Nothing is upscaled.
- Frame rates such as 23.976, 29.97, 59.94 and 119.88 are read from the file; slightly-off rates from
  edited exports are snapped to the camera's real rate.
- HEVC, 4K and 5.3K sources get a 1280-wide H.264 preview copy so they play in the app.
- The UI scales from a 13" laptop to a 4K monitor, and down to phone width (useful for checking a
  long job from your phone on the same network).

## Processing steps

1. **Read files**: `.bin` → CSV, GoPro GPMF → gyro/accelerometer, lens, almanac, outline, engine config.
2. **Sync clocks**: GoPro ↔ autopilot by matching vibration, searching ±15 min.
3. **Solve the camera** (COLMAP): *Quick* every 12th frame, *Custom* at a frame rate you
   pick (1–12 fps of video, turned into a frame step from the clip's real rate), or *Full* every frame.
   Above ~5,000 frames the global mapper's track building gets very slow; 4–8 fps is usually enough. Uses
   COLMAP's global mapper (`sfm.mapper`, `"incremental"` for the old one-frame-at-a-time solver).
   Extracted frames and the features database are reused on re-runs, so Quick after Full is cheap.
   For the GPU, unzip a COLMAP 4.x Windows release to `~/HERON Studio/tools/colmap-<version>/` (or set
   `HERON_COLMAP`); a folder with `cuda` in its name is preferred. `colmap-x64-windows-cuda.zip`
   (NVIDIA) runs feature extraction and matching on the GPU, and mapping runs through it too.
   `colmap-x64-windows-nocuda.zip` (AMD/Intel, OpenGL) does extraction only; its matcher failed on
   an RX 5700 XT. In the 4.2.1 release Ceres has no CUDA, so bundle adjustment stays on the CPU with
   either build. `sfm.gpu: "off"` disables all of it.
4. **Tie to GPS**: scale and place the solve in metres.
5. **Map the water**: a frame every 0.75 s is cast onto the ground and each 0.7 m cell collects what the
   camera saw there over the whole flight. Water is ground that is smooth and grey (it mirrors the sky)
   within 300 m of the camera, or glinting, or open blue further out; the thresholds come from the flight
   itself. Shallow water counts as water, bare mud and sand do not, and the outline file is not used.
   Maps from older versions are rebuilt automatically the next time this step or the render runs.
6. **Reflections**: every satellite above the mask every 0.5 s, on water by the outline vs by the camera.
7. **3D model**: height grid from the solved points. The water level in the model is the median ground
   height along the measured water edge (on 19 Oct this lands within 4 cm of the dense estimate).
8. **Render AR video**: one process per CPU thread, joined without re-encoding. The camera pose between
   solved frames is a spline through them. Where the solve is more than `pose_max_gps_error_m` (10 m) from
   the GPS track, e.g. on the ground before take-off, the frame is the plain video with the header only and
   has no rows in the CSV. Encoded on the NVIDIA GPU when there is one, else with libx264
   (`encoder`: `"auto"`, `"h264_nvenc"` or `"libx264"`).
9. **Exports + site package**.

Each step runs as its own process. You can cancel, resume after a crash or app restart, or re-run
from any step.

## Exports

| File | What |
|---|---|
| `water.geojson`, `water.kml` | Measured water polygons (WGS84) with area and date |
| `shoreline.geojson` | Shoreline and island outlines |
| `water_mask.tif` | 1 water / 0 land / 255 not seen, 0.7 m |
| `water_probability.tif` | 0–1 |
| `dem.tif` | Height above the flight-day water, 0.5–1 m |
| `reflections.csv` | Every reflection point: time, satellite, elevation, lat/lon, on water by outline / by camera |
| `summary.json` | Areas, how far below the outline the water sits, reflection counts |
| `water.kmz` | Google Earth (desktop and web) and Google My Maps: water, outline, 3D track, reflections, drone photo map as a ground overlay |
| `map.html` | One-file interactive Leaflet map (satellite/streets/topo switcher, layers, legend, coordinates). Works offline except the base maps; email it or put it on a site |
| `water_shapefiles.zip` | Shapefiles: water, shoreline, track (with height), reflections |
| `track_shoreline.gpx` | Flown track and shoreline for a handheld GPS or phone app |
| `shoreline_points.csv` | Shoreline as lat/lon rows: Excel, or import into Google My Maps |
| `HERON_site_<site>_<date>.heronsite` | ZIP for the Specular Planner: `site.json`, water, mask, heights |
| *Export this level* | Water outline at any other level (± m), from the height model |

GeoTIFFs use a transverse-Mercator projection centred on the flight's first GPS fix. That matches the
engine's East/North frame to well under a centimetre, and QGIS/ArcGIS read it directly.

## Tested

Here's what was checked before handing it over (Linux, in the cloud):
- The 19 Oct flight run through the app from scratch (Quick solve): `.bin` → CSV, clock sync −47.79 s
  (27 ms residual), 349/349 frames solved, GPS fit 0.76 m median. Reflections 926 outline / 48
  measured of 5,250 (the hand-run analysis had 945 / 46). Outline 1.2 m above the water (hand-run
  1.1 m). About 18 min end to end.
- The `.bin` reader round-trips every value exactly against the original CSVs.
- GPMF extraction on a real HERO7 file (848×480 at 29.97 fps, a second resolution and frame rate).
- Chunked upload interrupted half way and resumed.
- UI at 390 px (phone), 1280 px HiDPI, 1440 px and 2560 px wide.

Not yet run: the Electron app and the packaged builds on macOS/Windows.

## Water map tools

- **Base maps**: Esri satellite, satellite + labels, OpenStreetMap streets, Esri topo, dark, or none
  (offline). With a Google Maps API key (Map settings), you also get Google satellite, hybrid, roads
  and terrain through Google's own Maps JavaScript API.
- **Layers**: measured water, map outline, flown track, reflection points, drone photo map, each
  toggleable, with opacity sliders for the water and the photo.
- **Measure**: click points for a distance; with three or more you also get an area. Click the last
  point or press Enter to finish.
- **Coordinates**: shown as you move over the map; click to copy lat/lon.
- **Open in Google Maps / Google Earth** at the current view. Full-screen map.

## Layout

```
run.py                 start the server (and frozen-build entry for engine steps)
studio/                Flask app: flights, uploads, detection, jobs, water exports, UI (static/)
engine/                vendored HERON AR engine (heron_ar/, heron.py), see VENDORED.txt
lens_profiles/         GoPro lens profiles
sample/                the 19 Oct 2026 flight, pre-processed
electron/              desktop shell (main.js, preload.js), electron-builder config
packaging/             PyInstaller spec for the desktop build
tests/                 test_units.py (no server), test_api.py (against a running server)
tools/build_sample.py  rebuilds sample/ from a heron-ar checkout
```

## Known limits (v0.1)

- *Full* solve over a long flight is slow: COLMAP on every frame of a 3-minute clip takes hours on a
  laptop. Studio doesn't yet split a long clip into passes automatically. Use *Quick* first.
- New flights get a height model from the sparse solve (about 1 m cells). The finer 0.5 m model in
  the sample came from a separate dense step that isn't in the pipeline yet.
- Sun-glint check, ripple roughness and wind-from-lean are shown for the sample flight only. They're
  not yet steps.
- Camera axis order for HERO8+ comes from the GPMF `ORIN` tag. Only HERO7 has been flown; check the
  sync residual on the first flight with a newer camera.
- The Planner doesn't read `.heronsite` yet. That's the next piece on the Planner side.
