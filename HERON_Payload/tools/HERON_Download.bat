@echo off
rem Double-click to open the HERON Download page (http://localhost:8770).
rem It copies NUC recordings into D:\01_Data\collects. Keep this window open while you download.
rem Needs uv (https://docs.astral.sh/uv/); uv installs Python and PyYAML for the page by itself.
cd /d "%~dp0"
uv run heron_pull_page.py %*
if errorlevel 1 (
  echo.
  echo HERON Download stopped with an error. Is uv installed? Read the message above.
  pause
)
