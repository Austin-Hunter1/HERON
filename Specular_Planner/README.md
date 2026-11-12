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

## Science mode: ordered observations

Choose **Science** to combine stationary observations and shoreline transects in one mission.
The **Two hovers + three passes** preset sets 400 ft (121.92 m above Home), a 120-second
G09/E05/C12 observation, a separate 120-second E23 observation, and three passes at 3 m/s
with 25 m spacing. These are editable starting values, not a finalized flight plan.
Takeoff time and transit speed remain in Flight setup; set the date/time for the actual flight.

- Add, remove, or reorder **Hover** and **Passes** phases. Each phase has its own targets.
- List satellite IDs with the **primary first**. The primary determines aircraft placement and
  heading. All listed targets are modeled together; the preview reports their largest sampled
  heading offset. This does not guarantee that all targets fit the antenna beam.
- For a hover, select one **reflection point on water**. The drone is placed to hit that point
  at arrival, then remains stationary for the full selected duration. Its heading setpoints
  update every 10 seconds as the satellite moves; the reflection itself may drift on the water.
- For passes, select two reflection endpoints, one over water and one over land. Pick a line
  perpendicular to the shore, extending farther over water for a longer coherent observation.
  Adjust endpoints to change length, and edit count, speed, and spacing independently.
  Parallel lines are centred on the selected line, alternate directions, and use transit speed
  between passes. Spacing 0 repeats the same line.
- Use **Save setup / Load setup** to reuse the editable phases, locations, time, Home, altitude,
  constellations, elevation mask, transit speed, and antenna offset in a JSON file.
- **Build mission** gives one timed preview, satellite reflection tracks, phase summary, run
  card, and `.waypoints` export. Timing includes climb, transits, holds, passes, and return.
  Missing/below-mask requested targets produce an error rather than being silently replaced.

Science exports retain repeated hold waypoints for heading updates. Each speed/yaw command
follows the NAV waypoint it accompanies, according to
[ArduPilot's mission command association](https://ardupilot.org/copter/docs/common-mavlink-mission-command-messages-mav_cmd.html).
An explicit homebound waypoint restores transit speed before RTL; it is excluded from the
payload's default science recording window. Ordinary planning modes keep their existing export behavior.
The built-in map playback models the planned science timing. Aircraft dynamics, turns,
wind, actual signal coherence, and antenna tilt are not validated by that geometric preview.

## Aircraft connection and control - WIP (9/29/26)
THIS IS A BIG WIP STILL, UNFINISHED -jack

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

The demo flies at the mission's DO_CHANGE_SPEED, climbs and descends at
ArduCopter's default rates, and holds each waypoint for its full delay. It is an interface simulator,
not ArduPilot SITL and not a flight-dynamics or radio-link test. No hardware is
opened in demo mode.

### Connect to an aircraft or SITL

| Connection | Planner setting | Notes |
|---|---|---|
| Flight-controller USB | USB / ELRS serial, detected port, usually 115200 baud | Bench development; remove propellers for command testing. |
| External ELRS module USB | USB / ELRS serial, detected port, 460800 baud | Requires compatible hardware and native ELRS MAVLink configuration. |
| ELRS TX Backpack Wi-Fi | ELRS Wi-Fi / UDP, listen 0.0.0.0:14550 | Join the Backpack network on this computer first. |
| ArduPilot Copter SITL | SITL / TCP, 127.0.0.1:5760 | Start with `scripts/sitl.py` (below). |

For a flight-accurate test, run real ArduCopter firmware (SITL), the same thing
Mission Planner's simulator runs. Build it once (see the top of
`scripts/sitl.py`), then:

    .venv/bin/python scripts/sitl.py --params ~/Downloads/AURELIADRONE.param --mission output/HERON_....waypoints

It flies a hexa X from the mission's pad with the aircraft's mission, speed, yaw,
RTL, land and failsafe settings copied from the parameter file. Attitude, rate,
motor and battery-voltage settings stay at SITL's 3S hexa: the Aurelia X6 is a
heavy 6S airframe, and those numbers make the simulator overpowered and twitchy.

