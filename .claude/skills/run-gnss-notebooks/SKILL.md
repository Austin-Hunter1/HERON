---
name: run-gnss-notebooks
description: Run gnss_processing's notebooks 00 (raw-signal diagnostics), 01 (acquisition and tracking), or 02 (navigation) against a collect under COLLECTS_PATH, from the command line, without opening Jupyter. Use this whenever the user wants a raw USRP/B2x0 collect checked, acquired/tracked, or navigated -- including "run notebook 00/01/02 on <collect>", "check if this collect is usable", "track <signal> on <collect>", or "get a position fix for <collect>".
---

# Running gnss_processing's notebooks headlessly

`gnss_processing/notebooks/00-raw-signal-diagnostics.ipynb`,
`01-acquisition-and-tracking.ipynb`, and `02-navigation.ipynb` are meant to be
opened in Jupyter and have a handful of variables near the top hand-edited, then
re-run. `scripts/run_notebook.py` (in this repo, next to this skill) does that
same edit mechanically from the command line, so a notebook can be driven
without a UI:

```bash
cd /media/austin/PhD_Workspace/03_Projects/UAS_GNSS-R_HERON/00_git/gnss_processing
python3 scripts/run_notebook.py 00 --set EXPERIMENT_INDEX=1 --set CHANNEL_INDEX=0
```

It writes the patched notebook to `local-data/notebook_runs/<nb>_last_run.ipynb`,
prints every text/stdout cell output, and reports any cell that raised an error
(exit code 1 if so). Figures and tracking files land in their normal project
locations (see each notebook's section below) -- the run output notebook itself
is just a log, not a deliverable.

## Before the first run

1. **The `gnss_tracking_linux` Jupyter kernel must exist.** It is registered at
   `~/.local/share/jupyter/kernels/gnss_tracking_linux/kernel.json` and points at
   `gps-tracking-example/.venv-linux/bin/python3` (a sibling repo's venv -- it
   has the scientific-python + Jupyter stack this project also needs, and
   predates `gnss_processing` having its own). Check with:
   `python3 -m jupyter kernelspec list`. If it is missing, either build
   `gnss_processing`'s own environment per its README (poetry) and register that
   kernel instead, or point `--kernel` at whatever venv has `numpy`, `scipy`,
   `matplotlib`, `tqdm`, `pyyaml`, `h5py`, `ipykernel` and this repo's
   `submodules/gnss-tools` installed.
2. **`gnss_processing/.env` must hold paths that exist on this machine.**
   `COLLECTS_PATH`, `OUTPUTS_PATH`, `RESOURCES_PATH` point at
   `/media/austin/PhD_Workspace/01_Data/{collects,outputs,resources}` as of
   2026-10-08 -- confirm with `cat .env` if runs start raising
   `FileNotFoundError` for a path that looks like `D:\...` or similar; that
   means the `.env` reverted to another machine's values and needs the same fix
   applied again.
3. **A raw collect needs its experiment's `metadata.yml`** in the processing
   team's schema (`band_configurations` / `channel_configurations` /
   `collections`) before `catalog.list_channels` -- and so every notebook here
   -- can see it at all. `heron_onboard`-produced flights get this
   automatically (D-022 in `docs/DECISIONS.md`); a one-off USRP ground-test
   directory (its own per-collect YAML sidecar, no `metadata.yml`) needs one
   hand-translated first, following D-029 and D-030 in the same file (exact
   recipe: band centre frequency from the sidecar's `radio_profile.freq_hz`
   with `inter_freq: 0.0`, sample format from `sample_type`, collection ids
   renamed `<UTC date>_<UTC time>_<band>_<antenna>_<duration>s` taken from the
   `.dat` filename -- not the sidecar's local-time `start_time` -- and the
   byte count checked against `samp_rate * bytes_per_sample * duration`
   before trusting the translation).

## Why `run_notebook.py` injects a `sys.path` line

nbconvert's execute preprocessor runs the kernel with its working directory set
to wherever the patched notebook copy was written, not this repo's root. A
human opening these notebooks in an editor configured to use the repo root as
cwd gets `import utils` resolving to `gnss_processing/utils/` for free; a
headless nbconvert run does not, and will silently resolve `utils` to
`gps-tracking-example`'s own installed copy instead (same package name,
different repo). That matters: `gnss_processing/utils/signal_interfaces.py` and
`tracking_supervisor.py` carry real HERON-specific edits documented in this
repo's `README.md`, and notebooks 01/02 use both. `run_notebook.py` prepends
this repo's root to `sys.path` in an injected first cell so `import utils`
always resolves here, regardless of where nbconvert decides to run the kernel.
If you ever see `utils/signal_interfaces.py` warnings/behavior referencing a
`gps-tracking-example` path rather than `gnss_processing`, that guard broke --
check the injected cell is still cell 0 of the run.

## Notebook 00 · raw-signal diagnostics

Answers "is this collect usable?" -- sample histogram, PSD, and a full-file
spectrogram, saved as PNGs beside the collect
(`<collects_dir>/<experiment>/Figures/<collect_id>/`).

```bash
python3 scripts/run_notebook.py 00 --set EXPERIMENT_INDEX=<n> --set CHANNEL_INDEX=<n>
```

