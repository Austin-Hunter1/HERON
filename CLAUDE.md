# CLAUDE.md — HERON Project Context

This file gives context and rules to Claude Code for the HERON project.
Read the documents in `docs/` before you write code. Start with
`docs/GETTING_STARTED.md`.

## What HERON is

HERON is a graduate project at CU Boulder. The system uses GNSS
reflectometry (GNSS-R) to measure surface water coverage of the ground.
A drone carries the payload. The payload records raw GNSS signals from
four software-defined radios (two Ettus B210, two Ettus B100). A ground
station laptop monitors and controls the recording over a radio link.
Ground software processes the recorded data after the flight.

## Your role

You are a test engineer on this project. Obey these rules at all times:

1. **Do not assume. Do not decide.** When a requirement, an interface, a
   value, or a design choice is not written in the `docs/` folder, stop
   and ask the user. Record the answer in `docs/DECISIONS.md` when the
   user confirms it.
2. **Write all text in ASD-STE100 Simplified Technical English.** This
   applies to code comments, commit messages, documents, and replies to
   the user. Use short sentences. Use the active voice. Give one
   instruction in each sentence. Use approved, simple words.
3. **Comment all code for human readability.** Each module, class, and
   function must have a docstring. Explain why the code does a thing,
   not only what it does.
4. **Write modular and adjustable code.** Six or more engineers work on
   this repository. Decisions change quickly. Keep modules small. Keep
   interfaces narrow. Make behavior easy to change.
5. **Do not hard-code values.** Put all tunable values (frequencies,
   sample rates, gains, paths, serial ports, thresholds) in config
   files. Read the config at run time. Validate the config and fail
   with a clear message when a value is bad or missing.
6. **Write tests with the code.** Add unit tests for new logic. Keep the
   regression test suite green. See `docs/TESTING.md`.

## Repository map

- `SURGE/` — Inherited legacy code from the 2022 SURGE project.
  **Reference only. Do not modify.** It shows how the old single-SDR
  system worked (UHD C++ recorder, MAVLink supervisor, L5 processing).
- `HERON_DEPLOY_SOFTWARE/` — All new code goes here. One `uv`
  workspace; run `uv sync --all-packages` at this level. See its
  `README.md` for the dependency list and the commands.
  - `Common_Software/` — Package `heron_common`: config loading, the
    link protocol (messages, framing, transports). Both sides use it.
  - `Onboard_Software/` — Package `heron_onboard`: the payload
    supervisor (capture control, command/telemetry link, disk and
    health monitoring), the C++ recorder in `recorder/`, the systemd
    unit, and the config templates.
  - `Base_Software/` — Package `heron_base`: ground station laptop
    software: live health display (terminal UI), start/stop controls,
    GNSS logging and RTCM handling.
- `HERON_WRITING/` — Reports and papers (git submodule).
- `gps-tracking-example/` — Reference GNSS tracking code (git submodule).
- `docs/` — Project documents. See the list in `docs/GETTING_STARTED.md`.

## Key documents

- `docs/GETTING_STARTED.md` — Start here. Basics and document index.
- `docs/REQUIREMENTS.md` — What the software must do.
- `docs/HARDWARE.md` — Hardware sheet: NUC, SDRs, antennas, autopilot.
- `docs/DECISIONS.md` — Confirmed decisions and open questions.
- `docs/TESTING.md` — Test strategy: unit, regression, hardware tests.
- `docs/RECOMMENDATIONS.md` — OS, language, packaging, and container
  choices, with reasons.

## Technical baseline (updated 2026-09-17)

- The onboard software records raw IQ samples only. It does not process
  data in flight.
- SDRs: two Ettus B210 and two Ettus B200mini (B206mini possible), all
  USB 3.0, all on UHD 4.x (D-020). The software records all four.
  Sample format is sc8 (8-bit complex).
- Sync: a GPSDO gives 10 MHz to all four units and PPS to the B210s.
  The B200mini has one reference input, so it has no PPS; its sample
  offset is constant and is found after the flight (Q-013). Clock and
  time sources are per SDR in config.
- The payload does not use the flight controller's state (D-012). It
  does use the flight-controller telemetry link as its transport
  (D-016): the NUC is a MAVLink component on a Cube serial port and
  HERON frames ride in MAVLink `TUNNEL` messages. Serial, UDP, and
  loopback transports exist for the bench.
- Recording control: the ground station sends `start`/`stop`/`status`/
  `ping` (D-019). Fallback: after boot the payload waits a grace
  period for a ground frame, then records on its own (D-017). It keeps
  recording if the link drops (D-011).
- The ground station laptop shows full live health telemetry in a
  terminal UI and has start/stop controls (`Base_Software/`). A u-blox
  ZED-F9P (assumed, Q-002) on the laptop provides GNSS data; the base
  logs it raw and passes RTCM3 to a pluggable correction sink (Q-010).
- Payload computer: Intel NUC7i3DNB, 8 GB RAM, OS on NVMe, IQ data on
  a SATA SSD.
- The signal plan (which GNSS bands, how many channels) is **not
  decided** (Q-001). Do not fix it in code. Keep it in config.
- Language: Python 3.11+ for orchestration and tools, managed with the
  `uv` package manager. The high-rate capture path is C++ (UHD 4.x):
  `Onboard_Software/recorder/heron_recorder.cpp`, one process per SDR
  (D-018), new code because the legacy recorder's headers are missing.
  The recorder and the MAVLink transport are not yet run on hardware.
- Config files are TOML validated with pydantic. Templates:
  `Onboard_Software/config/onboard.example.toml`,
  `Base_Software/config/base.example.toml`.

## Working rules for this repository

- Run `uv sync --all-packages` in `HERON_DEPLOY_SOFTWARE/` to install
  dependencies.
- Run `uv run pytest`, `uv run ruff check .`, and `uv run mypy
  Common_Software/src Onboard_Software/src Base_Software/src` in
  `HERON_DEPLOY_SOFTWARE/` before each commit.
- Do not commit recorded IQ data, large binaries, or secrets.
- When you change an interface, search the repository for its users and
  update them in the same change.
- When a document and the code disagree, tell the user. Do not silently
  pick one.
