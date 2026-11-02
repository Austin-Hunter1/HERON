# HERON payload

Three pieces, each with one job:

| Piece | Where it runs | What it does |
|---|---|---|
| **Payload Setup** (web app) | On the NUC, opened in a browser over an Ethernet cable at `http://heron-nuc.local:8080` | Pick radios and channels, frequency, sample rate, gain, presets. Find radios on USB. Record a test clip and check the signal level. Bench only. |
| **Payload strip** (in Specular Planner) | Simulate and Fly decks, top centre | Shows recorder state, time, write rate, disk minutes left, dropped samples. **Record / Stop**, and **Auto** (record from one survey waypoint to another). |
| **Payload service** (`heron_payload.agent`) | On the NUC, started at boot by systemd | Talks MAVLink to the Cube on TELEM2, runs the UHD recorder, serves Payload Setup. |

```
Specular Planner ──telemetry radio── Cube TELEM1        Cube TELEM2 ──USB-UART── NUC ──USB 3── B210
  payload strip     (sys 255, comp 190)  routes MAVLink                         payload service (sys 1, comp 191)
                                                                                  └─ Payload Setup on Ethernet :8080
```

Radio settings never travel over the telemetry radio. The ground station only sends Record / Stop / Auto
and receives a few status numbers, all as standard MAVLink, so the same messages also appear in Mission
Planner and QGroundControl.

## The MAVLink protocol (protocol.py)

* **Commands:** `COMMAND_LONG` to component 191, command `MAV_CMD_USER_1` (31010).
  `param1`: 0 stop, 1 start, 2 arm auto-record (`param2` start item, `param3` stop item), 3 cancel auto, 4 resend status.
  `param4` is a request id: a retry after a lost acknowledgment repeats the id and the payload does not act twice.
* **Answer:** `COMMAND_ACK` (`result_param2` = request id). A refusal also sends `STATUSTEXT "HERON: <reason>"`.
* **Status, 1 Hz:** `HEARTBEAT` (onboard controller) and `NAMED_VALUE_FLOAT`:
  `HRN_STATE`, `HRN_REC_S`, `HRN_MBPS`, `HRN_MINLFT`, `HRN_FREEGB`, `HRN_OVF`, `HRN_AUTO`.
* **Events:** `STATUSTEXT` on every change, for example `HERON REC START at item 4 (mission)`.

