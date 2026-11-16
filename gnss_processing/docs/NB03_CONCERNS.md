# Notebook 03: concerns, gaps and risks (first draft)

Status: 2026-11-12. This list goes with the first draft of
`notebooks/03-open-loop-reflection-tracking.ipynb` and `utils/openloop/`.
The plan is `docs/NB03_OPEN_LOOP_PLAN.md`. The decisions are D-087 to D-101.

**Read this first.** No reflection recording exists yet. All of the code was
checked only against synthetic signals (`utils/openloop/synthetic.py`) and the
unit tests (`tests/test_openloop.py`). The real-data path in the notebook is
written, but it has never run. Treat every result as "correct in theory" until
the static test over water (plan phase 8) is done.

Each concern has an ID (C-n), the risk, what the draft does now, and what to do.
The most important ones are first.

## Summary table

| ID | Concern | Severity | Blocks |
| --- | --- | --- | --- |
| C-1 | Only synthetic data; the synthetic world is simple | High | Trust in every result |
| C-2 | Notebook 02 does not save its time anchors and clock series | High | Real-data run |
| C-3 | The direct reference makes assumptions about the notebook 01 output | High | Real-data run |
| C-4 | The start offset between the zenith and nadir files is not known | High | Real-data run |
| C-5 | Attitude comes from a CSV export; lever arms are not measured | Medium | Real-data run |
| C-6 | The troposphere excess term and the GPT3 reader are not verified | Medium | Low-elevation altimetry |
| C-7 | NeQuick-G: works through the `nequick` package; install needs a compiler and a DLL exception | Low | Absolute mode only |
| C-8 | Plan section 6.2 overstates the error of the image solution | Low (good news) | Plan text |
| C-9 | The leakage estimate has a bias; removal is not done | Medium | L1-group phase |
| C-10 | PROJ grids: cache location and network access | Low | Offline runs |
| C-11 | SCANF adaptations are not tuned on real data | Medium | Phase quality |
| C-12 | Satellite PCO and constellation frame transformations are missing | Low | Absolute mode |
| C-13 | Model rate and drone vibration | Medium | Phase on a real drone |
| C-14 | Hardware-delay calibration from one DDM peak | Medium | Code observable |
| C-15 | Plan items not built in this draft | Info | Later phases |
| C-16 | Run time | Low | Long flights |
| C-17 | Closed: the SCANF slip counter counted phase wraps as slips | Closed | Slip statistics |

---

## C-1. Only synthetic data; the synthetic world is simple

Risk: the code can be correct for the synthetic signal and wrong for a real one.

The synthetic world (`synthetic.SyntheticWorld`) has:

- one satellite on a straight line (not an orbit), over at most a few seconds;
- a flat, smooth surface on the ellipsoid (no geoid, no waves, no diffuse scattering);
- a constant reflection amplitude (no fading), and a constant reflection phase;
- a level drone (no attitude), with the zenith antenna straight above the nadir antenna;
- a perfect direct reference (the truth, as if notebook 01 had no tracking error);
- one exact sample clock for both channels (no LO phase drift, no quantisation, no filter).

The truth excess path comes from an independent numerical minimisation (Fermat),
not from the solver it checks. That part of the check is strong.

What to do:

1. Run the notebook on a static test over water first (a bridge, as in Bai 2012,
   or the `Ground_Test_Sim` setup) with a surveyed antenna position.
2. Then add to the generator: amplitude fading, a diffuse (Doppler-spread)
   part, an attitude time series, and a front-end filter with 8-bit quantisation.

## C-2. Notebook 02 does not save its time anchors and clock series

Risk: D-100 says notebook 03 uses the notebook 02 time anchors and receiver clock.
Notebook 02 prints them but does not write them to a file.

What the draft does: `utils/openloop/timebase.py` reads a small YAML file with
`uptime_anchor_ms`, `gpst_anchor_s`, `clock_drift_s_per_s` and
`clock_offset_at_anchor_s`, written by hand. The config key is
`time.timebase_file`.

What to do: add one cell at the end of notebook 02 that writes this YAML file
(and the full clock series) next to the tracking file. In Mode A, 1 ms of time
accuracy is enough (plan 3.3), so a linear map is adequate.

