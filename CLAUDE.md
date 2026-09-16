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
- `HERON_DEPLOY_SOFTWARE/` — All new code goes here.
  - `Onboard_Software/` — Code that runs on the payload computer (NUC)
    in flight: capture supervision, command/telemetry link, disk
    monitoring.
  - `Base_Software/` — Ground station laptop software: live health
    display, start/stop controls, GNSS/RTK data handling.
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

## Technical baseline (updated 2026-09-16)

- The onboard software records raw IQ samples only. It does not process
  data in flight.
- SDRs: two Ettus B210 and two Ettus B100. The software records all
  four. Sample format is sc8 (8-bit complex).
- All SDRs share a common oscillator. The time-alignment method across
  units is an open question (Q-004).
- The payload is separated from the flight control schema. There is no
  MAVLink link between the NUC and the Cube Orange.
- Recording control: the ground station laptop sends start and stop
  commands over a payload radio link (hardware open, Q-006). The
  payload has an autonomous fallback and keeps recording if the link
  drops.
- The ground station laptop shows full live health telemetry and has
  start/stop controls (`Base_Software/`). A GNSS receiver on the
  laptop provides RTK correction data.
- Payload computer: Intel NUC7i3DNB, 8 GB RAM, OS on NVMe, IQ data on
  a SATA SSD.
- The signal plan (which GNSS bands, how many channels) is **not
  decided**. Do not fix it in code. Keep it in config.
- Language: Python 3.11+ for orchestration and tools, managed with the
  `uv` package manager. The high-rate capture path stays in C++ (UHD),
  inherited and adapted from `SURGE/NUC_scripts/SDR_backup_files/`.
  Caution: verify a UHD version that supports both B210 and B100
  (Q-011). See `docs/RECOMMENDATIONS.md`.

## Working rules for this repository

- Run `uv sync` in `HERON_DEPLOY_SOFTWARE/` to install dependencies.
- Run tests with `uv run pytest` before each commit.
- Do not commit recorded IQ data, large binaries, or secrets.
- When you change an interface, search the repository for its users and
  update them in the same change.
- When a document and the code disagree, tell the user. Do not silently
  pick one.
