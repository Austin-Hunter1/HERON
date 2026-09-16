# TESTING — Test Strategy

Status: Draft 1, 2026-09-16. Rule: new logic ships with tests. A bug
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
  names (`test_<module>.py`).
- Each test has a docstring in Simplified Technical English that says
  what behavior it protects.
- No test may need network access or real hardware unless marked
  `hardware`.

## 3. Definition of done for a change

1. Unit tests added or updated.
2. `uv run pytest` passes locally.
3. `ruff` and `mypy` pass.
4. Docs updated if behavior or config keys changed.
5. If the change touches capture or trigger code: bench routine
   re-run before the next flight.
