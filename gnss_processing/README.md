
## About this copy

This is HERON's own working copy of [cu-sense-lab/gps-tracking-example](https://github.com/cu-sense-lab/gps-tracking-example),
kept here (rather than as a submodule pointing at upstream) because this project's
GitHub account does not have write access to push changes there. It carries edits
made for HERON's UAS GNSS-R work that are not upstream:

- `utils/signal_interfaces.py` -- guards `GpsL1C` registration against the
  `submodules/gnss-tools` checkout here not yet defining the L1C constants it needs,
  so `import utils` degrades gracefully (with a warning) instead of crashing.
- `utils/nav/symbols.py` -- `_usable_regions` (replacing `_usable_slice`) correctly
  extracts navigation symbols from a tracking run stitched from several segments,
  rather than assuming one uninterrupted run.
- `utils/tracking_supervisor.py` (new) -- detects a tracking channel losing lock
  (e.g. from a USRP overflow dropping samples mid-recording), and recovers it with
  a narrow, Doppler/code-phase-aided re-acquisition instead of leaving the channel
  to track garbage or drop out for the rest of the run. See the module's own
  docstring for the mechanism, and `notebooks/01-acquisition-and-tracking.ipynb`
  (section 6) for how it's wired into the tracking loop.
- `utils/gap_consensus.py` (new) -- after tracking, makes the channels of one
  recording agree on how much time each overflow dropped. A re-acquisition
  measures that time only modulo the PRN code period, so one channel can land on
  a different whole-period branch than the rest (thousands of km of pseudorange
  error for that channel). The channels vote per overflow episode; the minority
  is moved by whole periods, and what the vote cannot settle is left alone and
  reported. Run from `01-acquisition-and-tracking.ipynb`.
- `utils/observables.py` -- `form_observables` can leave out the measurement
  epochs that fall inside a hole of a lost-and-reacquired channel
  (`max_gap_factor`, `guard_after_gap_ms`) instead of interpolating a made-up
  code phase across it. On, in `02-navigation.ipynb`.
- `utils/period_ambiguity.py` (new) -- when six or more satellites share an
  epoch, finds which whole-code-period shift of each satellite's code phase makes
  the set fit best (an integer search, scored in one matrix product) and applies
  it only when the fit is good and clearly unique. Used by `02-navigation.ipynb`
  before the corrections.
- `utils/navigation.py` -- optional post-fit residual gate (`residual_gate_m`,
  receiver autonomous integrity monitoring) that drops the worst satellite of an
  epoch and re-solves; `solve_series` also skips a fix that is nowhere near the
  earth instead of seeding the next epoch with it.
- `scripts/run_notebook.py` -- runs notebook 00/01/02 headless with parameter
  overrides (see its docstring).
- `notebooks/00-raw-signal-diagnostics.ipynb`, `01-acquisition-and-tracking.ipynb`,
  `02-navigation.ipynb` -- updated to use the above, plus per-run figure saving and
  a `CHANNEL_INDEX` disambiguation fix for collects with more than one capture per band.

BeiDou B2a and B1C (D-044 to D-064; plan in `docs/BDS_PORT_PLAN.md`, follow-up list
in `docs/BDS_FOLLOW_UP.md`):

- `utils/bds_b2a.py`, `utils/bds_b1c.py` (new) -- the B2a and B1C code generators and
  signal constants. They do not use `gnss-tools`. B1C reads its code pairs from
  `docs/icd/bds_b1c_code_tables.json`.
- `utils/signal_interfaces.py` -- `BdsB2a` and `BdsB1C` signal classes, and their
  acquisition and tracking policies.
- `utils/tracking_channel.py` -- the symbol grid is tied to the overlay counter once
  the overlay syncs (D-059). B2a acquires only modulo 1 ms, so without this the
  epochs sit up to 4 ms from the symbol edge.
- `utils/nav/bcnav2.py`, `utils/nav/bcnav1.py`, `utils/nav/ldpc.py`,
  `utils/nav/ldpc_b1c_tables.py` (new) -- the B-CNAV2 (B2a) and B-CNAV1 (B1C)
  decoders and the three 64-ary LDPC codes.
- `utils/nav/ephemeris.py` -- `BdsEphemeris` (BeiDou orbit and clock, B2a and B1C
  group delays). `utils/nav/symbols.py` has the BeiDou table rows.
- `utils/time_systems.py` (new) -- GPST and BDT.
- `utils/observables.py`, `utils/navigation.py` -- time scales per system, joining
  several tracking runs, and a position solution with one inter-system bias for each
  extra system, optional C/N0 weights, and a fallback bias.
- `utils/multi_signal.py` (new) -- decodes a BeiDou tracking run into anchors and
  ephemerides.
- `utils/rinex3_nav.py`, `utils/bds_orbit_check.py` (new),
  `scripts/compare_bds_orbits.py` (new) -- compare decoded BeiDou orbits with RINEX 3
  and SP3 files. Needs CDDIS access.
- `docs/icd/` -- the B2a and B1C interface documents and the tables made from them.
- `notebooks/01-...ipynb` and `02-...ipynb` -- accept `BdsB2a` and `BdsB1C`. Notebook 02
  section 10 joins GPS L5 and BeiDou runs.

`submodules/gnss-tools` here is pinned to the same commit as the corresponding
submodule inside the `gps-tracking-example` submodule elsewhere in this repo
(`b5409db92a45fdbddae9a4863ded821873dadab6`, the real tip of upstream `gnss-tools`'
`master` -- the commit `gps-tracking-example`'s own history pins is unreachable on
any branch there, a bug in that repo, not this one).

