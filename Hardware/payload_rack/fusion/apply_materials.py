"""Set the physical material of every rack part from its category.

Why: the parts are in three material groups (laser, rigid, flex; D-035).
The `[materials]` table in `rack_seed.toml` gives one material for each
group. Change the table and run this script to change all parts of a group
at once. The script changes only materials. It does not touch geometry or
parameters, so edits made in Fusion stay. Run it through `run_in_fusion.py`
with ENTRY = "apply_materials".
"""
import adsk.core
import adsk.fusion

import build_flight_rack as builder
import fusion_lib as fl

# Categories that get a material. The "ref" parts are not made, so they
# keep their appearance and get no material.
MATERIAL_CATEGORIES = ("laser", "rigid", "flex")


def apply(design: adsk.fusion.Design, cfg: dict) -> list:
    """Set the material of each tagged component. Return (component, material) rows.

    Why a clear failure: a material name that is not in the library is a
    config error. The script stops before it changes any part.
    """
    app = adsk.core.Application.get()
    table = cfg["materials"]
    library = app.materialLibraries.itemByName(table["library"])
    if library is None:
        raise fl.ConfigError(f"[materials] library not found: {table['library']}")
    materials = {}
    for cat in MATERIAL_CATEGORIES:
        mat = library.materials.itemByName(table[cat])
        if mat is None:
            raise fl.ConfigError(f"[materials] {cat} = '{table[cat]}' is not in {table['library']}")
        materials[cat] = mat
    rows = []
    for comp in design.allComponents:
        attr = comp.attributes.itemByName("HERON", "category")
        if attr is None or attr.value not in materials:
            continue
        comp.material = materials[attr.value]
        rows.append((comp.name, materials[attr.value].name))
    return rows


def main() -> None:
    """Apply the materials to the active design and print what was set."""
    design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)
    rows = apply(design, fl.load_config(builder.SEED_PATH))
    print(f"apply_materials: {len(rows)} components set")
    for name, mat in rows:
        print(f"  {name}: {mat}")
