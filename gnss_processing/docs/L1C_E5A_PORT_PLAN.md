# GPS L1C and Galileo E5a Port Plan, with a BOC(6,1) Toggle

Written: 2026-10-20. Status: **plan only. No code has been changed for this plan.**
Branch: `feat/bds-b2a-b1c-ttjeh0` (or a new branch; see OQ-0).
Model: the BeiDou port (`BDS_PORT_PLAN.md`, decisions D-044 to D-066). Use the same working
method: one phase at a time, ask before a decision the plan does not cover, commit when the
"Done when" check passes, then stop and report.

---

## 0. How to use this document

1. Read sections 1 to 5 before you write code.
2. Do one phase at a time. The user says "Do Phase X".
3. Each phase has files, steps, tests and a "Done when" check. Commit only when the check passes.
4. Run the whole test suite before each commit. Compare the failures with the baseline of
   section 6.3. A new failure is a defect, unless the plan says it is expected.
5. Record every answer of the user in `docs/DECISIONS.md` (next free number is D-067).
6. Obey the rules of the root `CLAUDE.md`: STE English, docstrings that give the reason,
   modular code, no hard-coded tunable values, tests with the code, `SURGE/` is read only.
7. Text that is marked **[ICD]** is from memory or from the repository. **Confirm it against the
   attached ICD before you use it.** Do not copy a number from this plan into code.

## 1. Goals

| # | Goal |
| --- | --- |
| G1 | GPS L1C works end to end: codes, signal, acquisition, tracking, CNAV-2 message, position fix. |
| G2 | Galileo E5a works end to end: codes, signal, acquisition, tracking, F/NAV message, position fix. |
| G3 | A **switch** turns the BOC(6,1) part of the pilot on and off for B1C (QMBOC) and L1C (TMBOC), so the user can compare tracking performance with and without it. |
| G4 | A comparison method (synthetic and real data) that shows what the BOC(6,1) part is worth. |
| G5 | Documents and tools for hand-over: README, notebook text, runner examples, follow-up list. |

Non-goals (put them on the follow-up list, do not start them): Galileo E1 (CBOC), Galileo E5b and
E5 AltBOC, I/NAV, GLONASS, a batch tool, an ionosphere model for Galileo.

## 2. What the repository has today (facts found on 2026-10-20)

### 2.1 GPS L1C

- `utils/signal_interfaces.py` has a full `GpsL1C` class, acquisition policy and tracking policy
  (pilot carrier, data and pilot in the delay loop, Costas until the overlay locks, FFT overlay
  search with 200 prompts). It sits inside a `try` block. It needs these names from
  `gnss_tools.signals.gps_l1c`: `CARRIER_FREQ`, `CODE_RATE`, `PRIMARY_PERIOD_MS`,
  `get_GPS_L1CD_code_sequence`, `get_GPS_L1CP_code_sequence`, `get_GPS_L1CO_overlay_sequence`,
  `L1CD_POWER_FRACTION`, `L1CP_POWER_FRACTION`, `BOC_SUBCARRIER_RATE`, `TMBOC_PATTERN`,
  `TMBOC_SUBCARRIER_RATE`.
- The pinned `gnss-tools` commit (b5409db) does **not** have these names. Its `gps_l1c.py` has
  only the pilot Weil code and says "INCOMPLETE". So `GpsL1C` is `None` here, and **44 tests
  fail or cannot be collected** (`docs/BDS_BASELINE_TESTS.md`): `test_l1c.py` (22),
  `test_tracking_regression.py` (17), `test_coherent_integration.py` (4),
  `test_epoch_symbol_alignment.py` (1), and collection errors in `test_acquisition.py`,
  `test_double_estimator.py`, `test_l1c_codes.py`.
- The tests and golden files for L1C **already exist**. `tests/test_l1c_codes.py` checks 420
  ranging codes and 210 overlay codes against `tests/data/is_gps_800j_l1c_checksums.json`
  (first and last chips from IS-GPS-800). Golden files: `tests/golden/l1c_*.npz`.
  The code only has to provide the missing module. This is the biggest advantage for G1.
- `utils/code_components.py` already has `SubcarrierKind.TMBOC` (BOC(6,1) on a per-chip pattern),
  `BOC_SIN`, and `power_weight`. The correlator handles subcarriers. The tracker has a
  "double estimator" (second delay loop on the subcarrier, `subcarrier_bandwidth_hz`,
  `subcarrier_chip_spacing`), which notebook 01 turns on for L1C.
- `utils/nav/cnav2.py` decodes subframe 1 (TOI, BCH(51,8)), de-interleaves subframes 2 and 3
  (38 x 46 array), and finds the frame start from the overlay. It **does not decode the LDPC
  codes** (`decode_subframe_2` raises). So L1C gives a TOI but no ephemeris and no ITOW.
  Notebook 02 stops for `GPS_L1C` with `NotImplementedError`.
- `utils/nav/ephemeris.py` has `CnavEphemeris` (the CNAV orbit model that L1C subframe 2 also uses
  **[ICD]**), and `utils/nav/cnav.py` has CNAV field parsers that can be reused or compared.

### 2.2 Galileo E5a

- Nothing exists except the word "Galileo" in D-039 (signal plan), `TODO_SIGNALS.md`, and a few
  comments. No codes, no signal class, no message decoder, no ephemeris class.
- E5a is on the same carrier (1176.45 MHz) as GPS L5 and BeiDou B2a **[ICD]**. The structure is the
  same as B2a: a data component on I and a pilot on Q, 10.23 Mcps, 1 ms primary code, a secondary
  code on each. So most of the B2a work transfers (`signal_interfaces.py`, `nav/symbols.py`,
  notebook 01 tables).
- `utils/time_systems.py` knows GPST and BDT only. `utils/navigation.py` solves one clock plus one
  inter-system bias for each extra system letter (first character of the satellite id). A
  third system (`E`) fits this design without a change in the solver.

### 2.3 What the BeiDou port already gives us