## C-3. The direct reference makes assumptions about the notebook 01 output

`direct_ref.from_tracking_result` assumes:

1. Each epoch starts on a code-period boundary, and `floor(code_phase_ms)` is the
   code-period index. The tracking channel does this once its grid is anchored
   (`bit_synced`). Epochs before that are used as they are.
2. The overlay index of period p is `p mod L`. `phase.search_overlay_offset`
   finds the offset by a search when this is not true. It is a brute-force search
   over L offsets at one lag; it is correct for a pilot, but it is slow for long
   overlays (L1CO is 1800 chips, B1C pilot 1800 chips).
3. The data sign of an epoch comes from the projection of the prompt on its
   long-term axis. One sign fills all code periods of the epoch. **This is wrong
   when one epoch spans more than one data symbol** (for example a 20 ms epoch on
   L5I, whose symbols are 10 ms). Use the pilot for the phase where there is one
   (the notebook uses L5Q for L5). For data components, the epoch must not be
   longer than the symbol.
4. Low direct C/N0 gives wrong data signs. A wrong sign is a half-cycle jump in
   the reflected phase. There is no check for this yet.

What to do: test `from_tracking_result` on a real notebook 01 file of the zenith
channel. Compare its symbol table with the decoded navigation bits of notebook 02.

## C-4. The start offset between the zenith and nadir files is not known

Risk: the zenith channel (B200) and the nadir channels (B210) are separate
recordings. All four SDRs set their time at the same PPS edge (D-025). It is not
confirmed that the files start on the same sample. A whole-ms offset puts the
reflection outside the correlation window. A sub-sample offset goes into the
hardware delay tau_hw (that part is calibrated in section 9).

What the draft does: the notebook has `NADIR_MINUS_ZENITH_START_S` (section 2.3),
default 0.

What to do: make the recorder write the device time of the first sample of each
file in `metadata.yml`. Read it in notebook 03 instead of the manual value.

## C-5. Attitude comes from a CSV export; lever arms are not measured

- The ArduPilot binary log (`.bin`) needs `pymavlink` to read. That is not a
  dependency. The draft reads CSV exports of the `ATT` (or `XKF1`) and `GPS`
  messages (`attitude.read_ardupilot_csv`). The time tie uses a straight line
  through `TimeUS` against `GWk`/`GMS` of the GPS messages; the notebook shows the
  RMS of that fit. The autopilot GPS is the Here3 (D-087).
- The body rates come from finite differences of linearly interpolated Euler
  angles. They are piecewise constant between log records.
- The lever arms are null (D-091). The notebook stops in real mode until they are
  set. The lever-arm code is checked by unit tests only.
- The PCO is chosen per band through a global `BAND_NAME` in notebook section 4.
  This works but is not clean; move the band into `rx_tracks` when it is reworked.

## C-6. The troposphere excess term and the GPT3 reader are not verified

- `atmosphere.excess_tropo_first_order_m` is the first-order formula of plan 7.2
  (2e-6 N0 h / sin e, no ray bending). It gives about 3 cm of excess path at
  30 m height and 35 deg elevation, and up to decimetres at low elevation. It is
  the largest model term for low-elevation altimetry. **Check it against a
  ray-traced value** (GNSS-IR literature, for example Nievinski and Larson, or
  Williams and Nievinski 2017) before the height results are trusted.
- `atmosphere.GPT3Tropo` reads the grid by the column names in the file header.
  It uses the nearest grid node (no bilinear interpolation), a simple pressure
  reduction, and Saastamoinen with the GPT3 surface values. The GPT3/VMF3 mapping
  functions are not ported. It was tested on a small synthetic grid only; the
  real `gpt3_5.grd` was not downloaded. Compare it with the official GPT3 code
  (TU Wien) at a few sites before use.

## C-7. NeQuick-G works through the `nequick` package; the install is not simple (D-095, D-102)

Risk: D-095 makes NeQuick-G the rapid ionosphere model for all constellations.