### Auto-record rules (controller.py)
* Armed with a start and stop **mission item** (the strip shows them as the map's WP1, WP2…).
* Starts when the Cube reports **arrival** at the start item while armed in AUTO
  (late start if the arrival message was missed but the drone is already inside the survey).
* Stops on arrival at the stop item, on RTL/LAND, on leaving AUTO (setting), or on disarm (setting).
* The NUC follows the mission itself, so a radio dropout does not stop or skip a recording.
* Manual Record keeps going through mode changes and stops only on Stop or disarm.
* Disk below the reserve, or a recorder crash, stops recording and shows a fault.

## Recorded files

Where: `/media/DataStore/heron` when the data drive is mounted there, otherwise `~/heron_recordings` on the
NUC's own SSD (storage.py; the disk reserve still applies). Payload Setup's **Recordings** section lists
both, draws a spectrogram and histogram of any channel in the browser (recordings.py sends ~1 MB of sample
slices) and downloads single files.

`<storage>/HERON_<UTC time>/` → `session.json`, `completion.json`, and per radio a folder with `settings.json`
(actual UHD settings), `channel_N_000000.sc8` … (interleaved I,Q int8, 30 s per file by default) and
`samples.jsonl` (sample offsets, device time, UHD error codes for gap detection).

Recording method (HERON D-078): the collect wizard's `rx_samples_to_file`. The file format is also the USB
format, so for 8-bit files UHD sends sc8 over USB and scales it (`peak` × full scale maps to ±127; `peak` = 1.0 by
default, the wizard value). SURGE's `<peak>0.25</peak>` had no effect: its custom `sc8_0` converter ignores the
scale. **Write method** `direct` (default) receives one buffer of **Samples per buffer** (`spb`, 10000) and writes
it before the next receive; `threaded` queues buffers for a writer thread (up to 512 MB). **File buffer** sets the
buffer in front of each file (0 = library default). **USB buffers** passes UHD device args such as
`num_recv_frames=256,recv_frame_size=16000`. Change these on the bench, record a test clip, and watch overflows
to find the values the NUC needs. Each radio's **Profile** list has the wizard's radio profiles
(L5 / L1 15 MS/s 15 MHz, L1CA 5 MS/s 5 MHz); **Bandwidth** is the analog filter and takes any typed value.
The Signal check reports clipping and suggests a gain.

## Automated tests in Payload Setup (sweeps.py)

The **Automated tests** panel records short trials with the loaded setup, then loads that setup again. Nothing
changes in the setup until you press **Use**; then save the setup and load it. Bench only (refused while armed).

* **Gain sweep:** the collect wizard's gain calibration for every enabled channel at once: a coarse sweep
  (10-70 dB), one trial at the gain predicted from the median of (sigma dB - gain dB) of the clean trials, then
  2 dB steps between the two best. Score = -|sigma/full scale - 1/4.5| - 50 x clipped fraction, with sigma from
  the 16th-84th percentile spread. Each channel gets its own best gain.
* **Buffer sweep:** every combination of write method, samples per buffer, USB buffers (UHD device args) and
  file buffer. A trial is clean with no overflow, no gap, and no drop in samples.jsonl ("Find drops").

Trial recordings are deleted unless "Keep the trial recordings" is on. Each result is also saved as JSON in
`<state dir>/sweeps/` (`/var/lib/heron/sweeps` on the NUC).

## Testing without hardware

```sh
# 1. Payload service with simulated radios (no Cube needed), Setup app on http://127.0.0.1:8080
python -m heron_payload.agent --connect udpout:127.0.0.1:14599 --backend fake --state-dir /tmp/heron --http 127.0.0.1:8080

# 2. Planner: Simulate tab already has a simulated payload; Record / Auto work against the simulated drone.

# 3. Full chain with real ArduCopter firmware (SITL; see Specular_Planner/scripts/sitl.py):
#    SITL's SERIAL2 (TELEM2) is tcp:5763. Connect HERON Fly to TCP 127.0.0.1:5760, then:
python -m heron_payload.agent --connect tcp:127.0.0.1:5763 --backend fake --state-dir /tmp/heron --http 127.0.0.1:8080

# Unit + link tests
python -m unittest discover -s tests
```

## NUC setup (fresh, headless, Ethernet only)

Follow **[SETUP_GUIDE.md](SETUP_GUIDE.md)**: every step from a Windows laptop, from backing up the old
NUC and installing Ubuntu Server 24.04 to the bench checklist. Network in short: the NUC is always
`192.168.137.10`, the laptop's Ethernet port `192.168.137.1` (Windows Internet Connection Sharing gives the
NUC internet through the laptop's Wi‑Fi when it needs updates). Payload Setup: `http://192.168.137.10:8080`.

## Updating an installed NUC

Copy the new `HERON_Payload` folder to the NUC, then `sudo bash HERON_Payload/deploy/update.sh` (no internet
needed; keeps setups, `payload.env` and recordings). Details in SETUP_GUIDE.md.

## Status

* v0.2.0: Recordings section (spectrogram, histogram, downloads), automatic NUC-SSD fallback, `update.sh`,
  *One B200* preset. 32 payload tests pass, plus the planner integration tests.
* Tested here: 24 payload tests (setup validation, auto-record rules, signal check, MAVLink agent with real
  MAVLink 2 bytes, lost-ack retry), 9 planner integration tests (planner ↔ agent over MAVLink), the recorder's
  disk writer compiled and tested, the full recorder compiled against UHD 4.6, and a complete simulated flight
  in the planner where recording started on arrival at WP1.
* Not yet tested on hardware: the B210 itself, USB throughput, TELEM2 routing through a real Cube, external PPS.
