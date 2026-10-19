"""Update ONLY the KiCad-derived CLK_J<n>_KX / _KY parameters in the active design.

Why: the clock board layout changes while the rack design is open in Fusion.
This script re-reads the KiCad file and sets just the jack positions. It
touches no other parameter, so edits made in Fusion stay. Run it through
`run_in_fusion.py` with ENTRY = "sync_kicad".
"""
import adsk.core
import adsk.fusion

import build_flight_rack as builder
import fusion_lib as fl


def main() -> None:
    """Set the jack position literals from the KiCad file. Print what changed."""
    design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)
    cfg = fl.load_config(builder.SEED_PATH)
    literals = [s for s in builder.kicad_specs(cfg) if s.is_literal]
    changed = []
    for spec in literals:
        old = design.userParameters.itemByName(spec.name)
        if old is None:
            raise fl.ConfigError(f"Parameter {spec.name} is not in this design. Run the builder first.")
        if abs(old.value * 10.0 - spec.literal) > 1e-9:
            changed.append((spec.name, old.value * 10.0, spec.literal))
    fl.seed_parameters(design, literals)
    print(f"sync_kicad: {len(literals)} jack parameters set, {len(changed)} changed")
    for name, was, now in changed:
        print(f"  {name}: {was} -> {now}")