What the code does now (D-102): `atmosphere.nequick_jrc_backend` calls the EU
JRC reference C library through the PyPI package `nequick` (1.0.0).
`NeQuickGIono.jrc(az_coefficients, gps_minus_utc_s)` builds the model. The
adapter converts ECEF to WGS-84 (longitude, latitude, height) and GPST to UTC
with `models.gps_minus_utc_s`. A test with a fake library checks the argument
order, the time and the 40.3 STEC / f^2 step.

Validation (plan test T-10, 2026-11-12): the Galileo validation rays (high,
mid and low solar activity: 108 rays in `tests/data/nequick_g/`, 576 with
the expanded tables) agree with the reference STEC within 0.11 TECU through the
full adapter path. Only one ray (mid, month 4, UT 16) is above 0.02 TECU; the
library alone gives the same value there. The package's own tests allow
0.2 TECU, and `test_nequick_jrc_matches_validation_tables` uses the same.

Open problems:

1. **Install on Windows.** PyPI has only source releases, so the install needs
   a C compiler (MSVC Build Tools). On this machine, Windows Application Control
   also blocked the new unsigned DLL `_c_ext.cp313-win_amd64.pyd` until the user
   allowed it. Each team member's machine needs the same steps. Without the
   package, the tests that need it are skipped.
2. **The package pins `numpy<2.0`.** gnss_processing uses numpy 2.x. The C
   extension does not use numpy (only the `to_gim` helper does), so
   `pip install nequick --no-deps` is safe for this code. The pin also stops
   `nequick` from going into `pyproject.toml` as a normal dependency.
3. **The package README gives the wrong argument order.** It shows
   `compute_stec(epoch, lat, lon, alt, ...)`. The C binding passes the values to
   the JRC function `set_receiver_position(lon, lat, h)`. The adapter uses
   (lon, lat, h), from the C source. The validation tables confirm it.
4. **The binding drops fractions of a second** from the time. This is far below
   the model error.
5. **Az source:** the Galileo ai0, ai1, ai2 must come from a navigation message
   or the RINEX 3 navigation header. No reader for them is connected to
   notebook 03 yet (the D-095 note).

Impact now: none on Mode A. The excess ionosphere of the reflected path is zero
(plan 7.1), so the direct-referenced model does not use any ionosphere model.
Only the absolute mode (Mode B) and a direct-path position need one. Klobuchar
and IONEX (single layer) work now.

## C-8. Plan section 6.2 overstates the error of the image solution

Finding: the plan says a flat surface is wrong by d^2 / (2 R_E) (0.10 m at 100 m
and 5 deg). That is true for **one** plane through the point under the drone
(test `test_single_pass_plane_shows_curvature_error`). The draft's
`specular.image_plane` makes a second pass with the plane tangent at the first
specular point. Because the path is stationary at the specular point, that
solution matches the Fermat truth to well under 1 mm at 100 m and 5 deg (test
`test_specular_solvers_match_fermat_truth`).

So the rapid solver is also accurate enough for carrier-phase work over a smooth
surface. The iterative solver still matters for a DEM or a sloped surface.

Also: the iterative solver stops at a Snell residual near 1e-7 rad, a numerical
floor of the geodetic conversions. The path is then correct to micrometres.

What to do: update plan 6.2 with this result.

## C-9. The leakage estimate has a bias; removal is not done (D-098)

- `leakage.estimate_beta` estimates the coupling beta by least squares. The
  reflection itself leaks into the estimate. The bias is about
  (A_reflection / A_zenith) x `diversity`, where
  `diversity = |mean exp(j 2 pi D / lambda)|` over the window.
- A drone that moves up and down spends time at the turning points. Then
  `diversity` decays more slowly than 1 / (number of phase turns). The draft first
  used the turn count only, and the test showed the bias. The flag `separable`
  now needs both enough turns and a low `diversity`.
- The leakage is not removed (D-098). For the L1 group it lies within about
  0.2 chip of the reflection, so it biases the retracked code delay and adds an
  interference term to the reflected phase.
- In real mode, notebook section 12.1 stops with `NotImplementedError`: the
  zenith file must be run through `correlator.run_open_loop_file` with the
  reflected model. This is a small step, but it needs C-4 first.

