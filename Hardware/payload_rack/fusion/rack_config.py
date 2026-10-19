"""Seed configuration for the flight_rack Fusion design (pure Python).

Why this module exists: the seed file `rack_seed.toml` holds every tunable
value of the rack. CLAUDE.md says to validate a config at run time and to
fail with a clear message. This module does that work. It has no `adsk`
import, so pytest can test it outside Fusion.

The same module also evaluates the seed expressions in Python. The tests and
the builder use this to compare the seed against the CadQuery reference.
"""
from __future__ import annotations

import math
import re
import tomllib
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

# Units a parameter may use. "none" means unitless (counts, factors).
UNITS = ("mm", "deg", "none")

# Names that Fusion refuses as parameter names (they are unit symbols).
# Found by test in Fusion: T, H, W, K, m, mm. The rest are common unit symbols.
RESERVED_NAMES = frozenset(
    {"T", "H", "W", "K", "m", "mm", "cm", "in", "deg", "rad", "s", "A", "V",
     "N", "J", "F", "C", "L", "g", "ft", "Pa", "Hz"}
)

# Words that may appear in an expression and are not parameter names.
EXPR_WORDS = frozenset(
    {"mm", "cm", "m", "in", "deg", "rad", "abs", "min", "max", "cos", "sin",
     "tan", "acos", "asin", "atan", "sqrt", "pow"}
)

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUM_UNIT_RE = re.compile(r"(\d+\.?\d*(?:[eE][-+]?\d+)?)\s*(mm|cm|deg|rad)\b")


class ConfigError(ValueError):
    """Raised when the seed file has a bad or missing value."""


@dataclass(frozen=True)
class ParamSpec:
    """One User Parameter: its name, Fusion expression, unit and comment.

    Why a class: the builder, the KiCad sync and the tests all pass
    parameters around. One small type keeps that interface narrow.
    """

    name: str
    expression: str      # text for Fusion, for example "3.0 mm" or "W_ + 2"
    unit: str            # "mm", "deg" or "none"
    comment: str
    is_literal: bool     # True when the seed gives a number, False for an expression
    literal: Optional[float] = None


def load_config(path: str) -> Dict[str, Any]:
    """Read a TOML seed file and check it. Return the raw dictionary.

    Why: every user of the seed must get a checked file, so the check is
    part of the load.
    """
    try:
        with open(path, "rb") as handle:
            cfg = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"Seed file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Seed file {path} is not valid TOML: {exc}") from exc
    validate_config(cfg)
    return cfg


def _number_text(value: float) -> str:
    """Write a float so Fusion reads the same number back."""
    return repr(float(value))


def param_specs(cfg: Mapping[str, Any]) -> List[ParamSpec]:
    """Turn the `[params]` table into an ordered list of ParamSpec.

    Why: the order in the file is the order of creation. A parameter can
    only use names that come before it.
    """
    specs: List[ParamSpec] = []
    for name, entry in cfg.get("params", {}).items():
        unit = entry["unit"]
        if "value" in entry:
            number = _number_text(entry["value"])
            text = number if unit == "none" else f"{number} {unit}"
            specs.append(ParamSpec(name, text, unit, entry.get("comment", ""), True, float(entry["value"])))
        else:
            specs.append(ParamSpec(name, entry["expr"], unit, entry.get("comment", ""), False))
    return specs


def expression_names(expr: str) -> List[str]:
    """Return the parameter names that an expression uses."""
    stripped = _NUM_UNIT_RE.sub("0", expr)
    return [w for w in _IDENT_RE.findall(stripped) if w not in EXPR_WORDS]


