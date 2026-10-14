# BeiDou B2a and B1C Port Plan — MATLAB receiver to `gnss_processing`

Status: plan only. No code is changed by this document.
Written: 2026-10-14. Updated 2026-10-14 (B1C added; B2a ICD read; open-service
B1C ICD read, B1C code tables verified).
Decision records: D-044, D-045 and D-046 in `docs/DECISIONS.md`.

This document lets a new Claude Code session do the work with no other
context. Read section 0 first. Then do the phases in order.

---

## 0. How to use this document (read first)

You are the test engineer on HERON. The rules in the root `CLAUDE.md`
apply to all work here. The five that matter most for this task:

1. **Do not assume. Do not decide.** If a value, an interface or a design
   choice is not in `docs/`, in this plan, or in an ICD in `docs/icd/`,
   stop and ask the user. Section 5 lists the open questions. Ask them in
   chat. Record each answer in `docs/DECISIONS.md` as the next free
   `D-xxx` (the last one used was D-046).
2. Write all text in ASD-STE100 Simplified Technical English. This covers
   comments, docstrings, commit messages and documents.
3. Give every module, class and function a docstring. Explain why.
4. Do not hard-code tunable values. Put them in config (a notebook
   config cell, a dataclass default, or a YAML/TOML file).
5. Write tests with the code. Keep the existing test suite green.

Order of work:

1. Read sections 1 to 4 (scope, source code, target code, mapping).
2. Read section 5 (open questions) and section 11 (ICD facts).
3. Do Phase 0 (section 6). It ends with questions to the user.
4. Do Phases 1 to 9 in order. Each phase ends with a "Done when" test.
5. Commit once per phase. Never commit raw data, large binaries or
   `.env` (it holds Earthdata credentials).

Work in `gnss_processing/` only. Do **not** edit `gps-tracking-example/`
(read-only submodule) or `SURGE/`. Do **not** edit
`gnss_processing/submodules/gnss-tools` (pinned submodule; HERON cannot
push to it). Put new code in `gnss_processing/utils/` instead (D-044).

---

## 1. Goal and scope

**Goal.** Add the two BeiDou-3 open-service signals **B2a** and **B1C** to
the Python GNSS tools in `gnss_processing/`, using the user's MATLAB
receivers as the reference. The result runs through notebooks 00, 01 and 02
and gives:

- a position fix from B2a alone and from B1C alone;
- a combined fix from GPS L5 + B2a (same carrier, one recording);
- optionally, a dual-frequency B1C + B2a fix.

**Why it matters.** B2a and B1C are in the HERON signal plan (D-039). B2a,
GPS L5 and Galileo E5a share 1176.45 MHz. B1C, GPS L1 C/A, GPS L1C and
Galileo E1 share 1575.42 MHz. One recording per band therefore holds
several signals. The same recordings can test the port.

**In scope**

- B2a and B1C ranging codes (data, pilot, secondary codes), signal
  definitions, acquisition policies and tracking policies.
- The B-CNAV2 (B2a) and B-CNAV1 (B1C) navigation messages.
- The BeiDou orbit, clock and time (BDT) models.
- Position fix: single signal, GPS L5 + B2a, optional B1C + B2a.
- Changes to notebooks 00, 01 and 02 and to the files they call.
- Tests for all of the above.

**Out of scope for this plan** (record as follow-up work)

- Re-porting GPS L5. Python already has it (`GpsL5`). Only check it again.
- BDS B1I and B3I; Galileo E1 and E5a (D-039 lists E1 and E5a for later).
- The BDSBAS-B1C signal (SBAS service from three GEO satellites). The user
  chose the open-service B1C on 2026-10-14 (D-045).
- Any change to `Specular_Planner/`, `HERON_DEPLOY_SOFTWARE/` or hardware.
- Speed work on the correlator.

---

## 2. Source: the MATLAB code

### 2.1 Where it is

