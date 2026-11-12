"""
Save and load notebook 03 results: `<OUTPUTS_PATH>/openloop/*.h5` (plan 11.2).

One file per (collect, band, nadir channel). One group per satellite. Every raw
complex waveform is kept, because notebook 04 (CWF, D-101) needs them. The
provenance attributes say which models made the file, so a file made with other
models is never mistaken for this one.
"""

from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np

FORMAT_VERSION = 1

_ARRAYS = ("periods", "uptime_ms", "duration_ms", "lags_chips", "doppler_offsets_hz",
           "waveforms", "model_code_phase_ms", "model_carrier_cycles", "model_doppler_hz",
           "model_excess_m")


def save(path: str | Path, runs: dict, *, provenance: dict, products: dict | None = None) -> Path:
    """
    Write `runs` (sat_id -> OpenLoopOutput) and optional `products`
    (sat_id -> dict of arrays: phase, C/N0, filter output, leakage, geometry).
    """
    from utils.tracking_io import _git_commit

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        f.attrs["format_version"] = FORMAT_VERSION
        f.attrs["git_commit"] = _git_commit()
        f.attrs["provenance"] = json.dumps(provenance, default=str)
        for sat_id, ol in runs.items():
            g = f.create_group(sat_id)
            for name in _ARRAYS:
                g.create_dataset(name, data=np.asarray(getattr(ol, name)),
                                 compression="gzip" if name == "waveforms" else None)
            g.attrs["component_names"] = json.dumps(list(ol.component_names))
            g.attrs["carrier_freq_hz"] = ol.carrier_freq_hz
            g.attrs["code_rate_chips_per_sec"] = ol.code_rate_chips_per_sec
            g.attrs["extras"] = json.dumps(ol.extras, default=str)
            for key, value in (products or {}).get(sat_id, {}).items():
                g.create_dataset(f"products/{key}", data=np.asarray(value))
    return path


def load(path: str | Path) -> tuple[dict, dict, dict]:
    """Return (runs as dicts of arrays, products, provenance)."""
    runs, products = {}, {}
    with h5py.File(path, "r") as f:
        if int(f.attrs.get("format_version", 0)) != FORMAT_VERSION:
            raise ValueError(f"{path}: format version {f.attrs.get('format_version')} "
                             f"is not {FORMAT_VERSION}")
        provenance = json.loads(f.attrs["provenance"])
        for sat_id, g in f.items():
            runs[sat_id] = {name: g[name][()] for name in _ARRAYS}
            runs[sat_id]["component_names"] = json.loads(g.attrs["component_names"])
            runs[sat_id]["carrier_freq_hz"] = float(g.attrs["carrier_freq_hz"])
            if "products" in g:
                products[sat_id] = {k: v[()] for k, v in g["products"].items()}
    return runs, products, provenance
