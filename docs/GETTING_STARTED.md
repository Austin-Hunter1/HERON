# GETTING_STARTED — HERON Deploy Software

Read this document first. It tells you what HERON is, how the system
works, and where to find the other documents.

## 1. What HERON does

HERON measures surface water coverage of the ground with GNSS
reflectometry (GNSS-R). GNSS satellites transmit signals all the time.
Some of that signal reflects off the ground and off water. Water
reflects GNSS signals more strongly than dry ground. A drone carries
two antennas: one points up (direct signal) and one points down
(reflected signal). We record both signals in flight. On the ground we
correlate the recordings to measure the reflected power, and we use
precise drone positions (PPK) to map where each reflection came from.

## 2. The system in one paragraph

An Intel NUC (NUC7i3DNB) rides on the drone. Two Ettus B210 SDRs and
two Ettus B200mini SDRs (all USB 3.0) connect to the NUC and share a
common 10 MHz reference so their sample streams stay coherent. The
NUC records raw IQ samples to a SATA SSD; the OS runs on a separate
NVMe drive. The payload does not depend on the Cube Orange flight
controller's state (it never checks flight mode), but it does use the
Cube's telemetry link as its control channel: the NUC talks MAVLink
to the Cube, which forwards HERON's messages to the ground. A ground
station laptop reads that link: it shows live health data (recording
state, SDR lock, overflows, disk space) and sends the start and stop
commands. If the link drops, the payload keeps recording. A GNSS
receiver on the laptop provides RTK correction data. After the
flight, ground software processes the raw data.

## 3. Vocabulary

- **GNSS** — Global Navigation Satellite System (GPS, Galileo, etc.).
- **GNSS-R** — GNSS reflectometry: use of reflected GNSS signals to
  sense the surface.
- **SDR** — Software-defined radio. The B210 and B200mini convert
  radio signals to digital samples.
- **IQ samples** — Pairs of numbers (in-phase, quadrature) that
  represent the raw radio signal.
- **UHD** — USRP Hardware Driver. The Ettus software that controls the
  B210 and B200mini.
- **MAVLink** — The message protocol of the autopilot. Legacy SURGE
  used it to trigger recording from flight mode. The HERON payload
  does not read flight mode, but it does use MAVLink as a bit-pipe:
  its own messages travel inside MAVLink `TUNNEL` frames on the Cube's
  telemetry link (D-016).
- **RTK / PPK** — Real-time / post-processed kinematic GNSS. Both use
  base station correction data to give centimeter-level drone
  positions.
- **Telemetry** — The live health data the payload streams to the
  ground station.
- **Specular point** — The point on the ground where the reflection
  occurs.
- **SURGE** — The 2022 predecessor project. Its code is legacy
  reference material in `SURGE/`.

## 4. Repository layout

- `CLAUDE.md` — Rules and context for Claude Code. Humans: read it too.
- `docs/` — All project documents (this folder).
- `HERON_DEPLOY_SOFTWARE/` — One `uv` workspace with three packages
  (see its `README.md` for dependencies and commands):
  - `Common_Software/` — `heron_common`: config loading and the link
    protocol, used by both sides.
  - `Onboard_Software/` — `heron_onboard`: the NUC supervisor, the C++
    recorder, the systemd unit, config templates.
  - `Base_Software/` — `heron_base`: the ground station laptop: health
    display, controls, GNSS/RTK handling.
- `gnss_processing/` — The post-processing tools: HERON's own working
  copy of gps-tracking-example (acquisition, tracking, navigation). It
  reads each flight directory as one "experiment" through the
  `metadata.yml` the payload writes (`DATA_FORMATS.md`). It has its own
  `pyproject.toml` and a git submodule at `submodules/gnss-tools`.
- `SURGE/` — Legacy code. Read it. Do not change it.
- `HERON_WRITING/`, `gps-tracking-example/` — Git submodules (reports;
  the upstream reference code that `gnss_processing/` was copied from).

## 5. Document index

| Document | Purpose |
| --- | --- |
| `GETTING_STARTED.md` | This document. Basics and index. |
| `REQUIREMENTS.md` | What the software must do. |
| `HARDWARE.md` | Hardware sheet for the payload and ground segment. |
| `DECISIONS.md` | Confirmed decisions and open questions. |
| `DEPLOYMENT.md` | How to install the software on a fresh machine. |
| `USAGE.md` | How to operate the system for a flight. |
| `TEST_BENCH_ROUTINE.md` | The standard bench test sequence. |
| `BENCH_DEBUG_SOP.md` | How to debug problems on the bench. |
| `FIELD_DEBUG_SOP.md` | How to debug problems in the field. |
| `TESTING.md` | Test strategy: unit, regression, and hardware tests. |
| `DATA_FORMATS.md` | File formats, naming, and directory layout. |
| `RECOMMENDATIONS.md` | OS, language, packaging, container choices. |

## 6. Set up a development machine

1. Install git and clone the repository with submodules:
   `git clone --recurse-submodules https://github.com/Austin-Hunter1/HERON.git`
2. Install `uv` (the Python package manager we use):
   `curl -LsSf https://astral.sh/uv/install.sh | sh`
3. Go to `HERON_DEPLOY_SOFTWARE/` and run `uv sync --all-packages`.
   This creates a virtual environment and installs the locked
   dependencies of all three packages.
4. Run the tests: `uv run pytest`.
5. See the display without hardware: `uv run heron-base demo`.
6. Read `docs/REQUIREMENTS.md` and `docs/DECISIONS.md` before you write
   code.

### What is uv?

`uv` is a fast Python package and project manager. It replaces pip,
venv, and pip-tools with one tool. You declare dependencies in
`pyproject.toml`. `uv` writes an exact lock file (`uv.lock`) so every
engineer and every machine installs the same versions. Common commands:

- `uv sync --all-packages` — create the environment and install the
  locked dependencies of every workspace package.
- `uv add <package>` — add a dependency and update the lock file.
- `uv run <command>` — run a command inside the project environment
  (example: `uv run pytest`, `uv run python main.py`).
- `uv python install 3.11` — install a specific Python version.

Commit `pyproject.toml` and `uv.lock`. Do not commit the `.venv/`
folder.

## 7. Rules for all contributors

- Write all comments and documents in ASD-STE100 Simplified Technical
  English: short sentences, active voice, one instruction per sentence.
- Do not hard-code values. Put tunable values in config files.
- Do not modify `SURGE/`.
- Ask before you decide. Record decisions in `docs/DECISIONS.md`.