Everything below this section is upstream's own README, kept for setup reference;
the clone instructions assume `cu-sense-lab/gps-tracking-example` directly rather
than this folder -- clone the HERON repo with `--recurse-submodules` instead (see
the root [README.md](../README.md)).

---

## BeiDou B2a and B1C: quick start

B2a is on the L5 band (1176.45 MHz). B1C is on the L1 band (1575.42 MHz). Both run
through the same three notebooks as the GPS signals.

1. **Set up** as in "Getting Started" below (this folder also has a `uv.lock`, so
   `uv sync` can build the environment). Run `pytest` to check; the known baseline
   failures are in `docs/BDS_BASELINE_TESTS.md`. Fill in `.env` (`COLLECTS_PATH`, `OUTPUTS_PATH`,
   `RESOURCES_PATH`). A recording needs a `metadata.yml` in its experiment folder.
2. **Notebook 00**: check that the recording is usable. Use a channel of the right
   band. B2a needs about 20 MHz of bandwidth; B1C needs about 4-5 MHz.
3. **Notebook 01**: set `SIGNAL_ID = BdsB2a` (or `BdsB1C`) and the experiment and
   channel indices that the catalog table shows. Run all cells. It writes a tracking
   file under `OUTPUTS_PATH/tracking/`. Track at least 30 s (B2a) or 40 s (B1C).
4. **Notebook 02**: set the same `SIGNAL_ID` and the same file. Run all cells.
   It decodes the message, forms pseudoranges and solves the position. The
   ephemeris comes from the signal; nothing is downloaded. Put the antenna position
   in `RECEIVER_REFERENCE_GEO` to see the error.
5. **Headless**: `python scripts/run_notebook.py 01 --set SIGNAL_ID=BdsB1C ...`
   (see the docstring of `scripts/run_notebook.py` for the full commands).

Limits that you should know: no ionosphere correction for BeiDou, B1C narrow-band
only, and B1C has no real-data check yet. See `docs/BDS_FOLLOW_UP.md`.

---

# GNSS Processing Package

The current (as of 2026) SeNSe Lab GNSS processing package is a collection of Python scripts and functions that can be used to process GNSS data.  It is designed to be modular, so that you can use only the parts you need for your own research or learning. 

## Getting Started

1. Clone the repository and initialize submodules:

```bash
git clone https://github.com/cu-sense-lab/gps-tracking-example
cd gps-tracking-example
git submodule update --init --recursive
```

2. Conda+Poetry Environment Setup

If you know what you're doing and want to use your own virtual environment manager (`uv`, `venv`, etc.), go for it!

Personally, I use conda, and the following instructions assume it is installed on your system.
If you don't have conda, you may download miniforge, located here: https://github.com/conda-forge/miniforge

It also assumes you have the `poetry` package manager installed.  (I do this because I find its dependency resolution to be better..)
See install here:  https://python-poetry.org/docs/

To set up the environment, you can run:

```bash
conda env create -f environment.yml --prefix ./.conda_env
```

To activate the environment, run (from the root of this repository):

```bash
conda activate ./.conda_env
```

To install the packages, run:

```bash
poetry install
```

3. Environment Variable Setup

Runtime configurations (data locations and credentials) are read from a `.env` file in the root of this repository.
Copy the template below and fill in real values for your machine. Each variable has a
corresponding `get_*()` accessor in `utils/environment_variables.py` (e.g. `get_outputs_path()`,
`get_resources_path()`, `get_collects_path()`, `get_earthdata_credentials()`) — use those instead of
reading the environment directly.

```bash
# Copy the following to `.env` and fill in real values for your machine.
# `.env` is gitignored — never commit real paths/credentials.

# Your working directory for processing outputs
# (acquisition/tracking results, logs, etc.). See
# utils/environment_variables.py. Defaults to `<repo_root>/local-data` if unset.
OUTPUTS_PATH=

# Base data directory used by gnss-tools for downloaded RINEX/orbit (SP3) data.
RESOURCES_PATH=

# Path to directory containing GNSS raw data collects:
# <experiment_name>/{collect_metadata.yml, <collect_id>.<ext>}
COLLECTS_PATH=

# One `.env` for several machines: any of the three paths above may also be given
# per platform (suffix _WINDOWS, _LINUX or _MACOS). The key for the platform running
# now wins over the plain one; a variable set in the shell wins over both. For example:
# COLLECTS_PATH_WINDOWS=D:/01_Data/collects
# COLLECTS_PATH_LINUX=/media/<you>/01_Data/collects

# Earthdata credentials for downloading RINEX/orbit (SP3) data from CDDIS/Earthdata.
# helpers (gnss_tools.rinex_io.cddis_download_utils / earthdata_utils).
EARTHDATA_USERNAME=
EARTHDATA_PASSWORD=
```


## Notes

- There is a submodule `gnss-tools` in this repository that contains some utility functions.  It is another github repository, located here:
https://github.com/cu-sense-lab/gnss-tools

- You can add your own utilities/functions to the `utils/` folder as needed.

- Please email me if you have any problems, and I will do my best to help!

*Aside*: I had forgotten that `numba` (a package for JIT compiling Python code) is not
currently compatible with Python 3.14 yet, so I had to downgrade back to 3.13.  If you already made
a Python 3.14 environment, you can remake it with:

    conda env remove -n gnss_lectures
    conda env create -f environment.yml
    conda activate gnss_lectures
    poetry install


