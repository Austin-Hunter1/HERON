"""
Turn the choices in the window into an ordered list of notebook runs.

This module has no side effects and imports nothing heavy, so the tests can
check the plan without a kernel.  Each `Step` holds the exact `--set` values
that `scripts/run_notebook.py` writes into the notebook config cells.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

NOTEBOOK_TITLES = {
    "00": "Raw-signal diagnostics",
    "01": "Acquisition and tracking",
    "02": "Navigation",
}


@dataclass(frozen=True)
class RunRequest:
    """
    What the user chose in the window.

    `experiment_index` and `channel_index` are the `exp` and `ch` values of
    `utils.catalog.list_channels`.  Notebooks 00, 01 and 02 all select the channel
    with `catalog.select_channel(collects_dir, EXPERIMENT_INDEX, CHANNEL_INDEX)`,
    so one pair of indices is correct for all three notebooks.
    """

    experiment_index: int
    channel_index: int
    signals: tuple[str, ...]
    notebooks: tuple[str, ...] = ("00", "01", "02")
    start_offset_ms: float = 0.0
    track_duration_ms: int = 120_000
    reference_geo: tuple[float, float, float] | None = None


@dataclass(frozen=True)
class Step:
    """One notebook run: which notebook, for which signal, with which overrides."""

    notebook: str
    signal: str | None
    overrides: dict[str, str] = field(default_factory=dict)
    depends_on: str | None = None
    """Label of a step that must pass first (notebook 02 needs notebook 01's file)."""

    @property
    def label(self) -> str:
        """Short unique name.  It names the figure folder and the saved notebook."""
        return f"{self.notebook}_{self.signal}" if self.signal else f"{self.notebook}_raw"

    @property
    def title(self) -> str:
        """Heading for the log and the report."""
        name = NOTEBOOK_TITLES[self.notebook]
        return f"Notebook {self.notebook}: {name}" + (f" ({self.signal})" if self.signal else "")


def default_track_duration_ms(collect_duration_s: float, start_offset_ms: float) -> int:
    """
    The track duration that reaches the end of the collect, in whole seconds.

    Why whole seconds: notebook 01 names the tracking file with the span rounded
    to seconds, and notebook 02 builds the same name from its own
    `TRACK_DURATION_MS`.  A whole-second value keeps the two names equal.
    """
    remaining_s = collect_duration_s - start_offset_ms / 1e3
    if remaining_s < 1.0:
        raise ValueError(
            f"the start offset {start_offset_ms:,.0f} ms leaves less than 1 s of a "
            f"{collect_duration_s:,.1f} s collect."
        )
    return int(math.floor(remaining_s)) * 1000


def parse_reference_geo(text: str) -> tuple[float, float, float] | None:
    """
    Read "lat, lon, height" from the window field.  An empty field gives `None`,
    which keeps the notebook's own `RECEIVER_REFERENCE_GEO`.
    """
    text = text.strip().strip("()")
    if not text:
        return None
    parts = [p for p in text.replace(";", ",").split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError("the reference position must be three numbers: lat, lon, height.")
    try:
        lat, lon, height = (float(p) for p in parts)
    except ValueError:
        raise ValueError(f"the reference position {text!r} has a value that is not a number.") from None
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        raise ValueError(f"the reference position {text!r} is not a valid latitude and longitude.")
    return lat, lon, height


def build_steps(request: RunRequest, navigation_unsupported: list[str] | tuple[str, ...] = ()) -> list[Step]:
    """
    Make the ordered steps for one request.

    The order is: notebook 00 once, then for each signal notebook 01 and then 02.
    Notebook 02 of a signal depends on notebook 01 of the same signal when both
    are in the request; when only 02 is requested, it reads a tracking file that
    an earlier run made.  Notebook 02 is not planned for a signal in
    `navigation_unsupported`.
    """
    unknown = set(request.notebooks) - set(NOTEBOOK_TITLES)
    if unknown:
        raise ValueError(f"unknown notebook ids {sorted(unknown)}.")
    if not request.notebooks:
        raise ValueError("select at least one notebook.")
    if {"01", "02"} & set(request.notebooks) and not request.signals:
        raise ValueError("select at least one signal for notebooks 01 and 02.")
    if request.start_offset_ms < 0:
        raise ValueError("the start offset must not be negative.")
    if request.track_duration_ms <= 0:
        raise ValueError("the track duration must be more than 0.")

    # Values are Python source text: run_notebook.py pastes them into the cell.
    channel = {
        "EXPERIMENT_INDEX": str(int(request.experiment_index)),
        "CHANNEL_INDEX": str(int(request.channel_index)),
    }
    # Notebooks 01 and 02 must get the same span, or 02 looks for a file name
    # that 01 did not write.  TRACK_DURATION_MS must be an int: notebook 01 sizes
    # arrays with it.
    span = {
        "START_OFFSET_MS": repr(float(request.start_offset_ms)),
        "TRACK_DURATION_MS": str(int(request.track_duration_ms)),
    }

    steps: list[Step] = []
    if "00" in request.notebooks:
        steps.append(Step("00", None, dict(channel)))

    for signal in request.signals:
        tracking_label = None
        if "01" in request.notebooks:
            step = Step("01", signal, {"SIGNAL_ID": signal, **channel, **span})
            steps.append(step)
            tracking_label = step.label
        if "02" in request.notebooks and signal not in navigation_unsupported:
            overrides = {"SIGNAL_ID": signal, **channel, **span}
            if request.reference_geo is not None:
                overrides["RECEIVER_REFERENCE_GEO"] = repr(tuple(request.reference_geo))
            steps.append(Step("02", signal, overrides, depends_on=tracking_label))
    return steps