## C-10. PROJ grids: cache location and network access (D-094)

- `pyproj` is added to `pyproject.toml` and `uv.lock`. It was also installed in
  the `gps-tracking-example/.venv` environment, which the notebooks use on this
  Windows machine (`run-gnss-notebooks` skill). That is an environment change
  outside this folder.
- `surface.GeoidGrid` turns on PROJ network access, so PROJ downloads the grid
  tiles it needs from cdn.proj.org. It caches them in the PROJ user directory,
  **not in RESOURCES_PATH** as D-094 says. An offline machine fails unless the
  grids are already there.
- The hand-built pipelines agree with PROJ's registered transformations
  "NAD83(2011) to NAVD88 height" and "WGS 84 to EGM2008 height" to 1 cm (test
  `test_geoid_grids_match_official_proj_pipelines`). At Boulder: GEOID18
  N = -15.88 m, EGM2008 N = -16.05 m, and ITRF2014 to NAD83(2011) adds +0.85 m of
  ellipsoidal height. A check against a published NGS benchmark (plan T-11) is
  still to do.

## C-11. SCANF adaptations are not tuned on real data

The filter (`phase.scanf`) follows Wang et al. 2021 with these changes:

- One non-dispersive state (residual and rate) for all bands, because the excess
  ionosphere is zero (plan 7.1).
- A constant offset per band, set from the first epoch. A slip or a bad epoch at
  the start biases that offset.
- The integer slip search is per band (the measurement covariance is diagonal).
  There is no explicit wide-lane check yet; a slip on one band is found because
  the other band holds the common state.
- Epochs under `phase.min_cn0_dbhz` get a very large variance, so the filter
  coasts through a fade.
- The process noise is one acceleration value (`phase.process_accel_mps2`). It is
  not tuned on drone motion.
- The C/N0 estimator uses one median noise floor per run, from the noise lags.

The T-6 test once injected a slip of exactly 2 pi, which a wrapped phase cannot
show (C-17). It now checks a slow residual, and a second test checks a residual
that moves more than half a cycle per epoch. Real fading, diffuse scattering and
data-sign errors (C-3) are not tested.

Real-data result (2026-11-12, `20261013_Jiawei_Testing_x2`, G14, static, no
reflection, 30 s): the direct signal was tracked open loop with a deliberately
wrong model, D~ = 40 + 5 sin(2 pi t / 20 s) m, while the true D is 0. The
filter state r must then follow -D~, which accelerates up to 0.49 m/s^2. With
the config value `process_accel_mps2 = 0.05` the filter lags and leaves a
smooth error of 4.5 mm RMS on L1 and 3.3 mm RMS on L5. With 1.0 m/s^2 the
error is 0.85 mm and 0.80 mm RMS, which is the noise level. So the value must
match the expected acceleration of the model error (lever arm, attitude,
trajectory), not of the drone. Tune it on flight data.

Limit seen on the same data: with D~ changing at up to 6.3 m/s, L1 (20 ms
coherent) failed with 1162 slip events, because the residual moved more than
lambda / (2 T) = 4.8 m/s. L5 (limit 6.4 m/s) held with 5 corrected slips and
2.4 mm RMS. A model whose excess-path rate is wrong by more than lambda / (2 T)
cannot be repaired by the filter; use a shorter coherent time for that case.

## C-12. Satellite PCO and constellation frame transformations are missing

- `sat_states.PreciseProvider` does not apply the satellite antenna PCO from
  ANTEX. SP3 positions are centre-of-mass positions. The PCO is up to 1-2 m and
  would bias the absolute mode; it cancels in Mode A to below 1 mm.
- The D-092 TODO (GTRF for Galileo, BDCS/CGCS2000 for BeiDou, to ITRF) has a hook,
  `BroadcastProvider.frame_offset_m`, set to zero. The frames agree with ITRF to a
  few cm, which is below 1 mm in Mode A.
- `BroadcastProvider` was tested only with a stub ephemeris (BDT = GPST - 14 s).
  The real-data path in the notebook builds GPS ephemerides only.

## C-13. Model rate and drone vibration

