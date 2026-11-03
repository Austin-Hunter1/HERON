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
    acq_p_fa_total: dict[str, float] = field(default_factory=dict)
    """SIGNAL_ID -> false-alarm total of the first acquisition.  A missing signal
    keeps the notebook's ACQ_SETTINGS value (D-085)."""
    acq_threshold_db: dict[str, float] = field(default_factory=dict)
    """SIGNAL_ID -> detection threshold in dB above noise.  Wins over
    `acq_p_fa_total` in the notebook when both are given (D-085)."""
    acquisition_only: bool = False
    """Run notebook 01 up to its acquisition plots only, and no notebook 02 (D-085)."""
    assist: AssistRequest | None = None
    """Assisted acquisition in notebook 01 (D-086); None is the normal search."""


@dataclass(frozen=True)
class AssistRequest:
    """
    The assisted-acquisition values of notebook 01 (D-086).  The receiver position
    is `RunRequest.reference_geo`, the same field notebook 02 uses.

    `blocked_sectors` holds `(az_from_deg, az_to_deg, up_to_elevation_deg)` tuples,
    already checked (`utils.assisted_acquisition.parse_blocked_sectors`).  An empty
    tuple and 0 deg is the entire sky.
    """

    time_utc: str | None = None
    """'YYYY-MM-DD HH:MM:SS' at the first sample of the file; None: from the collect id."""
    min_elevation_deg: float = 0.0
    blocked_sectors: tuple[tuple[float, float, float], ...] = ()
    dwell_multiplier: int = 10
    stage1_satellites: int = 4
    doppler_halfwidth_hz: float = 250.0

    def overrides(self, reference_geo: tuple[float, float, float]) -> dict[str, str]:
        """The `--set` values for notebook 01, as Python source text."""
        return {
            "ASSISTED_ACQUISITION": "True",
            "ASSIST_POSITION_GEO": repr(tuple(float(v) for v in reference_geo)),
            "ASSIST_TIME_UTC": repr(self.time_utc) if self.time_utc else "None",
            "ASSIST_MIN_ELEVATION_DEG": repr(float(self.min_elevation_deg)),
            "ASSIST_BLOCKED_SECTORS": repr([tuple(float(v) for v in s) for s in self.blocked_sectors]),
            "ASSIST_DWELL_MULTIPLIER": str(int(self.dwell_multiplier)),
            "ASSIST_STAGE1_SATELLITES": str(int(self.stage1_satellites)),
            "ASSIST_DOPPLER_HALFWIDTH_HZ": repr(float(self.doppler_halfwidth_hz)),
        }


@dataclass(frozen=True)
class Step:
    """One notebook run: which notebook, for which signal, with which overrides."""

    notebook: str
    signal: str | None
    overrides: dict[str, str] = field(default_factory=dict)
    depends_on: str | None = None
    """Label of a step that must pass first (notebook 02 needs notebook 01's file)."""
    stop_before: str | None = None
    """Markdown heading where the run stops; None runs the whole notebook."""

    @property
    def label(self) -> str:
        """Short unique name.  It names the figure folder and the saved notebook."""
        return f"{self.notebook}_{self.signal}" if self.signal else f"{self.notebook}_raw"

    @property
    def title(self) -> str:
        """Heading for the log and the report."""
        name = "Acquisition only" if self.stop_before and self.notebook == "01" else NOTEBOOK_TITLES[self.notebook]
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


def parse_p_fa_total(text: str) -> float | None:
    """
    Read a false-alarm total from a window field.  Empty gives `None` (the notebook
    default).  The value must be in (0, 1), as `run_acquisition` requires.
    """
    text = text.strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"the p_fa value {text!r} is not a number (for example 1e-3).") from None
    if not 0.0 < value < 1.0:
        raise ValueError(f"the p_fa value {text!r} must be more than 0 and less than 1.")
    return value


def parse_threshold_db(text: str) -> float | None:
    """
    Read a detection threshold in dB above noise from a window field.  Empty gives
    `None`.  The value must be more than 0 dB: 0 dB is the mean noise level, so a
    lower threshold would declare noise cells as signals.
    """
    text = text.strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"the threshold {text!r} is not a number of dB.") from None
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"the threshold {text!r} must be more than 0 dB above noise.")
    return value


