# HERON GNSS signal processing course

An interactive lecture page (about 90 minutes) for the HERON team. It
goes from trilateration to GNSS-R geometry, with live figures and the
real tracking and acquisition results of the
`20261013_Jiawei_Testing_x2` collect.

## Open the course

Open `gnss_course.html` in a browser. The file is self-contained: it
holds the styles, KaTeX, the fonts for the equations, and the HERON
data. It works with no network. With a network, the page also loads
its text fonts from Google Fonts.

Keyboard: press `P` for present mode (larger type, a timer, arrow keys
step from panel to panel). Press `Esc` to leave present mode.

## Parts

| Part | Subject | Minutes |
|---|---|---|
| 01 | Trilateration: forest chimes, then satellites; why 4 satellites | 12 |
| 02 | What a working GNSS needs | 4 |
| 03 | Carrier × code × data, L1 and L5 | 6 |
| 04 | Gold codes, CDMA, correlation gain, FFT correlation | 10 |
| 05 | Navigation message to position fix | 6 |
| 06 | Acquisition: search space, threshold, coarse and fine | 12 |
| 07 | Tracking: DLL, FLL, PLL, live loop simulation, HERON data | 18 |
| 08 | BPSK, BOC, MBOC; HERON signal plan; school-bus scale | 8 |
| 09 | Pseudorange and carrier models; dual-frequency correction | 6 |
| 10 | GNSS-R geometry, Fresnel zone, open-loop error model | 8 |

## Edit and rebuild

Edit the sources in `src/`, then run (standard library only):

```bash
python docs/teaching/build.py
```

- `src/index.html` — page template.
- `src/css/course.css` — styles and colour tokens (light and dark).
- `src/js/core.js` — shared plotting, controls, C/A code generator.
- `src/sections/sNN.html` + `src/js/sNN.js` — one pair per part.
  `build.py` lists the parts and their order in `PARTS`.
- `src/vendor/katex/` — KaTeX 0.16.9 (MIT licence).
- `src/data/heron_data.json` — decimated HERON data, made by the tool below.

## Refresh the HERON data

`tools/extract_heron_data.py` reads the tracking HDF5 files and report
figures, picks the strongest satellite for each signal, and writes
`src/data/heron_data.json`. Run it in the `gnss_processing`
environment (it needs `h5py` and Pillow):

```bash
cd gnss_processing
uv run python ../docs/teaching/tools/extract_heron_data.py --tracking-dir D:/01_Data/outputs/tracking/20261013_Jiawei_Testing_x2 --reports-dir D:/01_Data/outputs/reports/20261013_Jiawei_Testing_x2
```

Then run `build.py` again.

## Notes on the models

- Simulations are for teaching. They use simple, stated models (see the
  docstring at the top of each `src/js/sNN.js`).
- Received power levels in Part 08 and term sizes in Part 09 are
  approximate textbook and ICD values.
- The range change in the Part 07 viewer comes from the integrated
  Doppler, because the stored `carr_phase_cycles` resets once when the
  loop changes its epoch length.