The geometry is evaluated at `openloop.model_rate_hz` (50 Hz) and interpolated
with a cubic spline. Motion of the nadir antenna above about 25 Hz (motor and
propeller vibration) is not in the model; it goes into the residual phase. At L1
a vibration of 1 mm amplitude is about 0.03 rad of phase; 1 cm is about 0.3 rad.
The PPK trajectory (Here3, rate not known, D-087) and the attitude log limit the
model bandwidth further.

What to do: measure the vibration of the antenna mount, and look at the residual
phase spectrum of the first real test.

## C-14. Hardware-delay calibration from one DDM peak

`acquisition.estimate_tau_hw_s` takes the refined DDM peak. Risks:

- **No simple peak refinement is exact for every case** (found 2026-11-12, D-103).
  `acquisition.peak_method` selects one of two:
  - `parabola` (default): through the top three lags of the power. For the
    sharp L1 C/A triangle it is biased by where the peak sits between two lags:
    up to 0.026 chip (about 26 ns) on a 0.25-chip grid (seen in the x2 open-loop
    demonstration) and up to 0.004 chip on the 0.05-chip grid of the config.
  - `early_late`: A(x - d) = A(x + d) on the amplitude, with
    d = `acquisition.peak_half_spacing_chips`. Exact for any symmetric peak (no
    grid bias), but a second path near the peak makes the two slopes unequal
    and pulls it.

  Worst-case error (chip) with a model of the L1 C/A triangle, the peak at any
  place between two lags:

  | Case                                  | parabola | early_late (d = 0.5) |
  |---------------------------------------|----------|----------------------|
  | sharp peak, 0.25-chip grid            | 0.026    | 0.000                |
  | sharp peak, 0.05-chip grid            | 0.004    | 0.000                |
  | rounded peak (L5-like), 0.05 grid     | 0.000    | 0.000                |
  | + leakage 0.3 at -0.117 chip, sharp   | 0.016    | 0.050                |
  | + leakage 0.3 at -0.117 chip, rounded | 0.030    | 0.050                |

  In the synthetic notebook (L1 leakage at 0.3 and -0.117 chip) the parabola
  gave 29.6 ns and early_late 18.8 ns for a 30 ns truth. Real L1 C/A data will
  probably have leakage (C-9), so the default is the parabola with a fine lag
  step. The proper fix is a two-path fit (reflection and leakage triangles with
  the known separation from the model), not built yet.
- For L1 C/A the direct leakage peak is within about 0.2 chip of the reflection
  and pulls the peak (C-9), with either method (table above).
- Diffuse scattering makes the waveform asymmetric and moves the peak late.
- One satellite gives one value; use several high-SNR satellites and look at the
  spread (a large spread is a geometry error, not a hardware delay).
- The detection threshold assumes independent lags. Lags closer than one chip are
  correlated, so the threshold is conservative.

The synthetic DDM test (no leakage) recovers a quarter-chip delay to 0.03 chip
with both methods. A unit test slides the peak across a full grid step for
`early_late_peak_lag`.

## C-15. Plan items not built in this draft

- Trajectory adapters for UBX in-flight RTK, generic CSV, and the notebook 02 SPP
  solution (D-088: later work).
- Antenna PCV and gain patterns (D-090: after Q-017).
- A DEM surface for land, and a gauge time series reader (D-093: the gauge is not
  chosen). The surface uses one water level from config.
- The second OL pass (plan 10.5): `ReflectionModel.excess_bias_m` is the hook, but
  no fit drives it yet.
- Mode B carrier phase: only the Mode B code phase is computed.
- Retrackers other than the parabola and early-late equality on the peak (a two-path
  fit with the leakage (C-14), leading edge, model fit, the
  learned retracker of Yang 2025).
- Notebook 03 in the pipeline window (plan phase 9).
- RHCP and LHCP together: the notebook runs one nadir channel per run
  (`NADIR_POLARIZATION`). The D-041 comparison needs two runs and a plot that joins
  them.

## C-16. Run time

