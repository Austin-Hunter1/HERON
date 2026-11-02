"""
Start the pipeline window, or run the same pipeline from the command line.

With no arguments the window opens.  With `--experiment` and `--channel` the
run starts at once with no window.  The command-line form is for scripts and
for checks of the pipeline itself; it gives the same report folder.

    python -m pipeline_gui --experiment 3 --channel 0 --signals GpsL1CA,BdsB1C
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from .plan import RunRequest, default_track_duration_ms, parse_reference_geo


def _cli(args: argparse.Namespace, cfg) -> int:
    """Run one request with no window.  Exit code 0 only when every step passed."""
    from .pipeline import default_collects_dir, list_channels, run_and_report

    collects_dir = Path(args.collects) if args.collects else default_collects_dir(cfg)
    if collects_dir is None or not collects_dir.is_dir():
        print(f"Collects folder not found: {collects_dir}", file=sys.stderr)
        return 2
    channel = next(
        (c for c in list_channels(collects_dir)
         if c.experiment_index == args.experiment and c.channel_index == args.channel),
        None,
    )
    if channel is None:
        print(f"No channel exp={args.experiment} ch={args.channel} in {collects_dir}. "
              "Open the window to see the list.", file=sys.stderr)
        return 2

    if args.signals is None:
        signals = [s for s in cfg.signals.get(channel.band_id, []) if s not in cfg.signals_off_by_default]
    else:
        signals = [s.strip() for s in args.signals.split(",") if s.strip()]
    offset_ms = args.start_offset_ms if args.start_offset_ms is not None else cfg.start_offset_ms
    duration_s = args.track_duration_s if args.track_duration_s is not None else cfg.track_duration_s
    duration_ms = (
        int(round(duration_s)) * 1000 if duration_s is not None
        else default_track_duration_ms(channel.duration_s, offset_ms)
    )
    request = RunRequest(
        experiment_index=channel.experiment_index,
        channel_index=channel.channel_index,
        signals=tuple(signals),
        notebooks=tuple(n.strip() for n in args.notebooks.split(",")),
        start_offset_ms=offset_ms,
        track_duration_ms=duration_ms,
        reference_geo=parse_reference_geo(args.reference or ""),
    )
    outcome = run_and_report(cfg, collects_dir, channel, request)
    for r in outcome.results:
        print(f"{r.step.label:<14}{r.status:<12}{len(r.figures):>3} figure(s)  {r.message}")
    return 0 if outcome.all_passed else 1


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python -m pipeline_gui", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Config file (YAML)")
    parser.add_argument("--collects", help="Collects folder (default: config, then COLLECTS_PATH)")
    parser.add_argument("--experiment", type=int, help="`exp` index of the channel (no window)")
    parser.add_argument("--channel", type=int, help="`ch` index of the channel (no window)")
    parser.add_argument("--signals", help="Comma-separated SIGNAL_IDs (default: the band's signals that are on)")
    parser.add_argument("--notebooks", default="00,01,02", help="Comma-separated notebook ids")
    parser.add_argument("--start-offset-ms", type=float)
    parser.add_argument("--track-duration-s", type=float, help="Default: to the end of the collect")
    parser.add_argument("--reference", help='"lat, lon, height" for notebook 02')
    args = parser.parse_args()

    try:
        cfg = load_config(Path(args.config))
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if args.experiment is None and args.channel is None:
        from .gui import main as gui_main

        gui_main(cfg)
        return 0
    if args.experiment is None or args.channel is None:
        parser.error("give both --experiment and --channel, or neither (to open the window).")
    try:
        return _cli(args, cfg)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