def assist_request_from_text(
    *,
    time_utc: str,
    min_elevation: str,
    sectors: str,
    dwell_multiplier: str,
    stage1_satellites: str,
    doppler_halfwidth: str,
) -> AssistRequest:
    """
    Read and check the assisted-acquisition fields of the window (or the command
    line).  A bad field raises ValueError with the reason.

    The time and sector parsers live in `utils.assisted_acquisition`, so the window
    and notebook 01 accept the same text.  They are imported here, not at the top,
    because they pull in numpy and the orbit code.
    """
    from utils import assisted_acquisition

    time_text = time_utc.strip() or None
    if time_text is not None:
        time_text = assisted_acquisition.parse_utc(time_text).isoformat(sep=" ")

    def number(text: str, name: str) -> float:
        try:
            return float(text.strip())
        except ValueError:
            raise ValueError(f"the {name} {text!r} is not a number.") from None

    def whole(text: str, name: str) -> int:
        value = number(text, name)
        if value < 1 or int(value) != value:
            raise ValueError(f"the {name} must be a whole number of at least 1, got {text!r}.")
        return int(value)

    min_el = number(min_elevation or "0", "minimum elevation")
    if not 0.0 <= min_el < 90.0:
        raise ValueError(f"the minimum elevation must be from 0 to 90 deg, got {min_elevation!r}.")
    halfwidth = number(doppler_halfwidth, "stage 2 window")
    if halfwidth <= 0:
        raise ValueError("the stage 2 window must be more than 0 Hz.")
    return AssistRequest(
        time_utc=time_text,
        min_elevation_deg=min_el,
        blocked_sectors=tuple(assisted_acquisition.parse_blocked_sectors(sectors)),
        dwell_multiplier=whole(dwell_multiplier, "dwell multiplier"),
        stage1_satellites=whole(stage1_satellites, "number of stage 1 satellites"),
        doppler_halfwidth_hz=halfwidth,
    )


def build_steps(
    request: RunRequest,
    navigation_unsupported: list[str] | tuple[str, ...] = (),
    acquisition_stop_heading: str = "## 6. Tracking",
) -> list[Step]:
    """
    Make the ordered steps for one request.

    The order is: notebook 00 once, then for each signal notebook 01 and then 02.
    Notebook 02 of a signal depends on notebook 01 of the same signal when both
    are in the request; when only 02 is requested, it reads a tracking file that
    an earlier run made.  Notebook 02 is not planned for a signal in
    `navigation_unsupported`.

    With `request.acquisition_only`, notebook 01 stops at
    `acquisition_stop_heading` and notebook 02 is not planned: there is no
    tracking file for it to read.
    """
    unknown = set(request.notebooks) - set(NOTEBOOK_TITLES)
    if unknown:
        raise ValueError(f"unknown notebook ids {sorted(unknown)}.")
    if not request.notebooks:
        raise ValueError("select at least one notebook.")
    if request.acquisition_only and "01" not in request.notebooks:
        raise ValueError("'acquisition only' needs notebook 01.")
    if request.assist is not None:
        if "01" not in request.notebooks:
            raise ValueError("assisted acquisition needs notebook 01.")
        if request.reference_geo is None:
            raise ValueError(
                "assisted acquisition needs the reference position (lat, lon, height)."
            )
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
            overrides = {"SIGNAL_ID": signal, **channel, **span}
            # Only a value the user gave is sent, so the notebook keeps its own
            # default for every other signal.
            if signal in request.acq_p_fa_total:
                overrides["ACQ_P_FA_TOTAL"] = repr(float(request.acq_p_fa_total[signal]))
            if signal in request.acq_threshold_db:
                overrides["ACQ_THRESHOLD_DB"] = repr(float(request.acq_threshold_db[signal]))
            if request.assist is not None:
                overrides.update(request.assist.overrides(request.reference_geo))
            stop = acquisition_stop_heading if request.acquisition_only else None
            step = Step("01", signal, overrides, stop_before=stop)
            steps.append(step)
            tracking_label = step.label
        if (
            "02" in request.notebooks
            and not request.acquisition_only
            and signal not in navigation_unsupported
        ):
            overrides = {"SIGNAL_ID": signal, **channel, **span}
            if request.reference_geo is not None:
                overrides["RECEIVER_REFERENCE_GEO"] = repr(tuple(request.reference_geo))
            steps.append(Step("02", signal, overrides, depends_on=tracking_label))
    return steps