| Piece | Reuse for |
| --- | --- |
| `bds_b2a.py`, `bds_b1c.py` pattern (generators in `utils/`, tables in `docs/icd/*.json`, ICD tests) | `gps_l1c.py`, `gal_e5a.py` |
| `docs/icd/*.json` + test of first and last chips (octal) | the same check for E5a (and L1C, which has the JSON already) |
| `nav/ldpc.py` (GF(64) non-binary LDPC, min-sum) | **not** for L1C (binary LDPC). Use as a style model only. |
| `nav/bcnav1.py` (frame search without a preamble, interleaver, CRC) | CNAV-2 frame handling |
| `nav/primitives.py` (CRC-24Q, convolutional encoder, BCH(51,8) for L1C) | F/NAV (convolutional code, CRC), CNAV-2 subframe 1 |
| `multi_signal.py`, `observables.combine_observables`, per-system ISB in `navigation.py` | combined GPS + BeiDou + Galileo fix |
| symbol-grid tie to the overlay counter (D-059) | E5a (acquires modulo 1 ms like B2a) |
| tests with synthetic signals (`tests/synthetic.py`, `test_b2a.py`) | `test_e5a.py`, a BOC(6,1) test suite |
| `scripts/run_notebook.py`, skill, README pattern | Phase 9 of this plan |

## 3. The BOC(6,1) toggle: design

### 3.1 The physics (what to explain in the report)

- **B1C (QMBOC(6,1,4/33)).** Data: BOC(1,1) on I, power 1/4. Pilot: BOC(1,1) on Q, power 29/44, and a
  BOC(6,1) part on I (in phase with the data), power 1/11 **[ICD: confirm in BDS B1C ICD 4.2.2;
  the repo has it in `BDS_PORT_PLAN.md` section 11.3]**. The BOC(6,1) part uses the same
  pilot primary code and overlay.
- **L1C (TMBOC(6,1,4/33)).** The pilot is time-multiplexed: BOC(6,1) on 4 of every 33 chips and
  BOC(1,1) on the other 29. Power: data 1/4, pilot 3/4. So the BOC(6,1) chips hold
  3/4 x 4/33 = **1/11** of the total power **[ICD: IS-GPS-800 3.2.1.7]**. The same 1/11 as B1C.
- **Cost of leaving it out.** The receiver then correlates only the BOC(1,1) part. It loses
  10 log10(1 - 1/11) = **0.41 dB** of pilot power. It also loses the high-frequency edge of the
  signal: the BOC(6,1) part has a much larger RMS bandwidth, so it gives a sharper code
  discriminator and less thermal and multipath error.
- **Cost of including it.** It needs sample rate and front-end bandwidth: the main lobes of
  BOC(6,1) are at about +/-6 to +/-8 MHz from the carrier. A recording of 15 Msps complex is
  marginal. About 25 Msps or more is safe **[confirm with a spectrum plot in notebook 00]**.

### 3.2 One option, two meanings

The option is called `boc61` (or `wideband`; see OQ-5). It is a property of the **receiver
replica and the tracking loops**, not of the signal in the air.

