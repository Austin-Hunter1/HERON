# Specular Planner

Local web app that plans a HERON GNSS-R flight over water. It places the drone so L5-band bounces (GPS L5, Galileo E5a, BeiDou-3 B2a) hit a chosen lake, river, or map box, then exports an ArduPilot `.waypoints` file.

Almanacs here are for **planning** (where the splash lands). After a flight, use that day's broadcast `.nav` for height math.

## Requirements

- Python 3.11+ (`python3 --version`)
- Network the first time you install packages, and while the map is open (Esri imagery + orbit checks)

## Run

From the HERON repo:

```bash
cd Specular_Planner
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

On Windows, after `python -m venv .venv`:

```bat
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

You should see:

```
Specular planner  http://127.0.0.1:5055
```

Open that URL in a browser. Leave the terminal running. Stop with `Ctrl+C`.

If port 5055 is already taken, quit the other `python app.py` first.

Later sessions (venv already exists):

```bash
cd Specular_Planner
source .venv/bin/activate    # Windows: .venv\Scripts\activate
python app.py
```

## Use it

1. Set takeoff time (**UTC**), speed, hover, altitude, elevation mask.
2. Pick constellations (GPS / Galileo / BeiDou).
3. Choose a pattern:
   - **Route** — click the map for waypoints in order.
   - **Area** — click two corners for a box (or more points for a polygon), then **Build mission** (or **Finish**).
   - **Water** — click a lake once, or a river start then end. Aim elevation locks onto the satellite closest to that elevation.
4. **Build mission**. Playback appears on the bottom bar (`1×` / `4×` / `20×`).
5. Download **`.waypoints`** (and optional run card) from the sidebar for Mission Planner.

Switching Route / Area / Water clears the current mission.

## Orbit files

A copy of GPS YUMA, Galileo XML, and BeiDou TLEs ships in `data/`. The app also checks for updates about once a day while it is running.

Force a refresh:

```bash
source .venv/bin/activate
python data/fetch_gnss.py
```

Then restart `python app.py` if it was already running.

## Layout

```
app.py              Flask server (port 5055)
engine/             geometry, almanac, planner, ArduPilot export
static/             map UI
data/               water polygons + current almanacs
output/             generated .waypoints / run cards (not needed to run)
```
