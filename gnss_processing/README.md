
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
- `notebooks/00-raw-signal-diagnostics.ipynb`, `01-acquisition-and-tracking.ipynb`,
  `02-navigation.ipynb` -- updated to use the above, plus per-run figure saving and
  a `CHANNEL_INDEX` disambiguation fix for collects with more than one capture per band.

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