def validate_config(cfg: Mapping[str, Any]) -> None:
    """Check the whole seed. Raise ConfigError with ALL problems found.

    Why all problems at once: a long seed file has many lines. One message
    that lists every problem saves many runs.
    """
    problems: List[str] = []
    params = cfg.get("params")
    if not isinstance(params, dict) or not params:
        raise ConfigError("Seed has no [params] table.")
    defined: set = set()
    for name, entry in params.items():
        where = f"[params] {name}"
        if not _NAME_RE.match(name):
            problems.append(f"{where}: bad parameter name")
        if name in RESERVED_NAMES:
            problems.append(f"{where}: Fusion refuses this name (unit symbol). Add an underscore.")
        if not isinstance(entry, dict):
            problems.append(f"{where}: must be a table with value/expr, unit, comment")
            defined.add(name)
            continue
        unit = entry.get("unit")
        if unit is None:
            problems.append(f"{where}: missing 'unit'")
        elif unit not in UNITS:
            problems.append(f"{where}: unknown unit '{unit}' (use one of {', '.join(UNITS)})")
        has_value, has_expr = "value" in entry, "expr" in entry
        if has_value == has_expr:
            problems.append(f"{where}: give exactly one of 'value' or 'expr'")
        if has_value and (isinstance(entry["value"], bool) or not isinstance(entry["value"], (int, float))):
            problems.append(f"{where}: 'value' must be a number")
        if has_expr:
            if not isinstance(entry["expr"], str) or not entry["expr"].strip():
                problems.append(f"{where}: 'expr' must be a non-empty string")
            else:
                for word in expression_names(entry["expr"]):
                    if word not in defined:
                        problems.append(f"{where}: expression uses '{word}', which is not defined above it")
        if not isinstance(entry.get("comment", ""), str):
            problems.append(f"{where}: 'comment' must be text")
        defined.add(name)
    for key in ("laser", "rigid", "flex"):
        if not isinstance(cfg.get("materials", {}).get(key), str):
            problems.append(f"[materials] missing text value '{key}'")
    look = cfg.get("ref_look", {})
    for key in ("library", "base", "name"):
        if not isinstance(look.get(key), str) or not look.get(key):
            problems.append(f"[ref_look] missing text value '{key}'")
    for key in ("red", "green", "blue", "alpha"):
        val = look.get(key)
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not 0.0 <= val <= 1.0:
            problems.append(f"[ref_look] '{key}' must be a number from 0 to 1")
    for index, slot in enumerate(cfg.get("slots", [])):
        where = f"[[slots]] #{index + 1}"
        for key in ("name", "kind", "pitch"):
            if key not in slot:
                problems.append(f"{where}: missing '{key}'")
        if slot.get("kind") not in ("nuc", "b210", "b200"):
            problems.append(f"{where}: kind must be nuc, b210 or b200")
        if slot.get("pitch") not in params:
            problems.append(f"{where}: pitch parameter '{slot.get('pitch')}' is not in [params]")
    if not cfg.get("slots"):
        problems.append("Seed has no [[slots]].")
    kicad = cfg.get("kicad", {})
    if not isinstance(kicad.get("pcb"), str):
        problems.append("[kicad] missing text value 'pcb'")
    if problems:
        raise ConfigError("Bad seed file:\n  - " + "\n  - ".join(problems))


# ---------------------------------------------------------------- evaluation
_UNIT_FACTOR = {"mm": 1.0, "cm": 10.0, "deg": math.pi / 180.0, "rad": 1.0}


def evaluate_expression(expr: str, env: Mapping[str, float]) -> float:
    """Evaluate a Fusion-style expression in Python.

    Internal units are mm and radians. Fusion writes function arguments with
    ';'. Python needs ','. The function maps one to the other.
    """
    text = _NUM_UNIT_RE.sub(lambda m: f"({m.group(1)}*{_UNIT_FACTOR[m.group(2)]!r})", expr)
    text = text.replace(";", ",")
    scope = {"abs": abs, "min": min, "max": max, "cos": math.cos, "sin": math.sin,
             "tan": math.tan, "sqrt": math.sqrt, "acos": math.acos,
             "asin": math.asin, "atan": math.atan, "pow": pow}
    scope.update(env)
    try:
        return float(eval(text, {"__builtins__": {}}, scope))  # noqa: S307 - own seed text only
    except Exception as exc:  # noqa: BLE001 - report the bad expression
        raise ConfigError(f"Cannot evaluate '{expr}': {exc}") from exc


def evaluate_params(specs: List[ParamSpec]) -> Dict[str, float]:
    """Evaluate all parameters in order. Return display values.

    Display values: mm for length, degrees for angle, plain number for
    unitless. These match what a Fusion parameter shows in its own unit.
    """
    env: Dict[str, float] = {}
    shown: Dict[str, float] = {}
    for spec in specs:
        internal = evaluate_expression(spec.expression, env)
        env[spec.name] = internal
        shown[spec.name] = math.degrees(internal) if spec.unit == "deg" else internal
    return shown
