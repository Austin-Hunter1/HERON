# TESTING — Test Strategy

Status: Draft 2, 2026-09-17. Rule: new logic ships with tests. A bug
fix ships with a test that fails before the fix and passes after.

## 1. Test levels

### Unit tests (light, required)

- Tool: `pytest`, run with `uv run pytest`.
- Scope: pure logic — config validation, the recording state machine
  (command and fallback → start/stop decisions), disk-threshold math,
  telemetry message packing, filename and metadata generation, log
  parsing.
- Hardware interfaces (UHD, the radio link, filesystem) sit behind thin
  wrapper modules. Unit tests replace the wrappers with fakes. Do not
  mock deep inside third-party libraries.
- Keep them fast (< 30 s total) so everyone runs them before commit.

### Regression tests (required)

- Purpose: with 6+ engineers and fast-changing decisions, protect
  behavior that already works.
- The full `pytest` suite is the regression suite. It runs on every
  push via CI (GitHub Actions — add the workflow when the package
  exists).
- Golden-file tests: keep small recorded IQ snippets (seconds, not
  minutes) and expected outputs (metadata, quick-look acquisition
  results) in `tests/data/`. A change that alters the output must
  update the golden file in the same reviewed change.
- Config regression: every config template in the repository must load
  and validate in a test. A renamed key that breaks a template fails
  CI, not a flight.

### Hardware-in-the-loop tests (recommended)

- Marked `@pytest.mark.hardware`; skipped in CI, run on the bench NUC
  with real SDRs and the ground link hardware.
- These automate parts of `TEST_BENCH_ROUTINE.md`: enumeration of all
  four SDRs, short capture with zero overflows, a ground-commanded
  START/STOP cycle, a link-loss keep-recording check.

### Recommended additions

- Endurance/soak test script (long capture with overflow and thermal
  logging) — run before each field campaign.
- Fault-injection tests: fake SDR disconnect, fake ground-link
  dropout, disk-full simulation against the wrapper fakes.
- Static checks in CI: `ruff` (lint + format) and `mypy` on new code.
- Replay test: feed a recorded command/telemetry log through the
  recording state machine and assert the start/stop timeline.

## 2. Layout and conventions

- Tests live in `HERON_DEPLOY_SOFTWARE/*/tests/`, mirroring module
  names (`test_<module>.py`). The three `tests/` folders have no
  `__init__.py`; pytest runs with `--import-mode=importlib` (set in
  the root `pyproject.toml`) so same-named files in different
  packages do not collide.
- Each test has a docstring in Simplified Technical English that says
  what behavior it protects.
- No test may need network access or real hardware unless marked
  `hardware`.
- Hardware interfaces have fakes: `LoopbackTransport` (link),
  `FakeCaptureBackend` (recorders), `DiskMonitor(usage_fn=...)`
  (disk). The supervisor and the link client take a `clock` callable,
  so loop tests step a fake clock instead of sleeping.

### What exists today (2026-09-17)

- `Common_Software/tests/`: framing and CRC, message encode/decode and
  size budget, loopback transport, MAVLink chunking and reassembly,
  config loading.
- `Onboard_Software/tests/`: config validation and every template,
  the recording controller (start/stop, retry idempotence, link loss
  keeps recording, fallback modes, disk low, capture fault), disk
  threshold math, recorder command line and status parsing, metadata
  (JSON and the SDR team's metadata.yml, plus rebuild from sidecars),
  the recorder console log file, band validation, capture manager, and
  the whole supervisor loop over a loopback link.
- `Base_Software/tests/`: link health thresholds, link client (ack
  matching, retries, abandon, flight log), NMEA GGA, RTCM3 splitting
  and CRC-24Q, the GNSS data path, config templates, a headless
  Textual test of the terminal display (telemetry shown, START dialog
  sends a START the fake payload accepts), and an HTTP test of the web
  display (page is self-contained, `/api/state` reflects the link,
  `/api/command` starts and stops the fake payload, bad requests get
  400/404).

Run from `HERON_DEPLOY_SOFTWARE/`: `uv run pytest` (about 1 s),
`uv run ruff check .`, `uv run mypy Common_Software/src
Onboard_Software/src Base_Software/src`.

## 3. Definition of done for a change

1. Unit tests added or updated.
2. `uv run pytest` passes locally.
3. `ruff` and `mypy` pass.
4. Docs updated if behavior or config keys changed.
5. If the change touches capture or trigger code: bench routine
   re-run before the next flight.
