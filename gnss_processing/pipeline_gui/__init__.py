"""
Notebook pipeline window (D-084).

Runs notebooks 00, 01 and 02 on one channel of a collect with one click, keeps
every figure, and writes a short PDF report.  Start it from `gnss_processing/`:

    python -m pipeline_gui            # the window
    python -m pipeline_gui --help     # the command-line form, with no window

Use the interpreter that runs the notebooks (see `scripts/run_notebook.py`).
Settings are in `configs/pipeline_gui.yml`.
"""