| Signal | `boc61 = on` (wide-band) | `boc61 = off` (narrow-band) |
| --- | --- | --- |
| B1C | Pilot replica = BOC(1,1) on Q **plus** BOC(6,1) on I (third code component) | Pilot replica = BOC(1,1) on Q only (today's `BdsB1C`, D-062) |
| L1C | Pilot replica = TMBOC (BOC(6,1) on 4 of 33 chips) (today's `GpsL1C`) | Pilot replica = BOC(1,1) only. The 4 of 33 BOC(6,1) chips are **gated off** (zero) or left mismatched (OQ-6) |

Today the defaults are opposite (B1C narrow-band, L1C wide-band). The switch makes them
symmetric. The default of each signal is an open question (OQ-5).

### 3.3 Where the option lives (modular, no hard-coded values)

1. **Signal level.** `Signal._build_components(prn, options)` gets an options object. A small
   frozen dataclass `SignalOptions(boc61: bool)` (extensible later for CBOC). Signals that have
   no BOC(6,1) part ignore it and raise a clear error if it is set to a value that has no meaning
   for them (for example `boc61=True` on E5a is meaningless: raise, or ignore with a warning;
   decide in OQ-5).
2. **Policy level.** The tracking policy depends on the variant, because the delay loop uses
   different components. `TRACKING_POLICIES` becomes a function of `(signal_type_id, options)`,
   or each variant gets its own key (for example `"BDS_B1C"` and `"BDS_B1C_WB"`). Pick one in
   Phase T1 (OQ-7). The policy keeps the carrier loop on the pilot Q branch.
3. **Plumbing.** `build_signals(...)`, `build_acquisition_code_params(...)`,
   `create_tracking_channels(...)` accept the options. They already take the signal class; add
   one argument and pass it on. **Acquisition** stays on the BOC(1,1) pilot in both variants
   (so the acquisition results do not depend on the switch).
4. **Notebook.** One variable in the notebook 01 config cell, for example `BOC61_ENABLED = False`.
   It must appear near `SIGNAL_ID`. No value in code.
5. **Metadata.** The tracking file must record the option (`tracking_io`), and the tracking
   file name must show it (a tag such as `_wb`), so a narrow-band and a wide-band run of the
   same recording do not overwrite each other. Notebook 02 and `compare_bds_orbits.py` read
   the option from the file and print it.
6. **Sample-rate guard.** If `boc61` is on and the sample rate is below a limit that comes from
   config (default from the spectrum analysis; see 3.5), notebook 01 stops with a clear message.

### 3.4 Wide-band B1C: how the replica is built

Two choices (OQ-7). Decide in Phase T0. Recommended: start with choice A.

- **A. Three components, non-coherent in the delay loop.** Add `B1CP61`: pilot code and overlay,
  `Branch.I`, `Subcarrier(BOC_SIN, 6.138 MHz)`, `power_weight = 1/11`. The correlator already
  correlates each component. The delay discriminator already combines components by
  `power_weight` (`_combine_code_magnitude`). No new correlator code. It gains in the code
  loop. It does **not** add the 1/11 power coherently to the carrier loop.
- **B. A coherent composite pilot.** One component whose replica is complex:
  `a * BOC11 on Q + b * BOC61 on I`. The prompt of the pilot then holds the full pilot power.
  This needs a "composite component" in `code_components.py` and the correlator. It is better,
  and it is the right model for a QMBOC receiver, but it changes shared code. Do it only if A
  shows a gain worth the change.

### 3.5 Questions the comparison must answer

- What does wide-band tracking give in C/N0 (expected: about +0.4 dB on the pilot, **[confirm]**)?
- What does it give in code-phase noise (expected: lower), measured as the standard deviation of
  code-minus-carrier (CMC) and of the pseudorange residuals?
- Does it change the number of lock losses or the time to lock?
- Does it change the position scatter and bias?
- What does it cost in run time, and in the minimum sample rate?

---

## 4. Open questions (with recommended answers)

Ask these at the start of the matching phase. Record the answers in `docs/DECISIONS.md`.

| ID | Question | Recommended answer | Phase |
| --- | --- | --- | --- |
| OQ-0 | Which branch? Continue `feat/bds-b2a-b1c-ttjeh0`, or start `feat/l1c-e5a`? | New branch `feat/l1c-e5a` from the current head, so BeiDou review stays clean. | L0 |
| OQ-1 | The two ICDs: which documents and versions? Does the Galileo document include the **F/NAV message** (Galileo OS SIS ICD) and not only the signal plan? Does the GPS document include IS-GPS-800 **and its CNAV-2 message** section? | Put both in `docs/icd/`. Both are needed. | L0, E0 |
| OQ-2 | Which recordings validate? Needs L1 (GPS L1C, B1C) and L5 (E5a, B2a, GPS L5) data. The `dualradio_L5_L1W_20261013_191744` recording (D-049) has both bands. Is its sample rate high enough for wide-band? How long is it? | Use it. Record the sample rate and duration. Add a **longer L5 recording** for F/NAV (see 6.4). | L3, E3 |
| OQ-3 | Where do the code generators live? | `utils/gps_l1c.py` and `utils/gal_e5a.py`, as in D-048. Do not change the `gnss-tools` submodule. | L1, E1 |
| OQ-4 | The L1C tests import `gnss_tools.signals.gps_l1c`. May Claude change **only that import** in the tests (to `utils.gps_l1c`)? | Yes. Change nothing else in the old tests. The 44 baseline failures should then fall. | L1 |
| OQ-5 | Names and defaults of the switch. Name: `boc61` / `wideband`? Default per signal? File tag? | Name `boc61` (True = include). Default `False` for B1C (as D-062) and `True` for L1C (as the code is today). Tag `_wb` / `_nb`. | T0 |
| OQ-6 | L1C narrow-band replica: gate the 4/33 BOC(6,1) chips (replica = 0 there) or keep BOC(1,1) on every chip? | Gate them. It removes noise from unmatched chips and matches the physical meaning ("BOC(6,1) off"). Test both in synthetic data (cheap). | T3 |
| OQ-7 | Wide-band B1C: choice A (3 components) or B (coherent composite)? Policy per variant: one key or two? | A first. Two keys (`"BDS_B1C"`, `"BDS_B1C_WB"`) if the policy differs; otherwise one policy with options. | T0, T1 |
| OQ-8 | CNAV-2 scope: decode subframe 2 (ephemeris, ITOW, health) now; subframe 3 pages (iono, UTC, almanac) later? | Yes. Subframe 3: parse page types that give health and the Klobuchar terms only if the ICD layout is simple; else defer. | L4 |
| OQ-9 | L1C LDPC: write the full decoder from the start (as for B-CNAV2, D-051) or a systematic shortcut with the CRC first? | Full decoder with encoder for tests; the ICD gives the matrices. A shortcut is not safe for a rate-1/2 code at low SNR. | L4 |
| OQ-10 | Galileo ionosphere (NeQuick G needs a model) and group delay (BGD E1/E5a)? | Group delay: yes (it is in the message). Ionosphere: **none** first, as for BeiDou (D-053); show its size in the notebook. | E5 |
| OQ-11 | Galileo satellite id and PRN range: `E01` to `E50`? signal id `"GAL_E5A"`, class `GalE5a`? | Yes. Check the PRN range in the ICD (codes exist for 50 PRNs **[ICD]**). | E1 |
| OQ-12 | Recording length for F/NAV. A full ephemeris needs pages 1 to 4, and a subframe is about 50 s **[ICD]**. The 120 s recordings may be marginal. | The user records at least 300 s of L5 data (see 6.4). Plan for 150 s minimum tracking. | E4 |
| OQ-13 | Combined fix: GPS L5 + B2a + E5a on one grid (one ISB per extra system), and GPS L1C/L1 C/A + B1C? | Yes, after the single-signal fixes work. Needs `GST` in `time_systems.py`. | E6 |
| OQ-14 | The position-error limit of OQ-12 of the BeiDou plan (D-050) still open. | The user sets it. No phase may claim a position match until then. | all |

---

## 5. Work plan

Phases are in this order: **L0, L1, L2, T0 to T2 (B1C wide-band first, it is the smallest
change), T3, T4, T5, L3, L4, L5, E0 to E6, F (hand-over)**. L and E phases are independent
after L0. The T phases need L2 for L1C. The user may re-order.

### Phase L0: Set-up, baseline and decisions

1. Read the root `CLAUDE.md`, this plan, `gnss_processing/README.md`, `docs/architecture.md`,
   `docs/BDS_PORT_PLAN.md` sections 3 and 11, and the BeiDou decisions D-044 to D-066.
2. Check that the two ICDs are in `docs/icd/`. If not, stop and ask. Extract their text
   (`pdftotext -layout`) into the scratchpad, not into the repository.
3. Create the branch (OQ-0). Run the whole suite. Save the result as the baseline
   (`docs/L1C_E5A_BASELINE_TESTS.md`, same format as `BDS_BASELINE_TESTS.md`). The expected
   result is **44 failed, 3 errors** (all L1C related).
4. Ask OQ-0, OQ-1, OQ-2, OQ-3, OQ-4.
5. Read the ICDs for the **[ICD]** items of this plan. Make a table "plan said / ICD says" in the
   scratchpad. Report every difference to the user before Phase L1 starts.

Done when: baseline saved, OQ-0 to OQ-4 answered and recorded, ICD differences reported.

### Phase L1: GPS L1C generators (`utils/gps_l1c.py`)

Goal: the 44 baseline failures that come from the missing module disappear.

Files: new `utils/gps_l1c.py`; edit `utils/signal_interfaces.py` (import), `tests/test_l1c_codes.py`,
`tests/test_l1c.py` and the other test files that import `gnss_tools.signals.gps_l1c` (import
line only, OQ-4); `tests/synthetic.py` (import only).

1. Implement the API listed in 2.1 (and the extra names the tests use: `NUM_PRNS`, `CODE_LENGTH`,
   `OVERLAY_LENGTH`, `OVERLAY_RATE`, `DATA_SYMBOL_RATE`, `EXPANSION_SEQUENCE`,
   `L1CP_WEIL_INDEX`, `L1CP_INSERTION_INDEX`, `generate_legendre_sequence`,
   `generate_weil_code_sequence`, `generate_code_sequence_L1CP`) **from the ICD**. Use
   `grep -rhn "gps_l1c\." tests utils` to get the exact list. Keep tables as data (a JSON in
   `docs/icd/` or tuples at the top of the module, as `bds_b2a.py` does), not inside functions.
2. The pilot Weil code in `gnss-tools` can be a cross-check. Do not import it.
3. L1CD: LFSR-based data code (Weil-code construction) **[ICD]**. L1CO overlay: the 1800-bit
   overlay codes for each PRN **[ICD]**. Both need the tables of IS-GPS-800 (210 PRNs: 63 GPS +
   non-standard codes).
4. Run `tests/test_l1c_codes.py`. All 630 code checks (420 ranging + 210 overlay) must pass with
   the checksum JSON that is already in `tests/data/`.
5. Point `signal_interfaces.py` at `utils.gps_l1c`. Remove the `try/except` that sets `GpsL1C = None`.
   Keep a clear error for a bad import (no silent `None`).
6. Run the L1C test files. Fix real defects. Do **not** loosen a tolerance without a reason.
   If a golden file differs, find out why (see Phase L2).

Done when: `test_l1c_codes.py` passes completely; `GpsL1C` is a class; the collection errors are
gone; the L1C-related baseline failures are explained one by one (fixed, or listed with a cause).

### Phase L2: GPS L1C signal, policies and regression

Files: `utils/signal_interfaces.py`, `tests/test_l1c.py`, `tests/golden/l1c_*.npz`, notebook 01 text.

1. With the module in place, run `test_l1c.py`, `test_double_estimator.py`,
   `test_tracking_regression.py`, `test_coherent_integration.py`, `test_acquisition.py`.
2. For each remaining failure decide: code defect, test defect, or float-level drift of a golden
   file (the baseline had 1e-9 drift failures for other signals, so expect some). **Ask the user
   before you regenerate any golden file.** Give the size of the difference.
3. Check the L1C policy against the ICD: the L1CD/L1CP power split (1/4, 3/4), BOC(1,1) data,
   TMBOC pilot, overlay on the pilot only, the data symbol (10 ms, 100 sps), the coherent
   ceiling (10 ms).
4. Check that notebook 01 runs for `SIGNAL_ID = GpsL1C` on synthetic data (use the test
   fixtures) and add `GpsL1C` to the notebook text where B1C was added.
5. Update `docs/BDS_BASELINE_TESTS.md`-style baseline file: **new baseline v2** (the failures
   that remain, expected: the golden drift of other signals).

Done when: the L1C test suite passes (or each failure is explained and accepted by the user);
baseline v2 is saved; the old non-L1C results are unchanged.

### Phases T0 to T5: the BOC(6,1) toggle

#### Phase T0: Design confirmation

1. Show the user section 3. Ask OQ-5 and OQ-7 (and OQ-6 for L1C). Record them.
2. Read the correlator code for subcarriers (`utils/bpsk_correlation.py`,
   `utils/code_components.py`) and write down how a third component or a gated TMBOC pattern
   maps to it. Check `tests/test_subcarrier.py` for what is covered.
3. Compute from the ICD the exact BOC(6,1) and BOC(1,1) power, subcarrier rates and the
   pilot overlay relation. Compare with section 3.1.

Done when: the design (option object, policy keys, file tag) is written in `DECISIONS.md`.

#### Phase T1: Options plumbing (no behaviour change)

Files: `utils/signal_interfaces.py`, `utils/code_components.py` (only if needed),
`utils/tracking_io.py`, notebook 01 config cell, tests.

1. Add `SignalOptions` and pass it through `Signal`, `build_signals`, acquisition and
   tracking builders. The default must give **exactly today's behaviour**.
2. Store the option in the tracking file and show it in `load_tracking_results`. Old files (no
   option) load as the signal's default. Test this.
3. Add the file-name tag (OQ-5). Update `tracking_io.tracking_path` users. Search the repository
   for every user (`grep`) and update them in the same change.
4. Tests: default behaviour unchanged for all signals (the full suite must be as baseline v2);
   option round trip through the `.h5` file; a clear error for an option that has no meaning.

Done when: full suite equals baseline v2; the new tests pass.

#### Phase T2: B1C wide-band (QMBOC)

Files: `utils/signal_interfaces.py`, `tests/test_b1c.py`, `tests/synthetic.py` (already has
`include_boc61`).

1. Add the `B1CP61` component when `boc61` is on (choice A of 3.4). Pilot code and overlay are
   the same arrays as `B1CP`.
2. Add the tracking policy for the variant: carrier on `B1CP`, delay loop on all components
   with `power_weight` (data 1/4, pilot 29/44, BOC(6,1) 1/11). Keep the 10 ms ceiling.
3. Synthetic tests (use `generate_b1c_samples(include_boc61=True)` at >= 25 Msps):
   - the aligned correlation shows the BOC(6,1) component at the right amplitude (1/11 power);
   - wide-band tracking locks, the overlay syncs, the code phase is unbiased;
   - **a narrow-band channel on a signal that contains the BOC(6,1) part** still locks (the test
     of D-062 exists); a wide-band channel on a narrow-band-only signal does not break.
4. Mutation checks (break the weight, break the branch, confirm the test fails).
5. If time allows: prototype choice B behind the same option and compare (Phase T5).

Done when: tests pass; wide-band and narrow-band B1C both track the synthetic signal; the old
narrow-band results are bit-identical to before.

#### Phase T3: L1C narrow-band (TMBOC off)

Files: `utils/signal_interfaces.py`, `utils/code_components.py` (if gating needs a new subcarrier
kind or a pattern with a "zero" value), `tests/test_l1c.py`, `tests/test_subcarrier.py`.

1. With `boc61` off, build the pilot as BOC(1,1) on all chips (mismatched variant) or with the
   BOC(6,1) chips gated to zero (OQ-6). Support what the user chooses. Test both on synthetic
   data and report the difference in a table.
2. The double estimator uses the BOC(6,1) slope today. With `boc61` off the subcarrier loop has
   no steep BOC(6,1) slope. **Decide** (ask): turn the double estimator off, or run it on the
   BOC(1,1) subcarrier with its own spacing. Document the choice.
3. Tests: narrow-band L1C locks on a TMBOC synthetic signal; amplitude of the pilot prompt is
   about 29/33 of the wide-band prompt for the gated variant; the wide-band default is unchanged
   (golden files).

Done when: tests pass; the L1C wide-band default is unchanged (goldens equal).

#### Phase T4: Notebook and tool support

1. Notebook 01: the `BOC61_ENABLED` variable; the sample-rate guard (3.3 item 6, limit from config);
   text that explains the two variants and the cost. Notebook 00: add a line to the spectrum
   check that shows whether the BOC(6,1) lobes are visible (a simple measure: power at +/-6.138
   MHz against the noise floor).
2. Notebook 02: print the variant. Make sure a narrow-band and a wide-band file for the same
   collect can both be loaded (the file tag).
3. `scripts/run_notebook.py` and the skill: the new `--set BOC61_ENABLED=...` example.

Done when: both variants run end to end in the notebooks on synthetic data (use the headless
runner) and the guard works.

#### Phase T5: Comparison tools

Files: new `scripts/compare_boc61.py`, new `utils/boc61_metrics.py`, tests.

1. **Metrics** (all computed from tracking files, same collect, same start offset):
   - mean C/N0 of each channel, difference per satellite (dB-Hz);
   - code-minus-carrier standard deviation per satellite (needs the carrier-phase range; the
     tracker has carrier phase and code phase; remove the ionosphere-divergence trend with a
     low-order polynomial);
   - post-fit residual rms and position scatter from notebook 02 (same ephemerides);
   - number of lock losses (supervisor events) and the time to PLL lock;
   - run time.
2. **Synthetic Monte Carlo.** Sweep C/N0 (for example 30 to 50 dB-Hz). For B1C and L1C, run
   both variants on the same noise. Plot the code-phase error std against C/N0 for both
   variants. Compare with the theoretical Cramer-Rao bound (written in the report, with the
   formulas) as a sanity check. Keep run times small (seeded, short).
3. **Real data A/B.** The script takes two tracking files and prints and plots the metrics.
   The user runs notebook 01 twice (switch off, switch on) on the same recording on their
   machine, then runs the script.
4. A short section in the PDF report (or a new report) with the figures.

Done when: the synthetic study runs in the cloud session and gives a figure; the real-data script
is tested on two synthetic tracking files; the user has instructions to run it on real data.

### Phase L3: L1C on real L1 data (user machine)

The user runs. Claude prepares and reads the results.

1. Claude gives exact commands. The user runs notebook 00 (spectrum and bandwidth check), then notebook
   01 with `SIGNAL_ID = GpsL1C` (both variants after T4), on the L1 stream of the validation recording.
2. The user sends: the printed tables, the C/N0 plot, and the tracking file or its summary.
3. Check: acquired PRNs against GPS L1 C/A of the same recording (the same satellites should
   appear), C/N0 near the L1 C/A level + the pilot gain, no systematic code-phase offset between
   L1C and L1 C/A pseudoranges (a few metres at most is a finding to explain).

Done when: L1C tracks on real data and the results are reported. Findings go into `DECISIONS.md`.

### Phase L4: CNAV-2 decoding (L1C message)

Files: `utils/nav/cnav2.py`, new `utils/nav/ldpc_binary.py`, new `docs/icd/gps_l1c_ldpc_tables.json`,
`utils/nav/primitives.py` (only if needed), `utils/nav/ephemeris.py`, `utils/observables.py`,
new `tests/test_nav_cnav2_ldpc.py`, edits in `tests/test_nav_cnav2.py`.

1. **Binary LDPC** for subframe 2 (LDPC(1200,600)) and subframe 3 (LDPC(548,274)), from the
   prototype matrices in the ICD. Write: matrix construction (check the ICD's lifting or
   permutation rule), a systematic **encoder** (for tests), and a soft decoder (normalised
   min-sum or sum-product). Test with the ICD vectors if the ICD gives any (ask); else test
   with random words, noise, and the matrix rank and syndrome.
2. **Frame handling.** `cnav2.py` already finds the frame start from the overlay and
   de-interleaves (38 x 46). Add the LDPC decode of both subframes, the CRC-24Q of each, and
   the polarity (resolved by the BCH(51,8) TOI field of subframe 1; check how).
3. **Subframe 2 parser** (ITOW, top, URA/health bits, clock, ephemeris, ISC/TGD) **[ICD:
   Figure 3.5-x]**. Reuse `CnavEphemeris` and the CNAV field layout from `nav/cnav.py` when the
   ICD says the fields are the same; **compare field by field**; do not assume.
4. **Time.** `TOW = ITOW x 7200 + TOI x 18`. TOI refers to the start of the **next** frame
   (existing text in `cnav2.py`). The anchor must use the right frame start (a test, as for
   B-CNAV1: synthetic signal, anchor code phase).
5. **Subframe 3 pages**: parse what OQ-8 says (health, Klobuchar from the page that has it).
6. `observables.anchor_from_cnav2`, `multi_signal.decode_cnav2_run` (or generalise
   `decode_beidou_run` into a `decode_run(signal_type_id)`; ask).
7. Tests: encoder/decoder round trip; noisy frames; wrong CRC; frame search; parsers with
   field positions copied by hand from the ICD figures; a full synthetic chain
   (`generate_l1c_samples` with a real encoded frame, tracker, symbols, decode); mutation
   checks.

Done when: a synthetic L1C signal gives a decoded ephemeris and a correct time anchor; the
suite equals baseline v2 plus the new passing tests.

### Phase L5: Notebook 02 for L1C and a position fix

1. Remove the `NotImplementedError` for `CNAV-2` in notebook 02. Add the CNAV-2 branch:
   `MESSAGE_FORMAT["GPS_L1C"] = "CNAV-2"`, anchors from `anchor_from_cnav2`, **the ephemeris from
   the signal** (no RINEX) or from RINEX for a comparison (both: decoded vs broadcast file),
   the minimum tracking time (a frame is 18 s; allow 40 s or more).
2. GPS ionosphere (Klobuchar) from subframe 3 if parsed (OQ-8), else from the RINEX header.
3. The fix on a real L1 collect (user). Compare it with the L1 C/A fix and the B1C fix of the
   same recording: all three should agree within the error you expect (record the numbers).
4. Combined fix with GPS L1 C/A and B1C if the notebook flow allows (section 10 of notebook 02
   accepts `GpsL5` and BeiDou signals today; extend the dispatch).

Done when: notebook 02 gives an L1C-only fix on a real L1 collect; the three-signal comparison
is written in `DECISIONS.md`.

### Phase E0: Galileo E5a ICD analysis

1. Read the ICD. Make a table of facts with the page numbers: carrier, chip rate, primary code
   construction (two registers, polynomials, start values), PRN range, secondary codes (E5a-I
   `CS20`, E5a-Q `CS100_n`), data rate and symbol rate, power split, the F/NAV page and
   frame structure (sync pattern, convolutional code, interleaver, CRC), the page types and
   which carry the ephemeris, clock, BGD, GST and week, the Galileo orbit algorithm and
   constants, the clock and group delay formulas for E5a-only users, the time scale GST.
2. Compare with the **[ICD]** items of this plan. Report differences.
3. Ask OQ-10, OQ-11, OQ-12.

Done when: the facts table is saved in `docs/` (short, STE) and the user has seen it.

### Phase E1: E5a codes (`utils/gal_e5a.py`)

Files: new `utils/gal_e5a.py`, `docs/icd/gal_e5a_code_tables.json`, new `tests/test_e5a_codes.py`.

1. Constants. Primary code generators for E5a-I and E5a-Q (per-PRN start values) from the ICD
   tables, as data. Secondary codes: the fixed 20-chip code for I and the 100-chip codes for Q
   (per PRN) **[ICD]**. Signs: logic 0 to +1, logic 1 to -1 (check the ICD).
2. Extract the tables from the PDF text by script, then **verify**: the ICD prints first chips
   (hex or octal) for each PRN **[ICD]**. The test repeats this for every PRN (as
   `test_b1c_codes.py`). A slow independent implementation of the register is a second check.
3. Property tests: length, balance, auto and cross correlation bounds measured on the real codes.

Done when: all PRNs match the ICD tables; property tests pass.

### Phase E2: `GalE5a` signal, policies, tables

Files: `utils/signal_interfaces.py`, `utils/nav/symbols.py`, `utils/observables.py`,
`tests/synthetic.py`, new `tests/test_e5a.py`, notebook 00 and 01 text, notebook 01 dicts.

1. `GalE5a(Signal)`: `signal_type_id = "GAL_E5A"`, `link = LINKS[LinkId.L5]`, carrier
   1176.45e6, chip rate 10.23e6, `primary_period_ms = 1`, `sat_id_prefix = "E"`. Components:
   `E5aI` (data, `Branch.I`, overlay CS20, `symbol_period_ms = 20`) and `E5aQ` (pilot,
   `Branch.Q`, overlay CS100). **Confirm** the symbol period and which secondary code is on
   which component in the ICD.
2. Policies: copy `GpsL5` and `BdsB2a`, change only what the ICD changes. Acquire on the pilot;
   do not fold the 100 ms overlay (as B2a); carrier on the pilot; Costas until the overlay locks;
   overlay search by brute force (100 offsets) with a window that fits the policy.
   The 20 ms data symbol sets the coherent ceiling (20 ms).
3. `nav/symbols.py`: symbol period 20 ms, data component 0, `HAS_OVERLAY`, `NEEDS_BIT_SYNC =
   False`, `DATA_IN_QUADRATURE = True` if the carrier runs on the pilot.
4. **Symbol grid.** E5a acquires modulo 1 ms like B2a. The D-059 fix ties epochs to the
   overlay counter. A test must slide the symbol phase 0 to 19 ms. (The test of B2a slides
   0 to 4 ms.) Check that `_symbol_overlay_chips` works for a 20 ms symbol over 1 ms periods.
5. Band frequency table in `observables.py` (E5a at 1176.45 MHz).
6. Synthetic tests: structure, acquisition, overlay sync, tracking, symbol extraction, mutation
   checks. A synthetic generator `generate_e5a_samples`.
7. Notebook 01: add `GalE5a` to imports, `SIGNAL_ID` comment, the per-signal dicts, the band
   table (L5 row). Notebook 00: text only.

Done when: all new tests pass; the rest of the suite equals baseline v2.

### Phase E3: E5a on real L5-band data (user machine)

1. The user runs notebook 01 with `SIGNAL_ID = GalE5a` on the L5-band channel of the validation
   recording (which also holds GPS L5 and B2a signals). Sample rate must be at least about 20 Msps.
2. Check: the acquired satellites are Galileo satellites that are above the horizon (compare
   with a predicted sky from a broadcast almanac if available; else with the C/N0 and Doppler
   spread). E5a PRNs overlap with B2a and L5 PRN numbers: the satellite id letter must keep
   them apart (`E`, `C`, `G`).
3. Report C/N0 and tracking quality.

Done when: notebook 01 runs end to end for `GalE5a` on one real collect; the tracking file is saved.

### Phase E4: F/NAV decoder (`utils/nav/fnav.py`)

Files: new `utils/nav/fnav.py`, `utils/nav/primitives.py` (reuse: convolutional encoder, Viterbi if
present, CRC-24Q), new `tests/test_nav_fnav.py`.

1. **Page structure [ICD]:** sync pattern, the 238 or 244 data bits, CRC-24Q, tail bits,
   rate-1/2 convolutional code (constraint length 7), block interleaver. Write the layout as data
   and assert that the sums match the page length.
2. **Decoder.** Frame (page) sync by the sync pattern (both polarities, and the Viterbi
   decoder gives polarity); de-interleave; Viterbi (soft) decode; CRC check; accept only with a
   good CRC. Check `primitives.py` for an existing soft Viterbi (CNAV uses a rate-1/2 K=7 code);
   reuse it **only if** the polynomials and the output order match the Galileo ones (the
   Galileo code inverts the second output **[ICD]**; test it).
3. **Encoder** for tests (page to symbols).
4. **Parsers.** Page types 1 to 4 (clock and SISA and ionosphere flags; ephemeris parts; BGD;
   GST-UTC), page 0 and others as "other". Scales and signs from the ICD tables, checked
   against the page images (as for B-CNAV2).
5. **Ephemeris assembly.** Pair pages by IODnav (the ICD says how). Rule for mixing pages from
   two issues: ask the user (as D-055 for B-CNAV2).
6. **Time.** GST: week number and TOW in page 1 or 2 **[ICD]**. The TOW of a page names the
   page start or end: check and test with a synthetic chain.
7. Tests as for B-CNAV2: geometry, encode/decode, noise, polarity, bad CRC, frame offset,
   parser fields from the ICD figures, anchor with the synthetic tracker.

Done when: a synthetic E5a signal gives a decoded ephemeris and a correct anchor.

### Phase E5: Galileo ephemeris, time and corrections

Files: `utils/nav/ephemeris.py` (new `GalEphemeris`, or a generalised Kepler class), `utils/time_systems.py`
(GST), `utils/observables.py`, `utils/navigation.py`, tests.

1. `GalEphemeris`: Kepler model with Galileo constants **[ICD]** (mu, Omega-dot-e, F), clock
   polynomial, relativistic term, **BGD(E1,E5a)** for the E5a single-frequency user: apply the
   ICD formula (sign and scale factor; the E5a clock is referenced to the E1/E5b combination).
2. `time_systems.py`: GST (week starts 1999-08-22; GST and GPST differ by a constant offset
   and a week-number offset **[ICD]**; the GGTO term is small, treat as zero first, record it).
   `convert_time_of_week` gets `"GST"`.
3. Solver: system letter `E`; ISB for `E`; `BAND_FREQ_HZ["GAL_E5A"]`; group delay path in
   `signal_group_delay_s`; no ionosphere for `E` (OQ-10), same as BeiDou.
4. Tests as in `test_navigation_multisystem.py`: truth world with E satellites, recover
   position, clock and two ISBs; wrong time scale and wrong BGD sign must show in residuals.
   Orbit test: compare with an independent computation (and with RINEX 3 `E` records if CDDIS
   is reachable from the user machine; extend `rinex3_nav.py` and `bds_orbit_check.py`).

Done when: truth-world tests pass for G + C + E.

### Phase E6: Notebook 02 for E5a and combined fixes

1. Notebook 02 branch `MESSAGE_FORMAT["GAL_E5A"] = "F/NAV"`, anchors, minimum tracking time
   (OQ-12), `multi_signal` decode, fix on a real recording.
2. Combined run on the L5 band: GPS L5 + BeiDou B2a + Galileo E5a, one grid, two ISBs. Extend the
   section 10 dispatch.
3. The user runs on a longer recording; Claude reads the results.

Done when: notebook 02 gives an E5a-only fix on real data; the combined fix is reported.

### Phase F: Hand-over

1. `scripts/run_notebook.py` (examples for `GpsL1C`, `GalE5a`, `BOC61_ENABLED`), the
   `run-gnss-notebooks` skill, `gnss_processing/README.md` (HERON edits list, quick start),
   `docs/architecture.md` (new modules), `docs/DECISIONS.md` (every answer), the follow-up list
   (E1 CBOC, E5b/AltBOC, I/NAV, NeQuick G, GGTO, composite QMBOC replica, batch tool).
2. Extend the PDF report (`docs/bds_report/`): L1C, E5a, the BOC(6,1) comparison.
3. Update the plan checklist (section 8).

Done when: a new engineer can run B1C, B2a, L1C and E5a through notebooks 00 to 02 from the
README alone.

---

## 6. Testing strategy

### 6.1 Rules

- Every new function has a test. A test that cannot fail is not a test: break the code on purpose
  and see the test fail (mutation check), as in the BeiDou port.
- Field positions in parser tests are copied by hand from the ICD figures, **not** from the module.
- Golden vectors from the ICDs: L1C check sums (exists), E5a first chips, F/NAV and CNAV-2
  encoding examples if the ICD has them.
- Use synthetic signals with a known truth first, real data second.

### 6.2 Test map

| Part | Tests |
| --- | --- |
| Codes | ICD tables for all PRNs; slow independent generator; correlation bounds |
| Signal and policies | structure through the correlator; acquisition; overlay; tracking; symbol-grid sweep; mutation |
| BOC(6,1) switch | default unchanged (goldens); both variants lock; narrow-band on a wide signal; wide-band on a narrow signal; file round trip |
| CNAV-2 | LDPC encoder/decoder; frame search; CRC; parsers; synthetic chain |
| F/NAV | Viterbi, interleaver, CRC; page sync; parsers; synthetic chain |
| Galileo orbit and time | truth world; time-scale and BGD errors visible |
| Comparison | metrics on known synthetic differences |

### 6.3 Baselines

- Baseline v1 (2026-10-14): 44 failed, 3 errors (`docs/BDS_BASELINE_TESTS.md`). Almost all are L1C.
- Baseline v2 (new, saved in Phase L2): what is left after the L1C module exists. Compare
  later phases with v2.
- A **fixed** failure is good news but must be explained. Never "fix" a test by changing its
  expected value without a reason written in the commit message.

### 6.4 Real-data runs (user machine)

| Run | Needs | Purpose | Phase |
| --- | --- | --- | --- |
| Notebook 00 spectrum on the L1 channel | recording with sample rate >= about 20 Msps (check) | Is the BOC(6,1) part inside the recorded band? | T4, L3 |
| Notebook 01 `GpsL1C`, `BOC61_ENABLED` off and on | L1 stream, 120 s | C/N0, CMC, lock; A/B | L3, T5 |
| Notebook 01 `BdsB1C`, off and on | same stream | A/B; also finishes the B1C real-data check (D-063) | T5 |
| Notebook 01 `GalE5a` | L5 stream, **>= 150 s, better 300 s** | tracking; F/NAV needs long data | E3, E6 |
| Notebook 02 L1C, B1C, E5a fixes | the tracking files | fixes and comparison | L5, E6 |

For every run, the user sends: the printed tables, the figures, and the tracking file (or its
summary if the file is large). Do not send `.env`.

## 7. Risks

| Risk | Effect | Mitigation |
| --- | --- | --- |
| L1C code tables typed wrong | Codes never acquire | Use the checksum JSON (exists); test all 630 codes. |
| Golden L1C files were made with another code version | False failures | Compare, explain, ask before any regeneration. |
| Recording bandwidth too low for BOC(6,1) | Wide-band gives no gain, or hurts | Spectrum check in notebook 00; sample-rate guard; report "not testable on this recording". |
| Wide-band replica is only non-coherent (choice A) | Gain smaller than the full 0.4 dB | Measure first; do choice B only if worth it. |
| 120 s recordings too short for F/NAV | No E5a ephemeris | Ask for >= 300 s recordings; or use RINEX ephemeris as a fallback for tests (flag it). |
| CNAV-2 LDPC matrices have a lifting rule that is easy to misread | Decoder never converges | Test the code with the encoder and the syndrome; compare with any ICD vector. |
| Galileo clock reference and BGD sign | Metres of range bias | A truth-world test, plus a real-data check against the other systems (ISB must be stable). |
| E5a and B2a and GPS L5 share one band, PRN numbers overlap | Mixed-up satellites | The satellite id letter (`E`, `C`, `G`); tests for the id. |
| The BeiDou open items (Q-019, B1C real run, D-059 re-tracking) are not closed | Confusion between old and new problems | Keep them on the follow-up list; do not mix commits. |
| Changing the `try/except` around `GpsL1C` changes `import utils` behaviour | Import errors for others | Test import on a clean checkout; keep a clear message. |

## 8. Checklist

- [ ] L0: baseline, OQ-0 to OQ-4, ICD differences reported
- [ ] L1: `utils/gps_l1c.py`, `test_l1c_codes.py` green
- [ ] L2: L1C suite green or explained, baseline v2
- [ ] T0: toggle design recorded (OQ-5, OQ-6, OQ-7)
- [ ] T1: options plumbing, behaviour unchanged
- [ ] T2: B1C wide-band
- [ ] T3: L1C narrow-band
- [ ] T4: notebook and runner support, sample-rate guard
- [ ] T5: comparison tools and synthetic study
- [ ] L3: L1C real data (user)
- [ ] L4: CNAV-2 LDPC and parsers
- [ ] L5: notebook 02 L1C fix
- [ ] E0: Galileo ICD facts table
- [ ] E1: E5a codes
- [ ] E2: `GalE5a` signal and tests
- [ ] E3: E5a real data (user)
- [ ] E4: F/NAV decoder
- [ ] E5: Galileo ephemeris, GST, corrections
- [ ] E6: notebook 02 E5a fix and combined fix
- [ ] F: hand-over documents
- [ ] `docs/DECISIONS.md` holds every answer

## 9. Reference: where the existing code is

| Topic | File |
| --- | --- |
| Signal classes, policies | `utils/signal_interfaces.py` |
| Code components, subcarriers, TMBOC | `utils/code_components.py`, `tests/test_subcarrier.py` |
| Correlator | `utils/bpsk_correlation.py` |
| Tracking loops, double estimator | `utils/tracking_channel.py`, `tests/test_double_estimator.py` |
| Symbols and tables | `utils/nav/symbols.py` |
| CNAV-2 (partial) | `utils/nav/cnav2.py`, `tests/test_nav_cnav2.py` |
| CNAV, ephemeris | `utils/nav/cnav.py`, `utils/nav/ephemeris.py` |
| Convolutional code, CRC, BCH | `utils/nav/primitives.py` |
| BeiDou model to copy | `utils/bds_b1c.py`, `utils/bds_b2a.py`, `utils/nav/bcnav1.py`, `utils/nav/bcnav2.py`, `utils/multi_signal.py` |
| Position solver | `utils/navigation.py`, `utils/observables.py`, `utils/time_systems.py` |
| L1C tests, goldens, ICD check sums | `tests/test_l1c*.py`, `tests/golden/l1c_*.npz`, `tests/data/is_gps_800j_l1c_checksums.json` |
| BeiDou plan, decisions, baseline | `docs/BDS_PORT_PLAN.md`, `docs/DECISIONS.md`, `docs/BDS_BASELINE_TESTS.md` |