### Auto-record per phase

For a science plan the payload strip's **Auto** popup offers **Each phase**: every phase on the
run card (e.g. hover 1, hover 2, shoreline passes) becomes its own recording. Each one starts on
arrival at the phase's first waypoint and ends when its last waypoint is finished, so a hover's
final hold is kept; transits between phases are not recorded. **One range** is the old single
start/stop. Up to 4 phases. The NUC follows the mission itself either way, and an operator Stop or
RTL ends the whole plan. This needs the NUC payload software from the same commit
(`HERON_Payload/deploy/update.sh`); an older NUC refuses it and the strip says so.

### Simulate on ArduCopter SITL, or dry-run the payload

The Simulate tab's top bar picks the simulator:

| Choice | What flies | Payload strip |
|---|---|---|
| **ArduCopter SITL · X6 MAX** | Real ArduCopter firmware with the X6 MAX frame model (`sitl/x6max.json`) and tune (`sitl/x6max_sitl.parm`) | Simulated payload |
| **Dry run · real FC + NUC** | Same SITL | The real NUC, reached through the real flight controller |
| **Quick preview** | The old interface simulator (not ArduPilot) | Simulated payload |

SITL needs the one-time build at the top of `scripts/sitl.py` (`~/ardupilot`, or set `ARDUPILOT`).
It starts on the launch pad of the mission on Plan, at real-time speed, and takes about a minute
after starting for GPS/EKF before it will arm. Its flight log is in `output/sitl_sim/logs/`.

**Dry run** records real SDR data on the NUC while SITL "flies" the mission. The real flight
controller stays on stock firmware and disarmed; it only relays MAVLink between SITL and the NUC
on TELEM2, so mission-triggered recording (start at WP1, stop at the last survey waypoint), the
static holds and the payload commands all take their in-flight path:

    planner ─TCP─▶ SITL (system 2) ─▶ relay ─USB─▶ flight controller ─TELEM2─▶ NUC

1. Props off. Plug the flight controller into this computer by USB and power the NUC on TELEM2.
2. On the NUC set `HERON_SYSTEM_ID=2` in `/etc/heron/payload.env` and
   `sudo systemctl restart heron-payload`. (SITL must use a different system ID from the real
   flight controller, which drops packets that carry its own ID.)
3. Simulate → **Dry run · real FC + NUC**, pick the flight controller's USB port, **Start dry run**.
   If the Fly tab was connected to that port it is released automatically.
4. Upload & verify, arm the payload's auto-record, and launch as usual.
5. Afterwards **set `HERON_SYSTEM_ID=1` back on the NUC and restart heron-payload before flying.**
   The Fly tab's payload strip shows no signal until you do.

Only SITL's heartbeat and mission progress, and messages addressed to the payload, are passed to
the flight controller; nothing is addressed to it, so it cannot be armed from the dry run. The RF
the NUC records is whatever the bench antenna sees: this tests the capture pipeline (triggers,
timing, files, HERON Studio import), not the reflection geometry. Use the SITL log for the track.

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
readiness, selects GUIDED, arms with normal pre-arm checks, confirms Home moved to
the aircraft, resets the current mission item to TAKEOFF, selects AUTO, sends
MISSION_START, and waits for airborne telemetry (Copter reports TAKEOFF for the
whole climb). Acknowledgment alone is not treated as completed takeoff. Duplicate
operations are blocked. Return home cancels pending launch steps and requests RTL;
its success message means RTL mode was confirmed, not that landing finished.

Launch requires current heartbeat, position, GPS, estimator, sensor, landed-state
and battery data; at least 30% reported battery; all pre-arm checks enabled;
configured battery/RC/GCS failsafe actions; enabled altitude and circular fences;
and a GPS Home. The aircraft can be any distance from the planned pad: ArduPilot
resets Home to the aircraft when it arms and TAKEOFF climbs in place, so the pad
only sets the first leg. Safety parameters the aircraft never reports by name are
fetched from its full parameter list; any still missing fail only the checks that
use them. If Home is still more than 30 m away after arming (a locked Home), launch
disarms and stops before AUTO. The route must fit inside the circle, measured from
the aircraft, and the altitude limits with at least 5 m margin. This first
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