| Path (under `D:\06_Prior_Work\MATLAB_CODE\6091\`) | What it is |
| --- | --- |
| `CU-SDR-Collection-main/BDS/B2a/` | Original CU B2a receiver. Serial. |
| `CU-SDR-Collection-main/BDS/B1C/` | Original CU **open-service B1C** receiver. Has narrow-band (NB) and wide-band (WB) tracking. **Main B1C source.** |
| `Project/ASEN6091_Project/B2a/` | Class-project B2a receiver. Adds `parfor`, progress plot, WLS weights, satellite down-select. **Main B2a source.** |
| `Project/ASEN6091_Project/GPS_L5C/` | Class-project GPS L5 receiver (same structure). |
| `Project/ASEN6091_Project/BD2a_GPSL5/` | Combined B2a + L5 receiver. Tracks each signal apart; one fix. |
| `Project/ASEN6091_Project/README.md` | List of the project changes. |
| `.../B2a/*.mat`, `.../BD2a_GPSL5/*.mat`, `.../BDS/B1C/*.mat` | Saved results (`acqResults`, `trkResults`, `navResults`, which also holds `eph`). **Use as golden references.** Python needs `scipy` to read them (the venv in `gps-tracking-example/.venv` has it). |
| `.../B2a/*.rnx` | RINEX 3 BeiDou nav (`..._CN.rnx`) and observation (`..._MO.rnx`) files. Use as truth. |

The class data is **not** on this machine: `test_L5_*.bin` (B2a, 18 Msps,
8-bit I/Q, IF 20 kHz) and `GNSSL1testSet-fs8_00-if0_02.bin` (B1C, 8 Msps,
IF 20 kHz). The project README links a Google Drive folder. See OQ-5.

### 2.2 MATLAB data flow (same for B2a, L5 and B1C)

```
init.m -> initSettings.m          settings struct (all constants)
  postProcessing.m
    fopen(file), fseek            8-bit interleaved I,Q file
    acquisition.m                 acqResults: carrFreq, codePhase, peakMetric (per PRN)
    preRun.m                      channel(): PRN, acquiredFreq, codePhase, codeFreq
    tracking.m (B1C: NB_tracking.m or WB_tracking.m)
                                  trkResults(): I/Q E,P,L, pilot I/Q, NCO values, C/N0
    postNavigation.m              decode nav, pseudoranges, WLS fix
      BCNAV2decoding.m / BCNAV1decoding.m     frame search, CRC, ephemeris()
      ephemeris.m                 nav message -> eph struct
      calculatePseudoranges.m, satpos(B2a).m, leastSquarePos.m
    plotAcquisition / plotNavigation / plotNavNew / skyPlot
Automate_*.m                      batch loop; puts each file in category 1-4
```

### 2.3 MATLAB algorithms and values (B2a)

| Item | MATLAB value or method | Source file |
| --- | --- | --- |
| Sample file | 8-bit signed I/Q, `fs = 18e6`, `IF = 20e3` | `initSettings.m` |
| Carrier / chip rate / length | 1176.45e6 Hz / 10.23e6 / 10230 chips (1 ms) | `initSettings.m` |
| PRN list | `1:62` (the ICD defines 1–63) | `initSettings.m` |
| Data code | Two 13-stage LFSRs. Reg 1 taps `[1 5 11 13]`, start all ones. Reg 2 taps `[3 5 9 11 12 13]`, start from a per-PRN table. Output = last stage of reg 1 XOR last stage of reg 2. Reg 1 resets after chip 8190. | `generateB2aDataCode.m` |
| Pilot code | Same form. Reg 1 taps `[3 6 7 13]`, reg 2 taps `[1 5 7 8 12 13]`, own table. | `generateB2aPilotCode.m` |
| Pilot secondary code | **Not in MATLAB.** The ICD defines it (section 11). | — |
| Acquisition | FFT code-phase search over 2 ms (1 ms zero-padded). Doppler ±10 kHz, 500 Hz step. 15 non-coherent sums of 1 ms. Each ms adds `abs(corr_data) + abs(corr_pilot)`. | `acquisition.m` |
| Detection metric | `peak / sqrt(var(first ms) * N) / nNonCoh`; threshold 5 (B2a), 4.5 (L5) | `acquisition.m` |
| Fine search | 25 Hz steps over ±250 Hz. At least 10 ms. Sum of `abs(1 ms coherent sums)` for data and pilot. Insensitive to data and to the secondary code. | `acquisition.m` |
| Channel set-up | `codeFreq = 10.23e6 + (acqFreq - IF)/carr * 10.23e6` | `preRun.m` |
| Tracking epoch | 1 ms, whole samples: `blksize = ceil((10230 - remCodePhase)/codePhaseStep)` | `tracking.m` |
| Correlators | Early, prompt, late at ±0.5 chip, on data and pilot | `tracking.m` |
| Carrier discriminator | `atan(Q_P/I_P)/(2*pi)` on data. Pilot prompt is rotated by −π/2, then `atan`. The two are averaged. | `tracking.m` |
| Code discriminator | `(E-L)/(E+L)` with `E = abs(I_E + jQ_E)`. Data and pilot are averaged. | `tracking.m` |
| PLL | 3rd order. `Wn = 1.2*15 Hz`. `pf3 = Wn^3 T^2`, `pf2 = 2 Wn^2 T`, `pf1 = 2 Wn`. No FLL. No pull-in mode. No lock detector. | `calcLoopCoefCarr.m` |
| DLL | 2 Hz, damping 0.7, `T = 1 ms`, first-order NCO from `calcLoopCoef` | `calcLoopCoef.m` |
| C/N0 | B2a: `Calc_CNo_PLD` every 200 ms. L5: `CNoVSM` over 400 ms. | `Calc_CNo_PLD.m`, `CNoVSM.m` |
| Nav bits | Data prompt `I_P`. Correlate with the 24-bit preamble stretched by `[1 1 1 -1 1]`. Threshold 115. Fix polarity. Sum over 5 ms. Take 288 bits. CRC-24Q. | `BCNAV2decoding.m` |
| Nav message types | 10 and 11 (ephemeris), 30 (clock, TGD, ISC, iono), 31–34 | `ephemeris.m` |
| Time of week | `SOW = 18-bit field * 3` seconds | `ephemeris.m` |
| Pseudorange | `TOW + code count`; first receive time = `max(tx) + 68.802 ms` | `calculatePseudoranges.m` |
| Fix period | 500 ms | `initSettings.m` |
| WLS weights | Linear map of C/N0: 35 dB-Hz → 0.1, 52 dB-Hz → 3. Optional elevation weights. | `setWLS.m`, `setWLS_el.m` |
| Satellite down-select | If more than 8 satellites, pick the best geometry (B2a only, `limit_sats`) | `postNavigation.m` |
| Troposphere | Goad-Goodman model at fixed 1013 hPa, 293 K, 50 % | `tropo.m` |
| Batch categories | 1 error, 2 no fix, 3 error > 25 m, 4 error ≤ 25 m | `Automate_*.m` |

### 2.4 MATLAB algorithms and values (B1C, `CU-SDR-Collection-main/BDS/B1C/`)

The open-service B1C ICD is in the repository
(`docs/icd/BDS_B1C_OpenService_ICD.pdf`, OQ-13 closed). **The MATLAB code
tables are verified:** on 2026-10-14 every `[w, p]` pair in the MATLAB data
(63), pilot (63) and secondary-code (63) tables reproduced the first and
last 24 chips given in ICD Tables 5-2, 5-3 and 5-4. The result is in
`docs/icd/bds_b1c_code_tables.json`. The MATLAB tracking and message
decoder values below are still only MATLAB behaviour.

| Item | MATLAB value or method | Source file |
| --- | --- | --- |
| Sample file | 8-bit I/Q, `fs = 8e6`, `IF = 20e3`, front-end bandwidth `FEBW = 27e6` (used for WB only; the ICD signal bandwidth is 32.736 MHz) | `initSettings.m` |
| Carrier / chip rate / length | 1575.42e6 Hz / 1.023e6 / 10230 chips (**10 ms**) | `initSettings.m` |
| Primary codes | Truncated **Weil** code. Length `N = 10243`, Legendre symbol by Jacobi symbol. Bit `n` = `L(k) XOR L((k+w) mod N)`, `k = (n+p-1) mod N`, `n = 0..10229`. One `[w, p]` pair per PRN, 63 rows each, separate tables for data and pilot. Value `1 - 2*bit`. | `generateDataBOC11.m`, `generatePilotBOC11.m`, `JacobiSymbol.m` |
| Subcarrier on the data | BOC(1,1): each chip becomes `[-c, +c]` (2 half-chips per chip). **The ICD says sine-phased, `sign(sin(2 pi f t))`, so the first half-chip is `+c`. MATLAB has the opposite sign.** | `generateDataBOC11.m` |
| Pilot | **QMBOC(6,1,4/33)**: a BOC(1,1) part (`generatePilotBOC11.m`) and a BOC(6,1) part (12 half-chips of `(-1)^i * c`, `generatePilotBOC61.m`). The ICD says the BOC(1,1) part is 90° from the data and the BOC(6,1) part is **in phase with the data** (section 11.3). | `generatePilotBOC*.m`, `NB_tracking.m` |
| Pilot secondary code | Truncated Weil, `N = 3607`, 1800 chips, one `[w, p]` pair per PRN (63 rows). One chip per 10 ms primary period: 18 s. The data component has **no** secondary code (ICD Table 5-1). | `generate2ndCode.m` |
| Acquisition | Pilot only (`pilotACQflag`). 10 ms coherent (`acqCohT`). Step `1000/acqCohT/2 = 50` Hz. ±5 kHz. Threshold 10. | `initSettings.m`, `acquisition.m` |
| Tracking modes | `pilotTRKflag = 1`: **NB** — correlate with the BOC(1,1) waveforms only (data and pilot). `= 2`: **WB** — adds the BOC(6,1) pilot part, with a data/pilot weighting from `CalcWeighingFactor` (uses `FEBW`). | `NB_tracking.m`, `WB_tracking.m`, `CalcWeighingFactor.m` |
| Loop values | 10 ms epoch. PLL 18 Hz. DLL 1 Hz. E–L half-spacing 0.06 chip (WB limit 0.0765 chip, Betz 2016). C/N0 every 50 epochs. | `initSettings.m` |
| Nav bits (NB) | Pilot `Q_P` signs are correlated with the secondary code (threshold 1799.5) to find the frame start. Then `I_P` of the data channel gives 1800 symbols. | `BCNAV1decoding.m` |
| Frame (B-CNAV1) | 1800 symbols = 18 s. Subframe 1: 72 symbols = BCH(21,6) (PRN) + BCH(51,8) (SOH, seconds of hour ×18 s). Subframes 2 and 3 are block-interleaved (36 rows × 48 columns): subframe 2 = 1200 symbols (600 bits), subframe 3 = 528 symbols (264 bits). Each ends in a CRC-24Q. LDPC is **skipped** (systematic bits taken directly). The ICD confirms this layout (section 11.3). | `BCNAV1decoding.m`, `BCH21_6Decoding.m`, `BCH51_8Decoding.m` |
| Time of week | `TOW = HOW*3600 + SOH*18`; HOW (8 bit) and WN (13 bit) are in subframe 2. The ICD agrees. | `ephemeris.m` |
| Ephemeris and clock | Subframe 2: WN, HOW, IODC, IODE, `toe`, SatType, quasi-Keplerian terms, `toc`, clock. Subframe 3: pages (page id, HS, integrity flags, BDGIM, BDT-UTC, BGTO ...). | `ephemeris.m` |

The wide-band mode needs about 27 MHz of front-end bandwidth. HERON ground
tests used 15 Msps / 15 MHz (D-035). Use NB mode first (OQ-14).

### 2.5 Known problems in the MATLAB code (do **not** port these)

Treat each item as a suspect. Check it against the ICD or the RINEX file
before you copy any behaviour. Items marked **(ICD)** were checked against
the B2a ICD on 2026-10-14.

1. `leastSquarePos.m` starts the position at the **true** position
   (`settings.truePosition`). The fix is biased towards the truth. Do not port.
2. `postNavigation.m` (B2a + L5) adds `+14 − 0.009` s to the local time for
   GPS and subtracts 14 s later. This is a BDT/GPST shift done by hand. It
   also has a 0.009 s term with no explanation. Replace with an explicit time
   system.
3. The combined fix has **one** receiver clock. BDS and GPS hardware delays
   differ, so an inter-system bias is left in the residuals.
4. `ephemeris.m` (B2a) message types 33 and 34 reuse the same bit ranges for
   different fields (for example `WN_ot`, `WN_LSF` and `DN` all read bits
   123:133). **(ICD)** Type 33 is the BGTO message and type 34 is the BDT-UTC
   message. Only types 10, 11 and 30 are safe to take as a guide. Re-derive
   all other fields from the ICD.
5. `satposB2a.m` treats GEO like IGSO. **(ICD)** This does not matter for
   B2a: GEO satellites shall not transmit B2a. Reject a GEO `SatType` instead.
6. `satposB2a.m` clock term `t_sv = T_GDB1Cp + T_GDB2ap + ISC_B2ad` is
   **wrong**. **(ICD)** For B2a pilot the correction is `− T_GDB2ap`. For the
   B2a data component it is `− T_GDB2ap − ISC_B2ad`. `T_GDB1Cp` is for B1C.
7. `BCNAV2decoding.m` skips LDPC. **(ICD)** The code is a 64-ary LDPC(96,48)
   and it is systematic: the first 48 symbols (288 bits) are the message. So
   the shortcut works for error-free frames, and the CRC catches the rest.
   Keep it as an option, not as the only path.
8. `BCNAV2decoding.m` uses the 5-chip pattern `[1 1 1 -1 1]` on the data
   channel. **(ICD)** This is correct. The B2a data secondary code is the
   fixed sequence `00010` (0 → +1, 1 → −1). It is one chip per primary period
   and equals one 200 sps data symbol (5 ms).
9. `acquisition.m` has no per-bin false-alarm model. Python already has one
   (`bpsk_acquisition`). Do not port the threshold logic.
10. The `fseek` start uses the acquired code phase as a **byte** offset. In
    Python the stream gives sample positions. Do not copy the file handling.
11. **(ICD)** The B2a PRN table has 63 rows in the MATLAB file. The MATLAB run
    lists use `1:62`. The ICD defines PRN 1–63. The tables for PRN 1–62 equal
    the ICD (checked). PRN 63 was not compared.
12. B1C: the `wp_*` tables are copied into several files (`generateDataBOC11.m`,
    `generatePilotBOC11.m`, `generatePilotBOC61.m`, `generate2ndCode.m`). The
    values are correct (verified) but keep one table per code in Python.
13. B1C: `BCNAV1decoding.m` accepts a frame when both CRC-24Q checks pass and
    skips LDPC. **(ICD)** The codes are 64-ary LDPC(200,100) for subframe 2
    and LDPC(88,44) for subframe 3, and both are systematic. Same remark as
    item 7.
14. B1C: the BOC(1,1) sign. **(ICD)** The ICD waveform is `sign(sin(2 pi f t))`
    (first half-chip positive). MATLAB's `[-c, +c]` is the opposite. The
    effect is a sign flip of the data bits and of the pilot secondary code.
    BCH polarity checks and secondary-code correlation hide it in MATLAB. In
    Python use the ICD sign.
15. B1C: `ephemeris.m` reads IODC as 11 bits (`22:32`) and IODE from `32:39`.
    **(ICD)** Subframe 2 is: WN 13, HOW 8, IODC **10**, IODE 8, ephemeris I
    203, ephemeris II 222, clock 69, `T_GDB2ap` 12, `ISC_B1Cd` 12,
    `T_GDB1Cp` 12, reserved 7, CRC 24. The overlap is a MATLAB bug. Re-derive
    every offset from the ICD.
16. B1C: the MATLAB tracking and the nav decoder use the pilot `Q_P` for NB
    mode because the pilot BOC(1,1) part is 90° from the data. **(ICD)**
    Correct. In WB mode the BOC(6,1) part is in phase with the data (I), not
    with the pilot BOC(1,1) part.

---

## 3. Target: the Python code in `gnss_processing/`

**Warning.** `gnss_processing/docs/architecture.md` is **out of date**. It
describes an older tree (no `tracking_channel.py`, no `code_components.py`).
Read the code, not that file. Update it as part of Phase 9.

### 3.1 Layout

```
gnss_processing/
  utils/
    signal_interfaces.py   Signal classes, ACQUISITION_POLICIES, TRACKING_POLICIES,
                           create_tracking_channels, build_signals
    code_components.py     CodeComponent, CodeSet (codes on one chip axis; overlays;
                           branches; BOC/TMBOC subcarriers)
    bpsk_acquisition.py    FFT acquisition, fine search, detection threshold
    bpsk_correlation.py    numba correlator kernels (BPSK and subcarrier)
    tracking_channel.py    TrackingChannel, loop filters, overlay sync, CN0 estimator
    tracking_supervisor.py HERON: lock-loss recovery
    gap_consensus.py       HERON: overflow-gap vote
    period_ambiguity.py    HERON: whole-code-period fix
    secondary_code.py      overlay phase search (brute force, FFT)
    sample_streaming.py    file -> complex64 buffers
    collect_metadata_utils.py, catalog.py   metadata.yml, collect and run catalogs
    tracking_io.py         save/load tracking runs (HDF5)
    nav/                   symbols.py, bitsync.py, primitives.py (CRC-24Q, Viterbi, BCH,
                           block interleave), lnav.py, cnav.py, cnav2.py (GPS L1C frame,
                           BCH(51,8), no LDPC), ephemeris.py
    observables.py         TimeAnchor, pseudoranges, clock and iono/tropo helpers
    navigation.py          satellite positions, corrections, solve_epoch/series
    broadcast_ephemeris.py RINEX 2 GPS broadcast file loader (GPS only)
    precise_orbits.py      SP3 truth (COD0MGXFIN covers BDS)
    plotting.py
  notebooks/               00, 01, 02 (+ two dev notebooks)
  tests/                   pytest; synthetic.py, scenarios.py, golden/*.npz
  scripts/run_notebook.py  headless runner for notebooks 00-02
  docs/icd/                ICDs and verified code tables (section 11)
  submodules/gnss-tools    pinned; do not edit
```

### 3.2 Facts that shape the port

- **B2a is close to GPS L5.** Same carrier, chip rate, code length, pilot
  and data in quadrature. L5 already runs end to end (`GpsL5`). Copy its
  structure.
- **B1C is close to GPS L1C.** Same carrier, chip rate and code length
  (10230 chips, 10 ms), data and pilot, an 1800-chip pilot overlay,
  BOC(1,1) subcarrier. `GpsL1C` (with its policies and the FFT overlay
  search) already exists. Copy its structure. Differences to handle:
  B1C data (BOC(1,1), 1/4 of the power) and the pilot BOC(1,1) part (29/44)
  are in **quadrature** (L1C: same phase); the pilot also has a BOC(6,1)
  part (1/11) in phase with the data (QMBOC, not TMBOC); the data has **no**
  overlay; the codes are Weil codes with a different construction; the nav
  message uses 64-ary LDPC and its own BCH codes and interleaver. Do not
  reuse the GPS L1C BCH(51,8), the 38 × 46 interleaver or the binary LDPC
  sizes (`nav/primitives.py`, `nav/cnav2.py`): B1C differs in all three.
- A signal is a `Signal` subclass. It builds `CodeComponent`s per PRN. A
  component holds a ±1 `int8` code, a `Branch` (I or Q), an optional
  `overlay` (secondary code), an optional `subcarrier`, a `power_weight` and
  an optional `symbol_period_ms`.
- Acquisition and tracking policy are **not** on the signal. They sit in
  `ACQUISITION_POLICIES` and `TRACKING_POLICIES`, keyed by `signal_type_id`.
- The tracking channel supports an overlay (secondary code) per component. It
  finds the overlay phase after lock (`OverlaySynchroniser`; brute force by
  default, FFT search for long overlays) unless acquisition gives it.
- `signal_type_id` must match `[A-Z]+_[A-Z0-9]+` in capitals. The tracking
  file name parser needs this (`catalog.py:35`). Use `BDS_B2A` and `BDS_B1C`.
- Tracking loops are different from MATLAB. Python uses an FLL/PLL mode
  switch, per-interval correlation and a longer coherent time after lock.
  **Do not port the MATLAB loops.** Map the parameters instead (section 7).
- **GPS is hard-coded in these places.** Each needs a change:

| Place | What is hard-coded |
| --- | --- |
| `signal_interfaces.py:148` | `signal_id = f"G{prn:02d}"` |
| `signal_interfaces.py:683` | `build_signals`: `f"G{prn:02d}"`, `prns=range(1, 33)` |
| `signal_interfaces.py:61-79` | `LinkId` and `LINKS` (L1 and L5 exist; B1C and B2a use them) |
| `observables.py:47-52` | `BAND_FREQ_HZ` has GPS ids only |
| `observables.py:61` and `:86` | `TimeAnchor` says "GPS time of week"; `anchor_from_cnav` builds `G..` (line 100) |
| `observables.py:304` | `signal_group_delay_s` knows `CnavEphemeris` only |
| `observables.py:366` | `klobuchar_delay_s` is the GPS model |
| `navigation.py` | One receiver clock. `Ephemeris` is GPS only. |
| `broadcast_ephemeris.py:472` | `sat_id.lstrip("G")`; RINEX 2 GPS `brdc` files only |
| `nav/ephemeris.py:42-56` | GPS `MU`, `OMEGA_E_DOT`; `Ephemeris` union is LNAV or CNAV |
| `nav/symbols.py:51-115` | Tables keyed by the four GPS signal ids |
| Notebook 01 | `SIGNAL_ID` dicts keyed by the four GPS classes |
| Notebook 02 | `MESSAGE_FORMAT`, `CODE_PERIOD_MS`, minimum track time, `G`-prefix lists, GPS week logic, `lstrip("G")`; the GPS L1C branch stops with `NotImplementedError` |

### 3.3 Notebook map (what each notebook does today)

| Notebook | Sections | Needs a BDS change? |
| --- | --- | --- |
| `00-raw-signal-diagnostics` | Pick a channel, band coverage, sample histogram, PSD, spectrogram | Little. No GPS code. Update text; check the band table lists B2a and B1C. |
| `01-acquisition-and-tracking` | Config (signal, collect, offset), acquisition, tracking with supervisor, plots, save HDF5 | **Yes.** Add the classes to the signal lists and per-signal dicts. |
| `02-navigation` | Load run, decode nav, time check, ephemeris, SP3 check, skyplot, pseudoranges, fix, clock | **Yes, large.** Decode branch, BDT time, BDS ephemeris source, multi-system fix. |

### 3.4 Environment note

`gnss_processing/.venv` exists but has **no numpy** (not synced).
`gps-tracking-example/.venv` has numpy 2.3.5 and scipy 1.18.1. Do not use
that one for HERON work (it is the read-only tree's env). Ask the user which
environment to use (Phase 0).

---

## 4. Concept mapping: MATLAB → Python

"Reuse" means the code exists. "New" means write it. "Drop" means do not port.

| MATLAB | Python target | Action |
| --- | --- | --- |
| `initSettings.m` etc. | Notebook 01 config cell; `TrackingLoopParameters`; `AcquisitionConfiguration`; `metadata.yml` (sample format, IF, rate) | Reuse. Add `BdsB2a` and `BdsB1C` entries to each per-signal dict. |
| `generateB2aDataCode.m`, `generateB2aPilotCode.m` | New `utils/bds_b2a.py` (code generators; integer bit operations, no `circshift`) | **New** |
| B2a secondary codes | Same module (data `00010`; pilot Weil 1021 from a `(w, p)` table) | **New** |
| `generateDataBOC11.m`, `generatePilotBOC11.m`, `generatePilotBOC61.m`, `generate2ndCode.m`, `JacobiSymbol.m` | New `utils/bds_b1c.py` (truncated Weil codes; one table per code; subcarrier by `code_components`, **not** baked into the code array) | **New** |
| `makeB2a*Table.m`, `makeDataTable.m`, `makePilotTable.m` | `bpsk_acquisition` replica build | Drop |
| `acquisition.m` | `bpsk_acquisition.run_acquisition` + `AcquisitionPolicy` | Reuse. Optional later: sum data + pilot. |
| `preRun.m` | `create_tracking_channels` | Reuse |
| `tracking.m`, `NB_tracking.m` | `TrackingChannel` + `TrackingPolicy` per signal | Reuse (policy only). Drop the MATLAB loops. |
| `WB_tracking.m`, `CalcWeighingFactor.m` | New QMBOC subcarrier support in `code_components` | **New, later** (OQ-14) |
| `Calc_CNo_PLD.m`, `CNoVSM.m` | `CN0EstimatorParameters` in `tracking_channel.py` | Reuse. Compare once. |
| `updateProgressBars.m`, `showChannelStatus.m` | `tqdm`, notebook tables | Drop |
| `BCNAV2decoding.m` | New `utils/nav/bcnav2.py` (frame search, CRC, LDPC, decode) | **New** |
| `BCNAV1decoding.m`, `BCH21_6Decoding.m`, `BCH51_8Decoding.m` | New `utils/nav/bcnav1.py` with its own BCH(21,6), BCH(51,8) (B1C polynomials) and the staggered 36 × 48 interleaver; reuse only `primitives.crc24q_check`; model on `cnav2.py`. The three 64-ary LDPC codes (B2a (96,48); B1C (200,100) and (88,44)) share one GF(64) module. | **New** |
| `ephemeris.m`, `eph_structure_init.m` (both) | Parsers in `bcnav2.py` and `bcnav1.py`, one `BdsEphemeris` in `nav/ephemeris.py` | **New** (re-derive from ICD) |
| `navPartyChk.m`, `twosComp2dec.m`, `check_t.m` | `nav/primitives.py` (`unpack_field`, `crc24q`) | Reuse |
| `calculatePseudoranges.m` | `observables.form_observables` + `TimeAnchor` | Reuse. Generalise the time system. |
| `satposB2a.m`, `satpos.m` (B1C) | `BdsEphemeris.orbit_state` using `_orbit_from_kepler_elements` | **New** (BDS constants) |
| `leastSquarePos.m` | `navigation.solve_epoch` | Reuse. **Extend** for two clocks and weights. |
| `setWLS.m`, `setWLS_el.m` | Weights argument in `solve_epoch` | **New** |
| `tropo.m`, `e_r_corr.m`, `topocent.m`, `cart2geo.m`, `togeod.m` | `navigation.py` (Saastamoinen, Sagnac, `ecf2geo`) | Drop |
| `findUtmZone.m`, `cart2utm.m`, `geo2cart.m`, `dms2mat.m`, `mat2dms.m`, `deg2dms.m`, `roundn.m`, `invert.m`, `clksin.m`, `clsin.m` | None | Drop |
| `skyPlot.m`, `plotNavigation.m`, `plotNavNew.m`, `plotTracking.m`, `plotAcquisition.m` | `plotting.py` | Drop (already present) |
| `Automate_*.m`, `Performance_Summary.m`, `init_Aut.m` | Optional `scripts/batch_survey.py` (Phase 9) | Optional |
| `bds_to_utc.m` | BDT conversion helpers in the new time code | **New** |
| `plotPseudoranges.m` | Notebook 02 section 7 plots | Check; probably drop |

---

## 5. Open questions (ask the user; do not decide)

Ask these in chat. Give the recommended answer. Write each answer in
`docs/DECISIONS.md`. Questions marked **BLOCKER** stop a phase.

OQ-1 (B2a ICD) is **closed**: the user supplied the B2a ICD on 2026-10-14.
OQ-11 (GEO) is **closed**: GEO satellites do not transmit B2a (ICD section 2).
OQ-13 (B1C ICD) is **closed**: the open-service B1C ICD was supplied on
2026-10-14 and is in `docs/icd/`.

| ID | Question | Recommended answer | Blocks |
| --- | --- | --- | --- |
| OQ-2 | What names the signals in code and files? | `BdsB2a` / `"BDS_B2A"`, `BdsB1C` / `"BDS_B1C"`, satellite ids `C01` to `C63` (RINEX style; the ICD defines PRN 1–63). | Phase 1 |
| OQ-3 | Where do the new code generators live? `gnss-tools` is a pinned submodule that HERON cannot push to. | New `utils/bds_b2a.py` and `utils/bds_b1c.py` in `gnss_processing/`. Easy to copy upstream later (D-044). | Phase 1 |
| OQ-4 | Acquisition policy: pilot only (like L5 and L1C), or sum of data and pilot (like the B2a MATLAB)? | Pilot only first (parity with GPS). Add the sum later if the gain is needed (about 3 dB for B2a). | Phase 2 |
| OQ-5 | **BLOCKER for Phases 3 and 7.** Which recordings validate the port? B2a: an L5-band collect under `D:\01_Data\collects` (for example `20261001_URSP_Testing`, `gpsl5_RHCP_capture.dat`). B1C: an L1-band collect (for example `20261008_USRP_Testing`, 15 Msps). The course data is not on this machine. | The user names 1 or 2 collects per band and, if wanted, puts the course data on disk. B2a needs about 20 MHz of bandwidth; B1C in NB mode needs about 4–5 MHz. | Phases 3, 7 |
| OQ-6 | Ionosphere model for BeiDou: none (as MATLAB), GPS Klobuchar on BDS, **BDGIM** (9 coefficients in B-CNAV2 type 30; ICD section 7.8), or the dual-frequency B1C + B2a combination (ICD section 7.8.3)? | Start with none. Show the size of the error in the notebook. Add BDGIM or the dual-frequency fix later. | Phase 5 |
| OQ-7 | Truth source for BDS ephemeris: a RINEX 3 multi-GNSS broadcast file from CDDIS (new loader), plus the MGEX SP3 file (`COD0MGXFIN`, already used). May the code download RINEX 3 `BRDM` or `BRDC` files with the existing Earthdata login? | Yes. | Phase 5 |
| OQ-8 | Combined fix: one fix with a receiver clock for GPS and one inter-system bias for BDS (5 unknowns)? Or two fixes? The B-CNAV2 type 33 message carries BDT–GPS time offset terms (`A0BGTO`, `A1BGTO`, `A2BGTO`) that can seed the bias. | One fix, 5 unknowns, when at least 5 satellites are in view. Fall back to a fixed bias from the data when fewer. | Phase 6 |
| OQ-9 | Combined run structure: run notebook 01 once per signal and let notebook 02 load all tracking files? Or one multi-signal notebook 01? | One run per signal. No change to the tracking code; matches the MATLAB `combinedTrkResults.mat` idea. | Phase 6 |
| OQ-10 | LDPC: the B2a ICD gives the full 64-ary LDPC(96,48) (H matrix, GF(64) tables, encoding example). Write the full decoder, or use the systematic-symbol shortcut with the CRC? | Shortcut first (config switch). Write the encoder (for tests) and the extended min-sum decoder after. The ICD example is a golden vector. | Phase 4 |
| OQ-12 | How close must Python be to MATLAB? | Statistical, not bit-exact: same PRNs acquired (±1 on marginal ones), Doppler within one fine bin, C/N0 within 1.5 dB-Hz, position within a set number of metres. The user sets the numbers. | Phase 3 |
| OQ-14 | B1C pilot: track the BOC(1,1) part only (MATLAB NB mode), or add the BOC(6,1) part (WB mode)? NB uses 10/11 of the signal power (the BOC(6,1) part is 1/11, about 0.4 dB). WB needs about 27 MHz of bandwidth (the ICD signal bandwidth is 32.736 MHz) and a new QMBOC subcarrier kind. | NB first. WB later, only if a collect has the bandwidth (HERON used 15 MHz). | Phase 7 |
| OQ-15 | Cross-signal help: one satellite uses the **same PRN** on B1C and B2a (ICD section 5.1). The four SDRs share a 10 MHz and PPS clock (D-025). May a B1C channel use the ephemeris decoded from the B2a channel of the same PRN, and a common receive time? | No for now. Decode each signal's own message first. Revisit when both work. | Phase 8 |
| OQ-16 | Is the dual-frequency B1C + B2a (iono-free) fix a goal? | Optional. Do it after the single-signal fixes. | Phase 8 |

---

## 6. Work plan

Each phase lists tasks, files and a "Done when" check. Use the existing
tests as templates: `tests/test_l5.py` and `tests/test_l1c.py` (signal,
acquisition, tracking), `tests/test_l1c_codes.py` (code checks),
`tests/test_nav_cnav2.py` (message encoder and decoder),
`tests/test_nav_ephemeris.py`, `tests/test_navigation.py`.

### Phase 0 — Set-up and baseline

1. Read the root `CLAUDE.md`, this file, `gnss_processing/README.md`,
   `TODO_SIGNALS.md`, and the module docstrings of `signal_interfaces.py`,
   `code_components.py`, `tracking_channel.py`, `secondary_code.py`,
   `nav/symbols.py`, `nav/cnav2.py`, `observables.py`, `navigation.py`.
2. Set up the environment per the README (section 3.4). This machine is
   Windows. The `run-gnss-notebooks` skill and `scripts/run_notebook.py`
   were written for Linux (kernel name, paths). `gnss_processing/.env` holds
   Linux paths (D-036). Ask the user which Python environment and which
   `.env` to use here. Do not print `.env`; it holds credentials.
3. Run the whole test suite. Save the result. Record any test that fails
   **before** your change. Do not "fix" those unless the user asks.
4. Ask OQ-2, OQ-3, OQ-5, OQ-12. Record the answers.
5. Create the branch `feat/bds-b2a-b1c` from the current branch. Check the
   working tree first (it has uncommitted user edits in `gnss_processing/`).

Done when: baseline test result is saved; OQ-5 is answered.

### Phase 1 — B2a signal definition: codes and `BdsB2a`

Files: new `utils/bds_b2a.py`; edit `utils/signal_interfaces.py`; new
`tests/test_b2a_codes.py`; fixture `docs/icd/bds_b2a_code_tables.json`.

1. In `utils/bds_b2a.py` write constants (carrier 1.17645e9 Hz, chip rate
   10.23e6, length 10230, data symbol rate 200 sps, secondary lengths 5 and
   100). Read PRN tables from a data structure, not from long literals in
   functions. Use the section 11 facts.
2. Write `generate_data_code(prn)` and `generate_pilot_code(prn)` as
   `int8` ±1 arrays (logic 1 → −1, logic 0 → +1; same as `(1 - 2*bits)`).
   Rules from the ICD: 13-stage registers; register 1 starts all ones;
   register 2 starts from the PRN table; shift towards stage 13; output is
   stage 13 of register 1 XOR stage 13 of register 2; register 1 resets
   after chip 8190; both registers reset at each primary period.
   Use integer bit operations. Cache per PRN. PRN range 1–63.
3. Write the secondary codes: data `00010` → `[+1,+1,+1,-1,+1]`; pilot from
   the truncated Weil code (Legendre of length 1021, phase `w`, truncation
   point `p`, 100 chips), formulas in ICD (5-3) to (5-5).
4. In `signal_interfaces.py` add class attributes `sat_id_prefix` (`"G"`
   default, `"C"` for BDS) and `default_prns`. Use them in `Signal.__init__`
   and `build_signals`. Do not change GPS behaviour.
5. Add `class BdsB2a(Signal)` after `GpsL5`:
   `signal_type_id = "BDS_B2A"`, `link = LINKS[LinkId.L5]`,
   components `B2aD` (`Branch.I`, overlay `00010`, `symbol_period_ms = 5`)
   and `B2aP` (`Branch.Q`, 100-chip overlay, no symbol period),
   `primary_period_ms = 1`. The pilot is 90° from the data (ICD Table 4-2).
   Power ratio 1 : 1. Register it in `SIGNALS`.
6. Tests. (a) Load `bds_b2a_code_tables.json` and check, for all 63 PRNs,
   that the first and last 24 chips (octal) of the data and pilot primary
   codes and of the pilot secondary code equal the ICD. This was done once by
   an independent script on 2026-10-14 (all 63 + 63 + 63 matched); the test
   must repeat it. (b) Write a slow literal copy of the MATLAB LFSR in the
   test file and compare it with the fast code. (c) Check length, ±1 values,
   balance, autocorrelation and cross-correlation. (d) Check PRN 0 and 64
   raise a clear error.

Done when: new tests pass; the full suite is as green as the baseline;
`SIGNALS["BDS_B2A"]` builds for PRN 1–63.

### Phase 2 — B2a acquisition and tracking policy, synthetic tests

Files: `utils/signal_interfaces.py`, `tests/synthetic.py`,
`tests/scenarios.py`, new `tests/test_b2a.py`, `tests/generate_golden.py`.

1. Add `ACQUISITION_POLICIES["BDS_B2A"]` (OQ-4: pilot `B2aP`, no overlay
   folded in; a 100 ms replica is too long). The overlay phase then comes
   from the post-lock search, as for L1C.
2. Add `TRACKING_POLICIES["BDS_B2A"]`: carrier loop on the pilot; delay
   discriminator on data and pilot; Costas until the overlay is synced, then
   the full-angle discriminator on the dataless pilot. Copy the L5 policy and
   change only what the ICD says differs. The data symbol (5 ms) sets the
   coherent-time cap; check `coherent_duration_target_ms`.
3. Extend `tests/synthetic.py` with a B2a generator: data and pilot codes,
   both overlays, data bits at 200 sps, Doppler, code rate slaved to Doppler.
4. Write `tests/test_b2a.py` from `tests/test_l5.py`: acquisition finds the
   code phase and Doppler; the tracker locks; the overlay sync finds the
   offset; the coherent time extends to the cap; the prompt values carry the
   data bits. Add golden files (`tests/golden/b2a_*.npz`) via
   `generate_golden.py` only after the test passes on physics, not before.
5. Add `nav/symbols.py` entries for `BDS_B2A` (data component 0, symbol
   period 5 ms, `NEEDS_BIT_SYNC = False` because the pilot overlay (100 ms)
   is a whole number of symbols, `HAS_OVERLAY = True`, `DATA_IN_QUADRATURE =
   True` when the carrier runs on the pilot, as for L5). Add the
   `BAND_FREQ_HZ` entry in `observables.py`.

Done when: synthetic acquisition and tracking tests for B2a pass; the L5
tests are unchanged and pass.

### Phase 3 — B2a on real data (notebooks 00, 01)

Needs OQ-5. Files: `notebooks/01-...ipynb`, `notebooks/00-...ipynb` (text),
`scripts/run_notebook.py` (check).

1. Notebook 01, import cell: add `BdsB2a`.
2. Notebook 01, config cell: add `BdsB2a` to the `SIGNAL_ID` comment, to the
   acquisition-settings dict, to the tracking-settings dict and to any
   coherent-duration dict. Use the values in section 7.
3. Notebook 01, text cell with the band table: add B2a to the L5 row.
4. Notebook 00: update the text that says which signals sit in each band.
5. Run notebook 00 then 01 on the chosen L5-band collect with
   `SIGNAL_ID = BdsB2a`. Expect some BDS-3 MEO/IGSO PRNs to acquire.
   Compare with the PRNs visible at that time (SP3/RINEX or an online sky
   view).
6. If the course data and the MATLAB `.mat` results are on disk: write a
   `metadata.yml` for the data by hand, following D-029 and D-030 (ask the
   user first). Compare acquired PRNs, Doppler and C/N0 with the MATLAB
   `acqResults.mat` and `trkResults.mat` using the OQ-12 numbers. Put the
   comparison in a small script under `scripts/`, not in the notebook.
7. Check these side effects: `tracking_io` file name (`..._BDS_B2A_...h5`
   matches the catalog regex), the supervisor and gap-consensus code (they
   should not care about the constellation), and `plotting.py` titles.

Done when: notebook 01 runs end to end for `BdsB2a` on one real collect,
saves a tracking file, and `catalog.print_tracking_catalog` lists it. The
comparison with MATLAB (if data exists) is within the OQ-12 limits.

### Phase 4 — B-CNAV2 decoding (B2a message)

Files: new `utils/nav/bcnav2.py`, optional `utils/nav/ldpc.py`; edit
`utils/nav/symbols.py`, `utils/nav/primitives.py`; new
`tests/test_nav_bcnav2.py`. Layouts are in section 11.

1. Frame search. Correlate the symbol stream with the 24-bit preamble
   `0xE24DE8`. Fix the 180° polarity. Take 600 symbols per frame (3 s). Use
   the **symbol stream** from `nav/symbols.extract` (one soft symbol per
   data symbol). Do not copy the MATLAB 5 ms re-sum; the channel already
   gives symbols.
2. CRC-24Q: reuse `primitives.crc24q_check`. The ICD polynomial is the same
   as the MATLAB list `[24 23 18 17 14 11 10 7 6 5 4 3 1 0]`; the register
   starts at zero.
3. LDPC (OQ-10). First the systematic shortcut (symbols 1–48 = 288 bits).
   Then, in a separate step: build GF(2^6) arithmetic (polynomial
   `1 + x + x^6`), the sparse H matrix from the ICD index and element tables,
   the encoder (`G = [I, (H2^-1 H1)^T]`), and the extended min-sum decoder.
   Test the encoder with the ICD annex example (48 input symbols, 96 output
   symbols).
4. Message parsers: types 10, 11 and 30 first (ephemeris I and II, clock,
   IODC, `T_GDB2ap`, `ISC_B2ad`, `T_GDB1Cp`, BDGIM terms). Then 33 (BGTO)
   and 34 (BDT-UTC) if needed. Read every bit range and scale factor from
   the ICD. The MATLAB offsets for types 10, 11 and 30 matched the ICD
   frame layout in a spot check; the other types do not.
5. Add `build_message` and an encoder in the same file (as `cnav.py` has),
   so tests can make real frames.
6. `TimeAnchor` support: `SOW` is a count of 3 s and names the **rising edge
   of the first preamble chip** of its frame. The anchor must carry the time
   system (BDT) and the BDT week (from type 10).
7. Tests (model on `test_nav_cnav2.py`): encode then decode a known
   ephemeris; CRC failure is rejected; polarity flip is handled; a frame
   offset is found; the LDPC decoder repairs symbol errors.

Done when: the decoder round-trips synthetic frames; and, with real tracking
output from Phase 3, it decodes at least one frame with a good CRC and the
decoded PRN equals the tracked PRN.

### Phase 5 — BeiDou ephemeris, time and truth data

Needs OQ-6, OQ-7. Files: `utils/nav/ephemeris.py`, `utils/observables.py`,
`utils/broadcast_ephemeris.py`, `utils/precise_orbits.py`, new
`utils/time_systems.py`; new/edited tests.

1. Time. Write `time_systems.py` with GPST and BDT. BDT starts at
   2006-01-01 00:00:00 UTC and has no leap seconds, so **BDT = GPST − 14 s**
   (derived from the epoch; the BDSBAS ICD section 3.3 agrees). Make
   functions to convert week/TOW both ways. Give the 14 s a named constant
   with a source note. Do not leave `+14` inline.
2. `BdsEphemeris` dataclass next to `CnavEphemeris`. Use the BDCS constants
   and the user algorithm of ICD section 7.7.2 (Table 7-9), see section 11.
   Call the shared `_orbit_from_kepler_elements` where it fits, but the GPS
   constants must **not** be used. Reject `SatType` GEO.
3. Add `BdsEphemeris` to the `Ephemeris` union. Give it `clock_correction_s`
   with the relativistic term `F e sqrt(A) sin(E)` and the group delay as
   the ICD states (section 11; this is **not** what `satposB2a.m` does).
4. `observables.py`: make `TimeAnchor` carry a time system; generalise
   `anchor_from_cnav` (sat id prefix); add a BDS branch to
   `signal_group_delay_s`.
5. Truth data: add a RINEX 3 BeiDou nav loader (OQ-7). Test the orbit model
   against it and against MGEX SP3: position error of a few metres or less
   at the ephemeris reference time. The MATLAB folder has an `AMC400USA ...
   _CN.rnx` file that can serve as an offline fixture (copy a short slice
   into `tests/data/`; keep it small).
6. `precise_orbits.py`: check that SP3 satellite ids `C..` work with the
   existing Lagrange code. Add a test.
7. Ionosphere: apply the choice from OQ-6.

Done when: BDS satellite positions from a decoded or RINEX ephemeris agree
with SP3 to the expected broadcast accuracy; unit tests cover time
conversion, a week rollover and a Kepler case.

### Phase 6 — Position fix: B2a alone, then GPS L5 + B2a (notebook 02)

Needs OQ-6, OQ-8, OQ-9. Files: `utils/navigation.py`,
`utils/observables.py`, `notebooks/02-navigation.ipynb`; new tests.

1. `navigation.py`: make the solver take a system index per satellite.
   Add one clock-bias unknown per extra system (OQ-8). Add an optional
   weight vector (port the C/N0 map from `setWLS.m` as a function with
   config values 35/52 dB-Hz and 0.1/3; do not hard-code). Keep GPS-only
   results identical to today.
2. Do **not** seed the solver with the true position (MATLAB problem 1).
3. Notebook 02, config cell: add `BdsB2a` to the import and to
   `SIGNAL_ID`; add `MESSAGE_FORMAT["BDS_B2A"] = "B-CNAV2"`; add
   `CODE_PERIOD_MS` and the minimum tracking time (a B-CNAV2 frame is 3 s;
   allow for frame search plus pull-in).
4. Notebook 02, decode cell: add the B-CNAV2 branch (section 3.2 table row
   "Notebook 02").
5. Notebook 02, time cells: the file name gives UTC. Convert UTC → GPST →
   BDT with named leap-second and offset constants. Use the BDT week for BDS
   satellites.
6. Notebook 02, ephemeris and skyplot cells: replace `f"G{prn:02d}"` loops
   and `lstrip("G")` with calls that take the constellation from the signal.
   Load BDS ephemeris from the Phase 5 source.
7. Combined fix (OQ-9): add a section that loads a list of tracking runs
   (for example one `GPS_L5` file and one `BDS_B2A` file for the same
   collect), forms observables for each, and joins them on one measurement
   grid. Check that both runs use the same `START_OFFSET_MS` and sample
   clock.
8. Re-check GPS L5 alone with the new solver. The result must not change.
9. Tests: two-system synthetic fix with a known inter-system bias; weights
   change the answer in the expected direction; one-system case equals the
   old result.

Done when: notebook 02 gives a B2a-only fix on the Phase 3 collect; the fix
is inside the error you expect from broadcast data (compare to the known
antenna position, not used to seed the solver); the GPS L5 + B2a fix runs; all
old tests pass.

### Phase 7 — B1C signal (codes, policies, notebook 01)

Needs OQ-5. The ICD is in `docs/icd/` and the code tables are verified
(section 11.3). Files: new `utils/bds_b1c.py`; edit
`utils/signal_interfaces.py`, `utils/code_components.py` (only if OQ-14 asks
for QMBOC), `utils/nav/symbols.py`, `utils/observables.py`; new
`tests/test_b1c_codes.py`, `tests/test_b1c.py`; notebook 01 and 00 text.

1. In `utils/bds_b1c.py` write: Jacobi/Legendre helper (use integer
   arithmetic and numpy, cached; `N = 10243` and `N = 3607`); the truncated
   Weil primary code for data and pilot from the per-PRN `[w, p]` tables;
   the 1800-chip pilot secondary code. Load the `[w, p]` pairs from
   `docs/icd/bds_b1c_code_tables.json` (one place; no copies in code).
   There is **no** data secondary code. PRN range 1-63. Logic 1 -> -1.0.
2. Add `class BdsB1C(Signal)` modelled on `GpsL1C`:
   `signal_type_id = "BDS_B1C"`, `link = LINKS[LinkId.L1]`, carrier
   1575.42e6, chip rate 1.023e6, `primary_period_ms = 10`. Components:
   `B1CD` (BOC(1,1) via `Subcarrier(BOC_SIN)`, `Branch.I`,
   `symbol_period_ms = 10`, no overlay, `power_weight` 1/4) and `B1CP`
   (BOC(1,1) part, NB mode, `Branch.Q`, 1800-chip overlay, no symbol
   period, `power_weight` 29/44). Check that `BOC_SIN` is the ICD's
   `sign(sin(2 pi f t))` (first half-chip positive). Do **not** put the
   subcarrier inside the code array (see `code_components` docstring).
3. Policies, copied from `GpsL1C` and changed only where the ICD differs:
   acquisition on the pilot without the overlay; overlay search by
   `secondary_code.fft_search` with 200 prompts; carrier on the pilot;
   Costas until the overlay is synced; DLL 0.5 Hz and 0.5 chip as
   `GpsL1C`; MATLAB used 1 Hz and 0.06 chip for WB.
   `nav/symbols.py`: symbol period 10 ms, `HAS_OVERLAY = True`,
   `NEEDS_BIT_SYNC = False`, `DATA_IN_QUADRATURE` from the carrier choice
   (data and pilot are in quadrature, so it is **True** if the carrier runs
   on the pilot).
4. Tests: (a) load `bds_b1c_code_tables.json` and check, for all 63 PRNs,
   that the first and last 24 chips (octal) of the data primary, pilot
   primary and pilot secondary codes equal the ICD (the table was checked
   once on 2026-10-14; the test must repeat it; ICD example: PRN 1 data code
   starts `101011111111011001001110` = `53773116`); (b) property tests:
   length, balance, auto- and cross-correlation; (c) with the course data,
   the acquired PRNs match MATLAB's `acqResults.mat`; (d) synthetic
   acquisition, tracking and overlay-sync tests modelled on
   `tests/test_l1c.py`.
5. Notebook 01: add `BdsB1C` to the imports, `SIGNAL_ID` comment and the
   per-signal dicts; add B1C to the L1 row of the band table. Run on an
   L1-band collect. GPS L1 C/A and L1C PRNs also appear in that collect;
   BDS PRNs must not be reported as GPS.
6. OQ-14: only if the user asks for WB: add a QMBOC kind to
   `code_components` (BOC(1,1) and BOC(6,1) as two components of the pilot
   with `power_weight` 29/44 and 1/11 of the total power; the BOC(1,1)
   part on `Branch.Q`, the BOC(6,1) part on `Branch.I`, in phase with the
   data; ICD section 4.2.2) and test it.

Done when: notebook 01 runs end to end for `BdsB1C` on one real L1 collect;
tracking file saved; with the course data, the acquired PRNs match MATLAB's
`acqResults.mat` (OQ-12 limits).

### Phase 8 — B-CNAV1 decoding and B1C fixes

Layouts are in section 11.3. Files: new `utils/nav/bcnav1.py`;
edit `utils/nav/ldpc.py` (shared), `utils/nav/primitives.py`,
`utils/nav/ephemeris.py`, `utils/observables.py`, notebook 02; new
`tests/test_nav_bcnav1.py`.

1. Frame: 1800 symbols = 18 s. Find the frame start from the pilot secondary
   code (the symbol stream gives the data symbols; `secondary_code` gives the
   pilot phase). Subframe 1: BCH(21,6) for the PRN (generator `x^6 + x^4 +
   x^2 + x + 1`) and BCH(51,8) for the seconds of hour (generator `x^8 +
   x^7 + x^4 + x^3 + x^2 + x + 1`). These are **not** the GPS L1C BCH(51,8)
   in `primitives.py`; write new encoders and decoders and test them.
   Subframes 2 and 3 use the staggered 36 × 48 block interleaver (section
   11.3), **not** `primitives.block_interleave`. CRC-24Q: reuse
   `primitives.crc24q_check`. Use the structure of `cnav2.py` as a model.
2. LDPC for subframes 2 and 3 (1200 and 528 symbols): systematic shortcut
   first (symbols 1-100 and 1-44 of each codeword). A decoder is a separate
   step. The B-CNAV1 codes are 64-ary LDPC(200,100) and LDPC(88,44) over
   GF(64), the same field as B2a's LDPC(96,48). Build one GF(64) module
   (`nav/ldpc.py`) for all three, with the H matrices from the ICD. GPS L1C
   uses **binary** LDPC, so that module is not shared with `cnav2.py`.
3. Parsers: subframe 2 (WN, HOW, IODC, IODE, ephemeris, clock) and the
   subframe 3 pages needed (health, group delay, BDGIM). Time of week is
   `HOW * 3600 + SOH * 18`. Tests with an encoder, as in Phase 4.
4. `BdsEphemeris` is shared with B2a. The ICD ephemeris I and II blocks
   (203 and 222 bits, same widths and scales) match B-CNAV2; compare the
   user-algorithm table too and add any difference explicitly. B1C group
   delay (ICD 7.6.2): pilot `dt_sv,B1Cp = dt_sv - T_GDB1Cp`; data
   `dt_sv,B1Cd = dt_sv - T_GDB1Cp - ISC_B1Cd`.
5. Notebook 02: add the B-CNAV1 decode branch, `MESSAGE_FORMAT["BDS_B1C"] =
   "B-CNAV1"`, and the minimum tracking time (a frame is 18 s plus pull-in
   and the subframe 2 and 3 CRC; allow 40 s or more, as for `GPS_L1C`).
6. Optional (OQ-15, OQ-16): after the single-signal fixes work, add (a) the
   dual-frequency B1C + B2a iono-free pseudorange (ICD B2a section 7.8.3),
   and (b) sharing of ephemeris between signals of one PRN. Ask first.

Done when: notebook 02 gives a B1C-only fix on an L1 collect; and the
B1C, B2a and GPS fixes agree within the error you expect.

### Phase 9 — Hand-over: docs, scripts, batch tool

1. `scripts/run_notebook.py`: check that `--set SIGNAL_ID=BdsB2a` and
   `BdsB1C` work. Add an example to its docstring.
2. `.claude/skills/run-gnss-notebooks/SKILL.md`: add B2a and B1C to the list
   of signals. Note the Windows differences if the user confirms them.
3. `gnss_processing/README.md`: add the new files to the "HERON edits" list.
4. `gnss_processing/docs/architecture.md`: replace the stale text with the
   current layout (section 3.1 is a start).
5. `docs/DECISIONS.md`: confirm that every OQ answer is recorded.
6. Optional: a batch script that runs notebooks 01 and 02 over many collects
   and sorts them by position error (the MATLAB `Automate_*.m` idea). Ask
   the user first.
7. Write a short follow-up list: Galileo E1/E5a (D-039), joint data+pilot
   acquisition, satellite down-select, BDGIM, QMBOC wide-band tracking,
   BDSBAS-B1C if ever wanted.

Done when: a new engineer can run B2a and B1C through notebooks 00–02 from
the README alone.

---

## 7. Parameter map: MATLAB value → Python parameter

Put each value in the notebook config cell, not in library code. Ask the
user before changing any default that already exists for GPS.

| MATLAB | Value (B2a / B1C) | Python parameter | Note |
| --- | --- | --- | --- |
| `samplingFreq` | 18e6 / 8e6 | `samp_rate` from `metadata.yml` | HERON data differs. |
| `IF` | 20e3 / 20e3 | `inter_freq` in `metadata.yml` | HERON data uses 0. |
| `acqSearchBand` | ±10 kHz / ±5 kHz | `AcquisitionConfiguration.min_search_doppler_hz` and `max_search_doppler_hz` | |
| `acqSearchStep` | 500 Hz / 50 Hz | Derived from the coherent length | Python sets the step from the replica length. |
| `acqNonCohTime`, `acqCohT` | 15 ×1 ms / 10 ms | `num_blocks`, `coherent_duration_replica_ms` | |
| `acqThreshold` | 5 / 10 | `prob_false_alarm_total` (argument of `run_acquisition`) | Python uses a false-alarm model. Do not copy the number. |
| fine search 25 Hz | B2a only | `fine_search` (`FineSearchParameters`) | |
| `dllNoiseBandwidth` | 2 Hz / 1 Hz | `DLL_bandwidth_hz` | `GpsL1C` uses 0.5 Hz. |
| `dllCorrelatorSpacing` | 0.5 chip / 0.06 chip | `EPL_chip_spacing` | B1C 0.06 is for WB (BOC(6,1)). NB: use the `GpsL1C` value. |
| `pllNoiseBandwidth` | 15 Hz / 18 Hz | PLL bandwidth in `TrackingLoopParameters` | Order and filter form differ. |
| `intTime` | 1 ms / 10 ms | `coherent_duration_ms` (cap, then extends) | Cap comes from the data symbol (5 ms / 10 ms). |
| `pilotTRKflag` | 1 (use pilot) / 1 NB, 2 WB | `TrackingPolicy.code_components = (0, 1)` | |
| `CNoInterval` / `VSMinterval` | 200 / 400 ms; 50 epochs | `CN0EstimatorParameters` | |
| `navSolPeriod` | 500 ms / 200 ms | `EPOCH_INTERVAL_MS` in notebook 02 | |
| `elevationMask` | 5° (0° in B2a) | elevation mask in notebook 02 | |
| `weightMat` | `[0 3; 35 52]` | new weight config | |
| `startOffset` | 68.802 ms | `NOMINAL_TRANSIT_TIME_S` (75 ms) | Absorbed by the clock bias. |
| `weightLS_flag`, `weight_el_flag` | 1, 0 | config flags | |

---

## 8. Test and quality gates

- Baseline first (Phase 0). Then run the full suite after every phase.
- New tests sit next to the code they test. Name them as in section 6.
- Golden files change only when the user agrees the physics changed.
- Tests must not need `local-data/`, network access or credentials. Use
  synthetic signals and small fixtures. Mark any test that needs real data
  so it skips when the data is missing.
- Check these before each commit: `pytest` (all), the notebooks 01 and 02
  still run for `GpsL5` and `GpsL1CA` (no regression), and no secret or
  large file is in the commit.
- Commit messages and PR text in STE. Use the attribution lines the
  harness gives.

---

## 9. Risks

| Risk | Effect | Mitigation |
| --- | --- | --- |
| MATLAB tables copied with a typo | Code does not match the satellite | B2a: test against the verified ICD fixture. B1C: copy by script; test with real data (a correct PRN gives a strong peak). |
| PDF text extraction misreads tables | Wrong fixture | The PRN labels in the B2a ICD text layer are offset by one row. Use row order and check page images. |
| Two time systems | Errors of 14 s (4 million km) | One named constant, one conversion module, tests at a week rollover. |
| Inter-system bias | Metre-level error in the combined fix | OQ-8; show the bias in the notebook. |
| BDS PRNs mistaken for GPS PRNs in a shared L1 or L5 recording | Wrong satellite id, wrong ephemeris | Satellite id prefix `C` vs `G` everywhere; tests. |
| BOC(1,1) tracking ambiguity (false lock on a side peak) | Wrong code phase | `GpsL1C` already handles BOC; reuse its policy and tests. |
| Windows paths and kernel names | Notebook runner fails | Phase 0 step 2. |
| Uncommitted user edits in the tree | Lost work | Branch first; never reset or stash without asking. |
| `docs/architecture.md` is stale | Wrong mental model | Read the code; fix the doc in Phase 9. |

---

## 10. Checklist for the new session

- [ ] Read sections 0 to 5 and 11.
- [ ] Phase 0 done; baseline saved; OQ-2, 3, 5, 12 asked and recorded.
- [ ] Phase 1: B2a codes, `BdsB2a`, tests green.
- [ ] Phase 2: B2a policies, synthetic tests green.
- [ ] Phase 3: notebook 01 on real L5-band data.
- [ ] Phase 4: B-CNAV2 decoder.
- [ ] Phase 5: BDS ephemeris, time, truth data.
- [ ] Phase 6: notebook 02, B2a fix, then L5 + B2a fix.
- [ ] Phase 7: B1C signal, notebook 01 on L1 data.
- [ ] Phase 8: B-CNAV1 decoder, B1C fix.
- [ ] Phase 9: docs, scripts, follow-up list.
- [ ] `docs/DECISIONS.md` holds every answer.

---

## 11. Reference documents and facts taken from the ICDs

### 11.1 Files

| File | What it is |
| --- | --- |
| `docs/icd/BDS_B2a_ICD.pdf` | BDS-SIS-ICD-B2a-1.0, December 2017. Open-service B2a and the B-CNAV2 message. **Used.** |
| `docs/icd/BDS_B1C_OpenService_ICD.pdf` | BDS-SIS-ICD-B1C-1.0, December 2017. Open-service B1C and the B-CNAV1 message. **Used.** |
| `docs/icd/bds_b1c_code_tables.json` | `[w, p]` and first/last 24 chips (octal) of the B1C data primary, pilot primary and pilot secondary codes, PRN 1-63. The octal values come from the ICD tables; the `[w, p]` values come from the MATLAB tables. Checked 2026-10-14: every MATLAB pair reproduces its ICD octal values (63 + 63 + 63). |
| `docs/icd/bds_b2a_code_tables.json` | Machine-readable copy of ICD Tables 5-2, 5-3 and 5-4 (register-2 start states, first and last 24 chips in octal, secondary-code `w` and `p`) for PRN 1–63. Checked 2026-10-14: an independent generator reproduced every entry. |
| `docs/icd/BDSBAS_B1C_ICD.pdf` | BDS-SIS-ICD-BDSBAS-B1C-1.0, July 2020. The **SBAS** signal from three GEO satellites (PRN 130, 143, 144; 1.023 Mcps BPSK; 250 bps, rate 1/2 convolutional, 500 sps; WAAS-like messages). **Not the open-service B1C.** Not used by this plan. Its section 3.3 says `SNT = BDT + 14 s` and SNT is within 50 ns of GPST. |

Tip: `pdftotext -layout` gives good text for these PDFs. Equations lose their
symbols (subscripts, Greek letters) in the text. Table rows keep their
order, but the PRN labels are shifted by one row. Check anything critical in
the page image (`pymupdf`, 110 dpi is enough).

### 11.2 B2a facts (from the ICD; section numbers are the ICD's)

- **Satellites.** MEO and IGSO satellites transmit B2a. GEO satellites shall
  not (section 2). PRN 1–63 (7.1). B1C and B2a of one satellite have the same
  PRN (5.1).
- **Signal.** 1176.45 MHz, 20.46 MHz bandwidth, RHCP. Complex envelope
  `s = s_data + j s_pilot`: data on I (0°), pilot on Q (90°), power 1 : 1,
  both BPSK(10) (4.2.2, Table 4-2). Logic 1 → −1.0, logic 0 → +1.0 (4.3).
  Minimum power at the ground: −156 dBW (MEO), −158 dBW (IGSO) (Table 4-4).
- **Timing** (4.8). The first chip of each primary code starts a data symbol
  and a secondary-code chip. One secondary chip lasts one primary period.
- **Primary codes** (5.2.1). Length 10230, 10.23 Mcps, 1 ms. Gold-type from
  two 13-stage LFSRs. Data: `g1 = 1 + x + x^5 + x^11 + x^13`,
  `g2 = 1 + x^3 + x^5 + x^9 + x^11 + x^12 + x^13`. Pilot: `g1 = 1 + x^3 + x^6
  + x^7 + x^13`, `g2 = 1 + x + x^5 + x^7 + x^8 + x^12 + x^13`. Register 1
  starts all ones; register 2 starts from Table 5-2 (data) or 5-3 (pilot),
  `s2,1 .. s2,13`. Both registers reset at the start of each period; register
  1 also resets after chip 8190. 126 codes (63 data, 63 pilot).
- **Secondary codes** (5.2.2, Table 5-1). Data: fixed 5 chips `00010`,
  MSB first, same for all satellites (period 5 ms = one 200 sps symbol).
  Pilot: 100 chips, truncated Weil code of length 1021:
  `W(k; w) = L(k) XOR L((k + w) mod N)`, Legendre `L(0) = 0`, `L(k) = 1` if
  `k` is a square mod `N`, else 0; `c(n; w, p) = W((n + p − 1) mod N; w)`,
  `n = 0..99`; `w` in 1–510 and `p` per PRN from Table 5-4 (period 100 ms).
- **Frame** (6.2.1). 600 symbols at 200 sps = 3 s. Symbols 1–24: preamble
  `0xE24DE8` (`111000100100110111101000`), MSB first. Symbols 25–600: 576
  LDPC symbols. Before coding: 288 bits = PRN (6) + MesType (6) + SOW (18)
  + message data (234) + CRC-24Q (24). PRN, type, SOW and data are in the
  CRC. CRC-24Q: generator `g(x) = 1 + x + x^3 + x^4 + x^5 + x^6 + x^7 + x^10 +
  x^11 + x^14 + x^17 + x^18 + x^23 + x^24`, register starts at zero (6.1.2).
- **LDPC** (6.2.2, Annex). 64-ary LDPC(96,48) over GF(2^6), primitive
  polynomial `1 + x + x^6`; 48 information symbols (288 bits), 48 check
  symbols; the first 48 symbols are the message (**systematic**). H is
  48 × 96 sparse with 4 non-zero elements per row, given by the "index" and
  "element" tables in 6.2.2. The annex gives: the encoder
  (`G = [I, (H2^-1 H1)^T]`), the vector/power maps of GF(64), a **worked
  encoding example** (48 symbols in, 96 symbols out; use it as a golden
  test) and two decoders (extended min-sum and another).
- **Message types** (6.2.3, Table 7-1): 10 and 11 (ephemeris I and II,
  always sent together), 30 (clock, group delay, BDGIM), 31 (reduced
  almanac ×3), 32 (EOP), 33 (BGTO, plus one reduced almanac), 34 (BDT-UTC),
  40 (midi almanac). Type 10 order: PRN, type, SOW, WN (13), integrity flags,
  SISMAI (4), B1C flags, IODE (8), ephemeris I (203 bits), CRC. Type 11:
  PRN, type, SOW, HS (2), flags, ephemeris II (222 bits), CRC. Type 30:
  PRN, type, SOW, HS, flags, clock (69 bits: `toc` 11, `a0` 25, `a1` 22,
  `a2` 11), IODC (10), `T_GDB2ap` (12), `ISC_B2ad` (12), BDGIM (74 bits),
  `T_GDB1Cp` (12), reserved, CRC. All figures are in 6.2.3 (Figures 6-3 to
  6-20).
- **Time** (3.3, 7.3). BDT has no leap seconds; epoch 2006-01-01 00:00:00
  UTC. `SOW` (18 bits, scale 3 s, 0–604797) is the time of the rising edge of
  the first chip of the frame preamble. `WN` is the BDT week (13 bits, type
  10 only). `BDT = GPST − 14 s` (derived; BDSBAS ICD agrees).
- **Clock** (7.5). `Δt_sv = a0 + a1 (t − toc) + a2 (t − toc)^2 + Δt_r`,
  `Δt_r = F e sqrt(A) sin(E_k)`, `F = −2 sqrt(mu) / c^2`;
  `t = t_sv − Δt_sv`. `toc` scale 300 s; `a0` 2^-34 s, `a1` 2^-50, `a2`
  2^-66.
- **Group delay** (7.6). `a0` includes the B3I group delay (B3I is the
  reference). B2a pilot: `Δt_sv,B2ap = Δt_sv − T_GDB2ap`. B2a data:
  `Δt_sv,B2ad = Δt_sv − T_GDB2ap − ISC_B2ad`. `T_GDB1Cp` is for B1C. Scale
  2^-34 s, 12 bits two's complement.
- **Ephemeris** (7.7, Tables 7-7 to 7-9). `toe` 11 bits ×300 s, `SatType`
  2 bits (01 GEO, 10 IGSO, 11 MEO), `ΔA` 26 bits 2^-9 m (`A_ref` MEO
  27 906 100 m, IGSO/GEO 42 162 200 m), `Ȧ` 25 bits 2^-21 m/s, `Δn0` 17 bits
  2^-44 semicircles/s, `Δn0dot` 23 bits 2^-57, `M0` 33 bits 2^-32 semicircles,
  `e` 33 bits 2^-34 (unsigned), `ω`, `Ω0`, `i0` 33 bits 2^-32 semicircles,
  `Ω̇` 19 bits 2^-44 semicircles/s (**sent directly, not as a difference**),
  `i̇` 15 bits 2^-44, `Cis`, `Cic` 16 bits 2^-30, `Crs`, `Crc` 24 bits 2^-8,
  `Cus`, `Cuc` 21 bits 2^-30. Constants: `mu = 3.986004418e14 m^3/s^2`,
  `Ω̇_e = 7.2921150e-5 rad/s`, `pi = 3.1415926535898`. Algorithm: `tk = t −
  toe` folded to ±302400 s; `A0 = A_ref + ΔA`, `A_k = A0 + Ȧ tk`,
  `n0 = sqrt(mu / A0^3)`, `Δn_A = Δn0 + ½ Δn0dot tk`, corrected mean motion
  `n_A = n0 + Δn_A`; Kepler; `Ω_k = Ω0 + (Ω̇ − Ω̇_e) tk − Ω̇_e toe`; standard
  harmonic corrections; MEO/IGSO ECEF as in GPS.
- **Iono** (7.8). BDGIM: 9 coefficients (10 bits, then 8 bits each, 2^-3 TECu)
  in type 30; single-frequency algorithm in 7.8.2; dual-frequency
  (B1C + B2a) iono-free combination in 7.8.3.
- **BGTO** (7.13, type 33). `GNSS ID` (001 GPS, 010 Galileo, 011 GLONASS),
  `WN0BGTO`, `t0BGTO` (×16 s), `A0BGTO` (2^-35 s), `A1BGTO` (2^-51), `A2BGTO`
  (2^-68). `t_BDT − t_GNSS = A0 + A1 (t − t0) + A2 (t − t0)^2`.
- **Health and integrity** (7.14, 7.15): `HS` 0 = healthy; `DIF`, `SIF`,
  `AIF` flags. Use them to drop a satellite.

### 11.3 B1C facts (from the open-service ICD, BDS-SIS-ICD-B1C-1.0)

- **Satellites.** MEO and IGSO. PRN 1–63; the same PRN as B2a for one
  satellite (5.1).
- **Signal.** 1575.42 MHz, 32.736 MHz bandwidth, RHCP, 100 sps data (4.1,
  4.2.2). Complex envelope `s = s_data + j s_pilot`. **Three** components
  (Table 4-2, eq. 4-11): data, sine-phased BOC(1,1), 0°, power 1/4; pilot
  part a, BOC(1,1), **90°**, power 29/44; pilot part b, BOC(6,1), **0°**
  (in phase with the data), power 1/11. Subcarrier `sign(sin(2 pi f t))`,
  `f = 1.023 MHz` (BOC(1,1)) and `6.138 MHz` (BOC(6,1)). Logic 1 → −1.0 (4.3).
  Minimum power at the ground: −159 dBW (MEO), −161 dBW (IGSO).
- **Timing** (4.8). Same alignment rules as B2a: the first primary chip
  starts a data symbol and a secondary chip; one secondary chip lasts one
  primary period (10 ms).
- **Primary codes** (5.2.1). Truncated Weil code from length `N = 10243`:
  `W(k; w) = L(k) XOR L((k + w) mod N)`, `c(n; w, p) = W((n + p − 1) mod N;
  w)`, `n = 0..10229`; `w` in 1–5121; Legendre `L(0) = 0`, `L(k) = 1` if `k`
  is a square mod `N`, else 0. Separate `(w, p)` per PRN for data and pilot
  (Tables 5-2 and 5-3). 126 codes. 1.023 Mcps, 10230 chips, 10 ms.
- **Secondary codes** (5.2.2, Table 5-1). Data: **none**. Pilot: 1800 chips,
  truncated Weil code from `N = 3607`, `w` in 1–1803, `(w, p)` per PRN
  (Table 5-4); one chip per 10 ms, so 18 s.
- **Frame** (6.2.1). 1800 symbols at 100 sps = 18 s. Subframe 1: 72
  symbols (14 bits: PRN 6 + SOH 8). Subframe 2: 1200 symbols (600 bits).
  Subframe 3: 528 symbols (264 bits). Total 878 bits before coding. CRC-24Q
  as in B2a (6.1.2): subframe 2 CRC over its first 576 bits; subframe 3 CRC
  over PageID plus data.
- **Subframe 1 coding** (6.2.2.1, Table 6-1). The 6 MSBs (PRN) use
  BCH(21,6), `g = x^6 + x^4 + x^2 + x + 1`; the 8 LSBs (SOH) use BCH(51,8),
  `g = x^8 + x^7 + x^4 + x^3 + x^2 + x + 1`. 21 + 51 = 72 symbols. Encoder
  circuit in Figure 6-2.
- **LDPC** (6.2.2.2, 6.2.2.3, Annex). 64-ary over GF(2^6), primitive
  polynomial `1 + x + x^6`. Subframe 2: LDPC(200,100); H is 100 × 200 with 4
  non-zero elements per row (index and element tables; Figure 6-3 gives the
  reading rule). Subframe 3: LDPC(88,44); H is 44 × 88. Both systematic
  (information symbols first). Same annex as B2a (encoder, GF(64) maps,
  extended min-sum decoder; the B2a annex has the worked example).
- **Interleaver** (6.2.2.4, Figure 6-4). Array of **36 rows × 48 columns**,
  1728 symbols. Write by rows: rows 1 and 2 with subframe 2 symbols, row 3
  with subframe 3 symbols, and so on (two rows of subframe 2, one row of
  subframe 3) down to row 33; the last three rows (34–36) take the remaining
  144 subframe 2 symbols. Read by columns, top to bottom, column 1 first.
  Transmitted order: subframe 1 (72), then the 1728 interleaved symbols.
- **Subframe 2 layout** (6.2.3.2, Figure 6-6; 600 bits): WN 13, HOW 8,
  IODC 10, IODE 8, ephemeris I 203, ephemeris II 222, clock 69 (`toc` 11,
  `a0` 25, `a1` 22, `a2` 11), `T_GDB2ap` 12, `ISC_B1Cd` 12, `T_GDB1Cp` 12,
  reserved 7, CRC 24. Ephemeris I and II have the same widths and scales as
  B2a (Tables 7-7 and 7-8).
- **Subframe 3** (6.2.3.3, Figures 6-7 to 6-11; 264 bits): PageID 6, data
  234, CRC 24. Page 1 (PageID 1): HS 2, DIF/SIF/AIF, SISMAI 4, SISAIoe 5,
  SISAIoc 22, ionosphere (BDGIM) 74, BDT-UTC 97, reserved 27. Page 2: reduced
  almanac for four satellites. Page 3: SISAIoe, EOP 138, BGTO 68. Page 4:
  SISAIoc, midi almanac 156.
- **Time** (7.3). `SOH` 8 bits, scale 18 s, 0–3582, in subframe 1: the time
  of the rising edge of the first chip of **subframe 1** of the current
  frame. `HOW` (8 bits, hours of week) and `WN` (13 bits, BDT week) are in
  subframe 2. So the frame start in BDT is `HOW * 3600 + SOH * 18` s.
- **Group delay** (7.6). `a0` includes the B3I group delay. Pilot:
  `Δt_sv,B1Cp = Δt_sv − T_GDB1Cp`. Data: `Δt_sv,B1Cd = Δt_sv − T_GDB1Cp −
  ISC_B1Cd`. `T_GDB2ap` is for B2a.
- **Ionosphere, BGTO, health, integrity:** the same definitions as B2a
  (sections 7.8, 7.13 to 7.15 of both ICDs).

Not read in detail: the H matrix index and element tables for LDPC(200,100)
and LDPC(88,44) (ICD pages 22 to 25) and the Annex. Read them in Phase 8.
