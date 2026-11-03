@echo off
rem Open the notebook pipeline window (D-084).  Double-click this file.
rem It uses the gps-tracking-example environment, the same one that runs the
rem notebooks (see scripts/run_notebook.py, PLATFORM_DEFAULTS).
cd /d "%~dp0.."
"..\gps-tracking-example\.venv\Scripts\python.exe" -m pipeline_gui %*
if errorlevel 1 pause