The OL correlator calls the existing per-sample kernel once per block, for every
lag. The cost grows with sample rate x lags. The synthetic notebook run (2 s of L1
at 4 Msps and 2 s of L5 at 20.46 Msps, about 190 and 230 lags) shows the time per
section. A flight of several minutes at 20 Msps with 200+ lags needs either fewer
lags (D-099 sizes the window from the height), a coarser lag step for the
waveform, or a parallel run per satellite (the kernel releases the GIL).

## C-17. Closed: the SCANF slip counter counted phase wraps as slips

Symptom: in the synthetic notebook run, `phase.scanf` reported 30 slip events on
L5 and none on L1. The L5 residual phase was steady, and the excess path agreed
with the truth to 2.2 mm RMS.

Cause: the filter set a slip event each time the integer m_i changed. But m_i
also changes at each normal wrap of w_i through +-pi. The constant phase of the
L5 band in that run was near +-pi, so the noise made w_i cross the boundary in
both directions, and each crossing was counted. The phase and the excess path
were correct. Only the count was wrong.

Fix: a slip event is now an epoch where the filter chooses an integer that is
different from the integer that plain continuity with the last good epoch gives.
A normal wrap is not an event. The notebook run now reports 0 slips on both
bands, with the same 2.21 mm RMS error.

The test `test_scanf_dual_frequency_repairs_injected_slips` also had a fault:
it added a step of exactly 2 pi to the phase before it wrapped the phase. That
step does not change a wrapped phase, so the test did not contain a slip. The
test now asserts that it gives no slip events. The new test
`test_scanf_rate_state_repairs_unwrap_slips` gives a real slip case: a residual
that accelerates to more than half a cycle per epoch. Plain unwrapping fails by
more than 1 m there. The filter follows the residual within 15 mm, and the
slip events start where the L1 step passes half a cycle.

Note for real data: a slip event now means "the filter disagreed with plain
unwrapping". With a good model the residual rate is small, so events must be
rare. Many events show a model error (for example, a bad lever arm or a bad
trajectory) or a low C/N0.

Fixed during the draft (for the record): each coherent sum was first labelled with
the start time of its first block, while its model value was the mean over the
sum. For a climbing drone that put about 9 mm of error into the excess path.
`phase.coherent_series` now labels each sum with its centre time.

## C-18. Closed: clock signs, Mode B and the ionosphere sign (D-105)

Symptom: a check of the models against the reference signal model (B. Breitsch,
"GNSS Signal Modeling", 2025; `docs/references/gnss_signal_modeling/`) found
three faults. All three were outside Mode A's direct-referenced path, so no
Mode A result changed.

1. The synthetic generator added the receiver clock with the sign of a
   transmitter clock (code = T + dt - tau). In the reference, a receiver clock
   that is ahead of GPS time makes the code look later: eta = T - dt_rx - tau.
   The geometry was also computed at the sample stamp, not at the true time.
2. Mode B added dt_rx after `TimeBase.gpst` had already removed it. Faults 1
   and 2 cancelled in the synthetic Mode B test. On real data, Mode A minus
   Mode B would keep a slope equal to the clock drift (up to 0.5 us/s, or
   150 m/s, for the free-running TCXO). The notebook also passed the EXCESS
   troposphere to Mode B, which needs the full delay of the path.
3. Mode A used one excess path for the code and the carrier. The ionosphere
   delays the code and advances the carrier.

Fix: the synthetic world has separate receiver and satellite clocks with the
D-105 signs, and gives its own `TimeBase`. Mode B has no dt_rx term, takes the
full path delays and a satellite group delay, and raises when the TimeBase does
not match the geometry. The model keeps a group excess (code) and a phase
excess (carrier, Doppler); `OpenLoopOutput.model_excess_phase_m` carries the
phase excess to the phase and leakage processing. New tests: the two clock
signs, Mode B with drifting receiver and satellite clocks, and the ionosphere
signs.

With the same change: the DDM peak fits took the lag step from the first two
lags. With the noise lags first in the array, the step was wrong by the ratio
of the grids (ten times on the ARGO open-loop run of 2026-11-14). They now use
the step beside the peak.

Still open: the full ionosphere of the reflected path is not modelled in the
notebook's Mode B cell, so its slow change stays in Mode A minus Mode B on
real data. The Mode B carrier phase is not built (C-15).
