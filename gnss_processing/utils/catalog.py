"""
What is on disk, listed as a table.

Both halves of this repository start with the same question -- *which recording?*
-- and the answer is not something a notebook can hard-code, because it depends on
what the reader has under `COLLECTS_PATH`.  Two catalogues are printed instead, one
per stage:

* `print_collect_catalog` -- every raw IF channel available: which experiment,
  which collect, which band, and the sample rate, format and duration needed to
  decide whether it can carry the signal you want.  Notebooks 00 and 01 print it
  immediately above their configuration cell, so the indices they ask for can be
  read straight off it.
* `print_tracking_catalog` -- every tracking run notebook 01 has already written:
  experiment, signal, the span of the collect it covers, and which satellites it
  holds.  Notebook 02 prints it for the same reason -- its four configuration
  values are exactly the four columns of that table.

The catalogues report indices rather than names wherever a notebook asks for an
index, so nothing has to be transcribed by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import collect_metadata_utils, tables, tracking_io
from .sample_streaming import SampleParameters

# `RX7_GPS_L1C_115-600s.h5`: collect label, signal type, and the span in seconds.
# Both the label and the signal type contain underscores, so the span at the end
# is what anchors the match.
_TRACKING_FILENAME = re.compile(
    r"^(?P<label>.+)_(?P<signal>[A-Z]+_[A-Z0-9]+)_(?P<start>\d+)-(?P<end>\d+)s\.h5$"
)


def sample_format_label(params: SampleParameters) -> str:
    """
    The short name a raw sample format goes by: `sc8` is signed, complex, 8-bit.

    The same three letters the file extensions use, so a format that disagrees
    with the file it describes is visible at a glance.
    """
    return (
        ("f" if not params.is_integer else "s" if params.is_signed else "u")
        + ("c" if params.is_complex else "r")
        + str(params.bit_depth)
    )


@dataclass
class Channel:
    """One band of one collect: everything needed to choose it, and to open it."""

    experiment_index: int
    experiment_name: str
    channel_index: int
    """Index within this experiment's channels -- what notebook 00 selects with."""
    collect_id: str
    band_id: str
    filepath: Path
    samp_rate: float
    sample_params: SampleParameters
    inter_freq_hz: float
    sibling_collect_ids: tuple[str, ...] = ()
    """Every collect id in this experiment -- what `tracking_io` needs to decide
    whether a collect's short label is unique enough to name a file with."""

    @property
    def size_bytes(self) -> int:
        return self.filepath.stat().st_size if self.filepath.exists() else 0

    @property
    def duration_s(self) -> float:
        """Length of the recording, from its size and its sample format."""
        bits_per_sample = self.sample_params.bit_depth * (
            2 if self.sample_params.is_complex else 1
        )
        if not self.size_bytes or not bits_per_sample:
            return 0.0
        return (self.size_bytes * 8 // bits_per_sample) / self.samp_rate


def list_channels(collects_dir: str | Path) -> list[Channel]:
    """
    Every (experiment, collect, band) triple under `collects_dir`.

    Experiments are indexed in the order `list_experiment_names` returns them, so
    `experiment_index` is the value a notebook's `EXPERIMENT_INDEX` takes.
    Channels are indexed within their experiment, and a collect carrying two bands
    contributes one channel per band -- because a band is what a signal rides on,
    and it is the band, not the collect, that a notebook is really choosing.
    """
    collects_dir = Path(collects_dir)
    channels: list[Channel] = []
    for experiment_index, name in enumerate(
        collect_metadata_utils.list_experiment_names(collects_dir)
    ):
        experiment_dir = collects_dir / name
        try:
            metadata = collect_metadata_utils.load_experiment_metadata_from_file(
                experiment_dir / "metadata.yml"
            )
        except Exception:
            # A directory without usable metadata is not an experiment.  It still
            # occupies an index, though, since `EXPERIMENT_INDEX` counts
            # directories -- so skip its channels, not the index.
            continue

        channel_index = 0
        for collect_id in metadata.collect_ids:
            collect = metadata.collects[collect_id]
            channel_config = metadata.channel_configurations[collect.channel_config_id]
            for band_id in channel_config.band_ids:
                band_config = metadata.band_configurations[band_id]
                channels.append(
                    Channel(
                        experiment_index=experiment_index,
                        experiment_name=name,
                        channel_index=channel_index,
                        collect_id=collect_id,
                        band_id=band_id,
                        filepath=experiment_dir / collect.filename,
                        samp_rate=channel_config.samp_rate,
                        sample_params=channel_config.sample_params,
                        inter_freq_hz=band_config.inter_freq,
                        sibling_collect_ids=tuple(metadata.collect_ids),
                    )
                )
                channel_index += 1
    return channels


def print_collect_catalog(
    collects_dir: str | Path, *, band: str | None = None
) -> list[Channel]:
    """
    Print every raw channel available, and return them.

    `band` keeps only channels carrying that band -- what notebook 01 wants, since
    the signal it is configured for determines the band and the only real choice
    left is the experiment.

    The experiment name is printed once per experiment rather than on every row:
    the rows under it are its channels.
    """
    channels = list_channels(collects_dir)
    if band is not None:
        channels = [c for c in channels if c.band_id == band]

    rows, previous = [], None
    for channel in channels:
        duration_s = channel.duration_s
        rows.append(
            [
                channel.experiment_index if channel.experiment_name != previous else "",
                channel.experiment_name if channel.experiment_name != previous else "",
                channel.channel_index,
                tracking_io.collect_label(
                channel.collect_id, channel.sibling_collect_ids
            ),
                channel.band_id,
                f"{channel.samp_rate / 1e6:.1f}",
                sample_format_label(channel.sample_params),
                f"{channel.inter_freq_hz / 1e3:,.0f}",
                f"{duration_s:,.0f}" if duration_s else "missing",
                f"{channel.size_bytes / 1e9:.1f}" if channel.size_bytes else "",
            ]
        )
        previous = channel.experiment_name

    caption = f"Collects under {collects_dir}"
    if band is not None:
        caption += f", carrying band {band}"
    if not rows:
        print(caption + ": none.")
        return channels
    tables.print_table(
        ["exp", "experiment", "ch", "collect", "band", "Msps", "fmt",
         "IF [kHz]", "dur [s]", "GB"],
        rows,
        aligns="><><<><>>>",
        caption=caption + ":",
    )
    return channels


@dataclass
class TrackingRunInfo:
    """One tracking file notebook 01 wrote, described without opening it fully."""

    path: Path
    experiment_index: int | None
    experiment_name: str
    collect_label: str
    signal_type_id: str
    start_offset_ms: float
    duration_ms: float
    signal_ids: list[str]


def list_tracking_runs(
    outputs_path: str | Path, collects_dir: str | Path | None = None
) -> list[TrackingRunInfo]:
    """
    Every tracking run under `<outputs_path>/tracking/<experiment>/`.

    The span and the signal come from the file name -- `tracking_io.tracking_filename`
    builds it and this reads it back -- and the satellite list from the file's own
    group names, which costs an open and no array reads.

    `collects_dir`, when given, supplies the `EXPERIMENT_INDEX` each run belongs
    to, so a reader can set notebook 02's configuration from the table alone.
    """
    import h5py

    tracking_dir = Path(outputs_path) / "tracking"
    if not tracking_dir.is_dir():
        return []

    indices: dict[str, int] = {}
    if collects_dir is not None:
        indices = {
            name: i
            for i, name in enumerate(
                collect_metadata_utils.list_experiment_names(Path(collects_dir))
            )
        }

    runs: list[TrackingRunInfo] = []
    for path in sorted(tracking_dir.glob("*/*.h5")):
        # macOS writes `._name` AppleDouble sidecars beside every file on an
        # exFAT volume; they match the glob and are not tracking files.
        if path.name.startswith("._"):
            continue
        match = _TRACKING_FILENAME.match(path.name)
        if match is None:
            continue
        experiment_name = path.parent.name
        try:
            with h5py.File(path, "r") as f:
                signal_ids = sorted(f.keys())
        except Exception:
            signal_ids = []
        start_s, end_s = float(match["start"]), float(match["end"])
        runs.append(
            TrackingRunInfo(
                path=path,
                experiment_index=indices.get(experiment_name),
                experiment_name=experiment_name,
                collect_label=match["label"],
                signal_type_id=match["signal"],
                start_offset_ms=start_s * 1e3,
                duration_ms=(end_s - start_s) * 1e3,
                signal_ids=signal_ids,
            )
        )
    return runs


def print_tracking_catalog(
    outputs_path: str | Path, collects_dir: str | Path | None = None
) -> list[TrackingRunInfo]:
    """
    Print every tracking run already on disk, and return them.

    The columns are notebook 02's four configuration values plus what the run
    contains, so choosing a run is reading one line across.
    """
    runs = list_tracking_runs(outputs_path, collects_dir)
    tracking_dir = Path(outputs_path) / "tracking"
    if not runs:
        print(
            f"No tracking runs under {tracking_dir}. Run notebook 01 to completion "
            "first -- its final cell writes one."
        )
        return runs

    rows, previous = [], None
    for run in runs:
        # Every PRN, not a head and a count.  Which satellites a run holds is the
        # reason to pick one run over another, and "+4" is exactly the part that
        # would decide it.
        prns = ",".join(run.signal_ids)
        rows.append(
            [
                run.experiment_index if run.experiment_name != previous else "",
                run.experiment_name if run.experiment_name != previous else "",
                run.collect_label,
                run.signal_type_id,
                f"{run.start_offset_ms:,.0f}",
                f"{run.duration_ms:,.0f}",
                len(run.signal_ids),
                prns,
            ]
        )
        previous = run.experiment_name

    tables.print_table(
        ["exp", "experiment", "collect", "SIGNAL_ID", "START_OFFSET_MS",
         "TRACK_DURATION_MS", "SVs", "acquired"],
        rows,
        aligns="><<<>>><",
        caption=f"Tracking runs under {tracking_dir}:",
    )
    return runs


def select_channel(
    collects_dir: str | Path, experiment_index: int, channel_index: int = 0
) -> Channel:
    """
    The channel at those two indices, or an error naming what is there instead.

    Selecting by (experiment, channel) rather than by (experiment, collect, band)
    is what keeps the two lists from being crossed.  A collect's id and a band id
    are independently sorted, so the *n*th collect need not carry the *n*th band;
    a channel is one collect and one band already paired, so there is nothing left
    to get wrong.
    """
    channels = list_channels(collects_dir)
    if not channels:
        raise FileNotFoundError(f"no readable experiments under {collects_dir}.")

    in_experiment = [c for c in channels if c.experiment_index == experiment_index]
    if not in_experiment:
        names = sorted({(c.experiment_index, c.experiment_name) for c in channels})
        available = "\n  ".join(f"{i}  {name}" for i, name in names)
        raise IndexError(
            f"EXPERIMENT_INDEX={experiment_index} matches no experiment. "
            f"Available:\n  {available}"
        )
    for channel in in_experiment:
        if channel.channel_index == channel_index:
            return channel
    available = ", ".join(
        f"{c.channel_index} ({c.collect_id}: {c.band_id})" for c in in_experiment
    )
    raise IndexError(
        f"CHANNEL_INDEX={channel_index} is not a channel of "
        f"{in_experiment[0].experiment_name!r}. Available: {available}"
    )


def select_channel_for_band(
    collects_dir: str | Path, experiment_index: int, band_id: str
) -> Channel:
    """
    The channel of that experiment carrying `band_id` -- what notebook 01 selects.

    A signal knows the band it rides on, so there is only one choice left to make
    and it is the experiment.  Fails with the bands the experiment actually holds,
    since "this collect has no L2" is the common way to configure this wrong.
    """
    channels = list_channels(collects_dir)
    in_experiment = [c for c in channels if c.experiment_index == experiment_index]
    if not in_experiment:
        names = sorted({(c.experiment_index, c.experiment_name) for c in channels})
        available = "\n  ".join(f"{i}  {name}" for i, name in names)
        raise IndexError(
            f"EXPERIMENT_INDEX={experiment_index} matches no experiment. "
            f"Available:\n  {available}"
        )
    for channel in in_experiment:
        if channel.band_id == band_id:
            return channel
    bands = sorted({c.band_id for c in in_experiment})
    carriers = sorted(
        {
            (c.experiment_index, c.experiment_name)
            for c in channels
            if c.band_id == band_id
        }
    )
    where = (
        "Experiments carrying it:\n  "
        + "\n  ".join(f"{i}  {name}" for i, name in carriers)
        if carriers
        else f"No experiment under {collects_dir} carries it."
    )
    raise RuntimeError(
        f"experiment {in_experiment[0].experiment_name!r} carries bands {bands}, "
        f"not {band_id!r}. Either choose a signal on one of those bands, or set "
        f"EXPERIMENT_INDEX to an experiment that has it. {where}"
    )
