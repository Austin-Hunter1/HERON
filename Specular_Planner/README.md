# Specular Planner

Local web app that plans a HERON GNSS-R flight over water. It places the drone so L5-band bounces (GPS L5, Galileo E5a, BeiDou-3 B2a) hit a chosen lake, river, or map box, then exports an ArduPilot `.waypoints` file.

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

## Aircraft connection and control

The **Aircraft** panel extends the existing planner with telemetry, mission upload
and download verification, guarded launch, and Return home. Existing file exports
and preview playback still work. Use **Hide aircraft panel** to see the preview
HUD. Real aircraft markers and simulated aircraft are labeled separately.

Install the updated dependencies in your existing virtual environment, then restart
the app:

```bash
python -m pip install -r requirements.txt
python app.py
```

### Try it without hardware

1. Choose **Simulated aircraft** and **Connect**. The yellow SIMULATED badge stays visible.
2. Use **Use aircraft Home as pad**. Place one or two waypoints near the pad.
3. Set **Above Home** to 30 m, then **Build mission**.
4. Click **Upload & verify**. Open Launch checks to inspect any blockers.
5. Click **Launch mission**, review the mission, then **Confirm launch**.
6. Watch the live marker and mission item advance. Try **Return home**.

The demo uses accelerated flight and dwell times. It is an interface simulator,
not ArduPilot SITL and not a flight-dynamics or radio-link test. No hardware is
opened in demo mode.

### Connect to an aircraft or SITL

| Connection | Planner setting | Notes |
|---|---|---|
| Flight-controller USB | USB / ELRS serial, detected port, usually 115200 baud | Bench development; remove propellers for command testing. |
| External ELRS module USB | USB / ELRS serial, detected port, 460800 baud | Requires compatible hardware and native ELRS MAVLink configuration. |
| ELRS TX Backpack Wi-Fi | ELRS Wi-Fi / UDP, listen 0.0.0.0:14550 | Join the Backpack network on this computer first. |
| ArduPilot Copter SITL | SITL / TCP, 127.0.0.1:5760 | Start SITL separately. Set its Home to your planned field or rebuild at SITL Home. |

Choose the actual aircraft system ID (default 1). The service uses ground-station
system 255 / component 190 and accepts the selected autopilot component 1 only.
Use one mission-writing ground station at a time. Close Mission Planner's serial
connection before opening the same port here. No automatic discovery, reconnection,
firmware changes, parameter writes, force-arming, motor tests, or in-flight disarm
are performed.

ELRS setup is specific to the transmitter, receiver, and firmware. The planner
does not change it. See the [official ELRS MAVLink guide](https://www.expresslrs.org/software/mavlink/).

### Launch behavior

Build → Upload & verify → Launch mission → Confirm launch.

Upload preserves the planner's existing TAKEOFF, speed, waypoint dwell, ROI and
RTL commands. It waits for the aircraft's acknowledgment, downloads the stored
mission, and compares executable fields with coordinate/float precision tolerances.
ArduPilot's downloaded Home row is synthesized from actual Home and is checked
separately. Uploading a WPL Home row **does not set the real RTL origin**.

Launch repeats the download comparison, rereads safety parameters and Home, checks
readiness, selects GUIDED, arms with normal pre-arm checks, resets the current
mission item to TAKEOFF, selects AUTO, sends MISSION_START, and waits for airborne
telemetry. Acknowledgment alone is not treated as completed takeoff. Duplicate
operations are blocked. Return home cancels pending launch steps and requests RTL;
its success message means RTL mode was confirmed, not that landing finished.

Launch requires current heartbeat, position, GPS, estimator, sensor, landed-state
and battery data; at least 30% reported battery; all pre-arm checks enabled;
configured battery/RC/GCS failsafe actions; enabled altitude and circular fences;
and an actual Home within 30 m of both the aircraft and planned pad. The route must
fit inside the circle and altitude limits with at least 5 m margin. This first
version requires a circular fence; additional polygon/exclusion
fences, terrain, obstacles, airspace and battery endurance are not validated by
the planner. Aircraft checks and failsafes still apply.

The app checks SYSID_MYGCS=255 so the aircraft can monitor this service's heartbeat.
It checks that failsafe actions are configured but does not prove all combinations
of firmware, FS_OPTIONS, radio modes and battery-monitor settings. Verify actual
loss-of-link behavior in SITL and on the configured aircraft. Closing just the
browser does not stop the Python service or its heartbeat. Do not depend on browser
closure to trigger RTL.

Parameters and Home expire after 60 seconds; other launch telemetry expires after
5 seconds. Use **Refresh aircraft checks** if needed. A changed plan, reconnect,
or detected aircraft restart invalidates mission verification. Interrupted uploads
can leave an incomplete mission in ArduPilot; re-upload and verify before flight.
No automatic rollback or restart is attempted after an uncertain command response.

Altitude is **metres above Home**, not terrain-following AGL. The scientific
geometry preview still assumes the existing flat water/terrain model. Launch is
immediate; the planner's UTC time predicts satellite geometry and does not schedule
takeoff. Rebuild for the expected launch time when that geometry matters.

### Validation and limitations

```bash
python -m unittest discover -s tests -v
node --check static/flight.js
```

Tests cover the demo lifecycle, stale/unsafe readiness, wrong field, changed stored
mission, reconnect/reboot, cancel/RTL, local API protections, and MAVLink v2 wire
encoding/decoding with an in-memory aircraft peer (including legacy requests,
rejected arm/upload, dropped packets, foreign aircraft and stale acknowledgments).
The peer is not ArduPilot SITL. Real hardware, ELRS range/link behavior, and flight
performance still require validation. Do not treat passing software tests as flight
qualification.

The service remains bound to localhost. Flight API mutations require a per-process
token and same-origin requests; do not expose it through a proxy or public server.
MAVLink signing and multi-aircraft control are not implemented.

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