## RTK base station (Fly tab)

HERON can send RTK corrections from a GNSS base station plugged into this laptop to the drone,
the same job as Mission Planner's **RTK/GPS Inject**. Corrections ride the normal MAVLink link
(telemetry radio or Herelink) as `GPS_RTCM_DATA`; ArduPilot hands them to the Here4 over DroneCAN.

```
base receiver --USB--> laptop (HERON) --telemetry radio--> Cube Orange --DroneCAN--> Here4
```

**Aircraft parameters** (check once, Mission Planner → Config → Full Parameter List):

| Param | Set to | Why |
|---|---|---|
| `GPS_INJECT_TO` | 127 | Send corrections to every GPS (already set on the X6) |
| `GPS_TYPE` | 9 | DroneCAN GPS (Here4) — already set |
| `GPS_POS1_X/Y/Z` | Antenna offset from the centre of gravity, m (X fwd, Y right, Z down) | At cm accuracy an unset offset shows up as error when the drone tilts |
| `EK3_SRC1_POSZ` | Leave at 1 (baro) | RTK height can jump if the fix drops to float; use RTK altitude from the log in post-processing |

**Try it with no hardware**

```bash
.venv/bin/python scripts/rtk_demo.py --show-packets
```

Opens the planner with a simulated u-blox base and a simulated drone. In Fly, open the RTK bar, pick
*Simulated base (demo)*, Start base. The survey runs 4x faster; then the drone goes 3D fix -> RTK Float ->
RTK Fixed while complete corrections keep arriving. `--show-packets` prints every `GPS_RTCM_DATA` the
planner sends and the RTCM message the drone rebuilt from it. The RTCM contents are filler (real framing
and checksums, no satellite data), and the rest of the Fly page still waits for a real aircraft.

**At the field**

1. Put the base antenna on a tripod with a clear sky view, 10 m+ from the takeoff spot. Plug the base into the laptop.
2. Fly tab → **Set up base** on the RTK strip. Pick the base's port.
3. Choose how the base gets its position:
   - **Survey-in** (u-blox F9P bases): the base averages its position for at least the minimum time and until it reaches the target accuracy (2 m in about 2–5 min). Then it starts sending.
   - **Saved spot**: reuse a position you surveyed and saved before. The antenna must go back on the exact same mark. Corrections start immediately.
   - **As-is**: HERON sends no settings. Use this for a base you set up in u-center or Mission Planner, or one that isn't a u-blox.
4. Press **Start**. The strip shows the base's progress, then the drone's fix: **3D fix → RTK Float → RTK Fixed** (usually 1–5 min). The top bar GPS readout shows the same.
5. After a survey-in finishes, **Save this base position** with a name so next time you can use **Saved spot**.

Relative accuracy (drone vs base) is cm-level right away. Absolute accuracy is only as good as the base position: a 2 m survey-in shifts the whole flight by up to ~2 m. For repeatable heights across days, survey once for a long time (or post-process the base), save it, and always set the antenna on the same mark.

**Raw base data for post-processing (OPUS / PPP)**

Every time the base runs, HERON saves everything the receiver sends to `output/rtk/`:

- `<UTC time>_base.ubx`: the raw stream, including `RXM-RAWX` / `RXM-SFRBX` (turned on automatically in
  Survey and Saved position modes; in "Use the receiver's settings" mode, turn them on in u-center yourself).
- `<UTC time>_base.json`: start/stop time, mode, and `base_position`, the exact position the base's
  corrections used (from its RTCM 1005). The drone's RTK positions are relative to this point.

The panel shows *Raw data: Saving, 1:12:03 (7.4 MB)* while it records. About 10–20 MB per hour.

To get the base's true position and fix a whole day's flights to a few cm:

