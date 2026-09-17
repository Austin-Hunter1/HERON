"""
Capture the golden tracking baseline.

Run BEFORE refactoring, then never again unless a numerical change is intended and
reviewed:

    python -m tests.generate_golden

Adding a signal is the one routine reason to run this afterwards, and then only
for the new scenarios -- pass their names so the existing baselines are left
untouched and keep doing their job:

    python -m tests.generate_golden l1c_clean l1c_noisy

`tests/test_tracking_regression.py` diffs live output against these files.
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np

from .driver import run_scenario
from .scenarios import SCENARIOS

GOLDEN_DIR = pathlib.Path(__file__).parent / "golden"


def main(names: list[str] | None = None) -> None:
    GOLDEN_DIR.mkdir(exist_ok=True)
    scenarios = SCENARIOS
    if names:
        known = {s.name for s in SCENARIOS}
        unknown = sorted(set(names) - known)
        if unknown:
            raise SystemExit(f"no such scenario(s): {unknown}; have {sorted(known)}")
        scenarios = tuple(s for s in SCENARIOS if s.name in set(names))
    for scenario in scenarios:
        outputs = run_scenario(scenario)
        path = GOLDEN_DIR / f"{scenario.name}.npz"
        np.savez_compressed(path, **outputs)

        # Report the component the loops ran on; for L5 that is the Q pilot, and
        # component 0 would show its data-limited I channel instead.
        component = int(outputs["carrier_component"])
        prompt_mag = np.abs(outputs["prompt_corr"][:, component])
        final_doppler_error = outputs["doppler_freq_hz"][-1] - scenario.doppler_hz
        tail = prompt_mag[-min(500, len(prompt_mag)) :]
        print(
            f"{scenario.name:22} epochs={int(outputs['output_index']):5} "
            f"mode={outputs['final_mode']:3} "
            f"dopp_err={final_doppler_error:+9.3f} Hz "
            f"|P[{component}]|={tail.mean():9.1f} -> {path.name}"
        )


if __name__ == "__main__":
    main(sys.argv[1:])