`EXPERIMENT_INDEX`/`CHANNEL_INDEX` are not something to guess: notebook 00's
output prints the full catalog (every experiment, every channel within it) as
a table before anything else runs, with the exact indices in its `exp`/`ch`
columns. Run it once with throwaway indices (e.g. `0 0`) just to read that
table if the right indices aren't already known, then re-run with the real
ones. **Read `run_notebook.py`'s own stdout, not just its exit code** --
errors from a bad index (e.g. index out of range) show up as a cell traceback,
not a nonzero exit before that point.

What "usable" looks like in the output figures: a Gaussian, zero-mean I/Q
histogram and a round constellation scatter well inside the format's rails (a
bit of pile-up right at the rail edge is a clipping/AGC-too-hot warning sign,
not automatically disqualifying); a steady horizontal band in the spectrogram
with no persistent thin line (CW interference) or vertical stripes (dropped
samples).

## Notebook 01 · acquisition and tracking

Despreads one band of one channel for one GPS signal, writing a tracking `.h5`
file under `<outputs_path>/tracking/<experiment>/`.

```bash
python3 scripts/run_notebook.py 01 \
  --set SIGNAL_ID=GpsL1CA \
  --set EXPERIMENT_INDEX=<n> --set CHANNEL_INDEX=<n> \
  --set START_OFFSET_MS=0.0
```

`SIGNAL_ID` is one of `GpsL1CA`, `GpsL2C`, `GpsL5`, `GpsL1C`, `BdsB2a`,
`BdsB1C` (bareword, no quotes -- it's a class reference, not a string). The
band decides which collects can be used: `GpsL1CA`, `GpsL1C` and `BdsB1C` are
on L1; `GpsL2C` is on L2; `GpsL5` and `BdsB2a` are on L5. BeiDou satellites
are `C01`..`C63`; a B1C PRN and its B2a PRN name the same satellite. B2a needs
about 20 MHz of sample bandwidth; B1C (narrow-band model) needs about 4-5 MHz.
Tracking files made for `BdsB2a` before the symbol-grid fix of D-059 are not
valid -- track again. `EXPERIMENT_INDEX`/
`CHANNEL_INDEX` here index notebook 01's own catalog printout, which is
filtered to channels carrying that signal's band -- re-run with throwaway
values first to read it, same as notebook 00. This is the slow notebook (it
walks the acquisition search grid then tracks to the end of the file); give it
a generous `--timeout` for a full-length collect.

## Notebook 02 · navigation

Reads one or more tracking runs notebook 01 produced and computes a position
fix, writing plots and a solution file.

```bash
python3 scripts/run_notebook.py 02 \
  --set SIGNAL_ID=GpsL1CA \
  --set EXPERIMENT_INDEX=<n> --set CHANNEL_INDEX=<n>
```

Its `EXPERIMENT_INDEX`/`CHANNEL_INDEX` index the *tracking* catalog (what
notebook 01 has already written), not the raw-collect catalog -- these are not
the same indices used in notebooks 00/01 for the same physical collect. Read
notebook 02's own printed table the same way. It also takes
`RECEIVER_REFERENCE_GEO` (an a-priori lat/lon/height tuple) and
`TRACK_DURATION_MS`; leave them at the notebook's own defaults unless the user
gives a real receiver position or wants a shorter window.

### BeiDou in notebook 02

`SIGNAL_ID=BdsB2a` and `SIGNAL_ID=BdsB1C` need no broadcast file and no
download: the signal carries its own ephemeris (B-CNAV2 for B2a, B-CNAV1 for
B1C), in BeiDou time. The tracking run must be long enough to decode it: 30 s
for B2a (frames of 3 s, three message types) and 40 s for B1C (one frame is
18 s). Notebook 02 prints a table of what decoded; a satellite with no frame
is listed under "Not decoded". No ionosphere correction is applied to BeiDou
(D-053). Section 10 of notebook 02 joins a GPS L5 run and a BeiDou run into
one fix with an inter-system bias; set `COMBINED_ENABLED=True` for it, and it
needs one notebook-01 tracking file for each signal, made with the same
`START_OFFSET_MS` and `TRACK_DURATION_MS`.

To compare decoded BeiDou orbits with precise orbits (needs CDDIS access):
`python3 scripts/compare_bds_orbits.py <tracking.h5> --date YYYY-MM-DD`.

## Windows

`run_notebook.py` defaults are Linux ones: the kernel `gnss_tracking_linux` and
the interpreter `gps-tracking-example/.venv-linux/bin/python3`. On another
system pass `--python <interpreter>` and `--kernel <kernelspec name>`.
`.env` may hold one path for each platform (`COLLECTS_PATH_WINDOWS` and so
on; see `utils/environment_variables.py`). The exact Windows recipe (which
environment, which kernel) is **not confirmed by the user yet**; ask before
writing it down here.

## If a run fails

- `nbconvert failed` with `NoSuchKernel`: the `--kernel` name does not match an
  installed kernelspec. List them with `python3 -m jupyter kernelspec list`.
- A cell traceback mentioning `D:\...` or any path that does not exist on this
  machine: `.env` has stale values from another machine -- see step 2 above.
- A cell traceback from `catalog.select_channel` about an index: re-run with
  throwaway indices to print the catalog and read the real ones off it.
- A cell traceback inside `gps-tracking-example/utils/...` rather than
  `gnss_processing/utils/...`: the injected `sys.path` cell did not end up
  first -- check `local-data/notebook_runs/<nb>_patched.ipynb`'s cell 0.