1. Set the tripod over a marked spot and measure the vertical antenna height above the mark.
   Leave the base logging **2 h or more** (6 h+ for the best result). Clear sky matters more than time.
2. Check the log: `.venv/bin/python scripts/check_base_log.py output/rtk/<file>_base.ubx`
   (duration, gaps, rate, satellites, GPS L2, and the base position used).
3. Convert to RINEX with RTKLIB's `convbin` (the rtklibexplorer "demo5" build handles the F9P best;
   on a Mac build it once: `git clone https://github.com/rtklibexplorer/RTKLIB && cd RTKLIB/app/consapp/convbin/gcc && make`):
   `convbin -r ubx -v 3.04 -od -os -o base.obs -n base.nav <file>_base.ubx`
   (Don't add `-ti 1`: if the receiver's epochs aren't exactly on whole seconds it drops every epoch.)
4. Upload `base.obs` to **NRCan CSRS-PPP** (https://webapp.csrs-scrs.nrcan-rncan.gc.ca/geod/tools-outils/ppp.php,
   free account, choose ITRF / Static, give the antenna height). This is the route people have used
   successfully with the ZED-F9P. NGS OPUS *static* (https://geodesy.noaa.gov/OPUS/, 2–48 h, takes
   RINEX 3) may also work: it uses GPS L1/L2 only, which the F9P records as L2C. OPUS-RS (15 min–2 h)
   needs P2 observables the F9P doesn't record, so don't count on it. On either site pick the antenna
   type exactly (NovAtel GPS-702-GG = `NOV702GG NONE`); the wrong type can put the height off by tens of cm.
5. Shift every drone position by *(post-processed antenna position − `base_position` in the .json)*.
6. Add the mark in the RTK panel: Saved position → **Add known coordinates** (ellipsoid height of the mark
   + antenna height). Next time, set up on the same mark and use that saved position: no survey, and the
   flights are absolute from the start.

The base is set to 1 Hz in Survey and Saved position modes; the raw log is roughly 10–20 MB per hour.

**Checks after the flight** (in the `.bin` log): `GPS.Status` 6 = RTK fixed (5 = float, 3 = no RTK), `GPA.HAcc` about 0.01–0.05 m.

Notes:
- u-blox settings go to the receiver's RAM and battery-backed RAM, never its flash.
- HERON keeps aircraft auto-connect off the base's port while the base is running.
- Corrections use about 1–2 kB/s. A 57600 SiK radio or Herelink is fine; ELRS MAVLink may be too slow.
- Saved spots live in `data/rtk_base.json`.
- Code: `engine/rtk.py` (RTCM framing, `GPS_RTCM_DATA` fragmenting, u-blox setup), `static/rtk-strip.js`. Tests: `tests/test_rtk.py`.

## Map layers from earlier flights

The layers button above the zoom buttons (bottom right, every tab) opens **Map layers**. For each
imported flight you can switch on:

- **Orthomosaic**: the GoPro imagery stitched onto the ground, with an opacity slider.
- **Water** (orange): where the water actually was on that flight, from heron-ar's water
  segmentation, only inside the area the camera saw.

While **Water** is on, Plan > Water can select it like any lake: click inside the orange
area to tile it, or use Shoreline edge to fly its real shoreline. Turn the layer off to pick the
mapped lake instead.

Choices are remembered in the browser. The layers sit under the plan and the aircraft and never
take clicks, so you can still place waypoints on top of them.

Add another flight from a heron-ar twin export (`ortho.jpg`, `scene.json`, `water.png`):

```bash
.venv/bin/python -m pip install numpy opencv-python-headless pillow   # once
.venv/bin/python scripts/import_overlay.py ../../heron-ar/docs/twin \
    --pos ../../heron-ar/data/POS.csv --name "Boulder Reservoir survey" --date 2026-10-19
```

`--pos` is that flight's POS.csv (its first row is heron-ar's local origin); `--origin lat,lon`
works too. Output goes to `data/overlays/<date>-<name>/` and shows up after a page reload.

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
