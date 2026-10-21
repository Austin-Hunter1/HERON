#!/usr/bin/env python3
"""
Compare two tracking runs of one signal: with and without the BOC(6,1) part.

Use it after you have tracked one recording twice with notebook 01 (or
`scripts/run_notebook.py`): once with the default model and once with
``BOC61_ENABLED`` set to the other value.  The second run's file name ends in
``_wb`` (wide band) or ``_nb`` (narrow band), so the two files sit side by side
(D-077).

    python scripts/compare_boc61.py DEFAULT.h5 VARIANT.h5
    python scripts/compare_boc61.py DEFAULT.h5 VARIANT.h5 --cmc-window-s 5 --cn0-skip-s 5

It prints, for every satellite both runs tracked: the pilot C/N0 of each run and
the change, the code-minus-carrier scatter of each run and their ratio, and the
number of phase-lock losses.  The meaning of each number is in
`utils/boc61_metrics.py`.  Every setting of the metrics is a flag here.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from utils import boc61_metrics, tracking_io  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    defaults = boc61_metrics.MetricSettings()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("default_file", type=Path, help="tracking file of the first model")
    parser.add_argument("variant_file", type=Path, help="tracking file of the second model")
    parser.add_argument("--cmc-window-s", type=float, default=defaults.cmc_window_s)
    parser.add_argument("--cmc-polynomial-order", type=int, default=defaults.cmc_polynomial_order)
    parser.add_argument("--cn0-skip-s", type=float, default=defaults.cn0_skip_s)
    parser.add_argument(
        "--no-subcarrier-correction", action="store_true",
        help="use the code loop alone, without the subcarrier loop's offset",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = replace(
        boc61_metrics.MetricSettings(),
        cmc_window_s=args.cmc_window_s,
        cmc_polynomial_order=args.cmc_polynomial_order,
        cn0_skip_s=args.cn0_skip_s,
        use_subcarrier_correction=not args.no_subcarrier_correction,
    )
    first = tracking_io.load_tracking_results(args.default_file)
    second = tracking_io.load_tracking_results(args.variant_file)
    comparison = boc61_metrics.compare_runs(first, second, settings)
    if not comparison:
        print("The two runs share no satellite; nothing to compare.", file=sys.stderr)
        return 1
    label_first = first.variant or "default"
    label_second = second.variant or "default"
    print(f"{first.signal_type_id}: {label_first} against {label_second}\n")
    print(boc61_metrics.format_comparison(comparison, label_first[:7], label_second[:7]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
