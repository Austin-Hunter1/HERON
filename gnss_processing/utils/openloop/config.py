"""
Load and validate `configs/openloop.yml` (CLAUDE.md rule 5).

The loader checks every key that notebook 03 reads, with its type and its
allowed values, and stops with one message that lists every problem. Values that
are null on purpose until a decision or a measurement exists (lever arms D-091,
flight height D-099, water level D-093) are allowed by `load_config`, and
checked by `require_real_data_values` before a real-data run.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "configs" / "openloop.yml"

CHOICES = {
    ("trajectory", "source"): {"rtklib_pos", "static"},
    ("trajectory", "rover"): {"here3", "zed_f9p"},
    ("attitude", "source"): {"ardupilot_log", "constant"},
    ("attitude", "message"): {"ATT", "XKF1"},
    ("models", "model_mode"): {"direct_referenced", "absolute"},
    ("models", "orbits"): {"broadcast", "precise"},
    ("models", "specular"): {"image_plane", "iterative_curved"},
    ("models", "geoid"): {"geoid18", "egm2008", "constant", "none"},
    ("models", "ionosphere"): {"nequick_g", "klobuchar", "ionex", "none"},
    ("models", "troposphere"): {"saastamoinen", "gpt3", "none"},
}

NUMBERS = [
    ("openloop", "model_rate_hz"), ("openloop", "coherent_ms"), ("openloop", "lag_step_chips"),
    ("openloop", "lag_margin_chips"), ("acquisition", "incoherent_blocks"),
    ("acquisition", "p_fa_total"), ("phase", "slip_search"), ("phase", "process_accel_mps2"),
    ("phase", "min_cn0_dbhz"), ("phase", "coherence_window"), ("phase", "coherence_min"),
    ("leakage", "window_blocks"), ("leakage", "min_turns"), ("leakage", "strong_fraction"),
    ("surface", "rms_height_m"), ("models", "geoid_epoch_year"),
]

POSITIVE = {("openloop", "model_rate_hz"), ("openloop", "coherent_ms"),
            ("openloop", "lag_step_chips"), ("acquisition", "incoherent_blocks"),
            ("leakage", "window_blocks")}


def _get(cfg: dict, path: tuple[str, ...]):
    node = cfg
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise KeyError(".".join(path))
        node = node[key]
    return node


def validate(cfg: dict) -> list[str]:
    """Every problem in `cfg`, as text. Empty when the config is good."""
    problems: list[str] = []
    for path, allowed in CHOICES.items():
        try:
            value = _get(cfg, path)
        except KeyError:
            problems.append(f"missing: {'.'.join(path)}")
            continue
        if value not in allowed:
            problems.append(f"{'.'.join(path)} = {value!r}; allowed: {sorted(allowed)}")
    for path in NUMBERS:
        try:
            value = _get(cfg, path)
        except KeyError:
            problems.append(f"missing: {'.'.join(path)}")
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            problems.append(f"{'.'.join(path)} must be a number, not {value!r}")
        elif path in POSITIVE and value <= 0:
            problems.append(f"{'.'.join(path)} must be > 0, not {value}")
    try:
        p_fa = _get(cfg, ("acquisition", "p_fa_total"))
        if isinstance(p_fa, (int, float)) and not 0 < p_fa < 1:
            problems.append("acquisition.p_fa_total must be in (0, 1)")
        noise = _get(cfg, ("openloop", "noise_lags_chips"))
        if not (isinstance(noise, list) and len(noise) == 2 and noise[0] < noise[1]):
            problems.append("openloop.noise_lags_chips must be [start, stop] with start < stop")
        for name, ant in _get(cfg, ("antennas",)).items():
            arm = ant.get("lever_arm_b")
            if arm is not None and (not isinstance(arm, list) or len(arm) != 3):
                problems.append(f"antennas.{name}.lever_arm_b must be null or [x, y, z]")
            bore = ant.get("boresight_b")
            if not (isinstance(bore, list) and len(bore) == 3 and np.linalg.norm(bore) > 0):
                problems.append(f"antennas.{name}.boresight_b must be a non-zero [x, y, z]")
        if _get(cfg, ("trajectory", "reference_point")) not in _get(cfg, ("antennas",)):
            problems.append("trajectory.reference_point must name an antenna in 'antennas'")
    except KeyError as exc:
        problems.append(f"missing: {exc.args[0]}")
    if cfg.get("models", {}).get("troposphere") == "gpt3" and not cfg["models"].get("gpt3_grid"):
        problems.append("models.gpt3_grid is required when models.troposphere = gpt3")
    if cfg.get("models", {}).get("geoid") == "constant" and cfg.get("surface", {}).get(
            "constant_geoid_n_m") is None:
        problems.append("surface.constant_geoid_n_m is required when models.geoid = constant")
    return problems


def load_config(path: str | Path = DEFAULT_PATH) -> dict:
    """Read and validate the config; raise ValueError that lists every problem."""
    path = Path(path)
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"config file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: not valid YAML: {exc}") from exc
    problems = validate(cfg or {})
    if problems:
        raise ValueError(f"{path}: bad config:\n  - " + "\n  - ".join(problems))
    return cfg


def require_real_data_values(cfg: dict) -> None:
    """
    Stop before a real-data run when a value that has no default is still null:
    the lever arms (D-091), the flight height and mask (D-099), the water level
    (D-093), the trajectory file, and the time base (D-100).
    """
    missing = [f"antennas.{n}.lever_arm_b (D-091)" for n, a in cfg["antennas"].items()
               if a.get("lever_arm_b") is None]
    for path, why in [(("flight", "height_max_m"), "D-099"),
                      (("flight", "elevation_mask_deg"), "D-099"),
                      (("surface", "water_level_m"), "D-093"),
                      (("time", "timebase_file"), "D-100")]:
        if _get(cfg, path) is None:
            missing.append(f"{'.'.join(path)} ({why})")
    if cfg["trajectory"]["source"] == "rtklib_pos" and not cfg["trajectory"].get("path"):
        missing.append("trajectory.path (D-088)")
    if missing:
        raise ValueError("these values must be set before a real-data run:\n  - "
                         + "\n  - ".join(missing))
