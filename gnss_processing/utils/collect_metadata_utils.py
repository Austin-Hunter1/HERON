import logging
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional

from dataclasses import dataclass
from .sample_streaming import SampleParameters

@dataclass
class BandConfiguration:
    center_freq: float
    # 0.0 means the recording is already centred on this band (see notebook 00).
    inter_freq: float = 0.0

    @staticmethod
    def from_dict(band_dict: Dict[str, Any]) -> "BandConfiguration":
        if "center_freq" not in band_dict:
            raise ValueError("Band configuration dictionary must contain 'center_freq' key.")
        center_freq = band_dict["center_freq"]
        inter_freq = band_dict.get("inter_freq", 0.0)
        return BandConfiguration(center_freq=center_freq, inter_freq=inter_freq)

@dataclass
class ChannelConfiguration:
    samp_rate: float
    band_ids: List[str]
    sample_params: SampleParameters

def parse_channel_configurations_from_dict(config_dict: Dict[str, Dict[str, Any]], verbose: bool = False) -> Dict[str, ChannelConfiguration]:
    """
    NOTE: Receiver configurations can optionally specify an "inherit" key to indicate that they should inherit and override parameters from a parent configuration. For example:

    "receiver_configurations": {
        "base_config": {
            "samp_rate": 12500000,
            "bands": ["band1", "band2"],
            "sample_format": {
                "is_i_lsb": True,
                "is_integer": True,
                "is_signed": True,
                "is_complex": True,
                "bit_depth": 16,
            },
        },
        "collect1_config": {
            "inherit": "base_config",
            "bands": ["band1"],  # override bands to only include band1
        },
    }

    NOTE: the base config may fail to parse if it doesn't specify all required fields.  If a config parse fails, we omit it from
    the parsed configurations.  By default, we will not warn the user (so they don't see warnings due to base/default configs failing),
    but warnings may be turned on for debug purposes using the `verbose` flag.
    """
    channel_configs = {}
    for channel_config_id, channel_config_dict in config_dict.items():
        if "inherit" in channel_config_dict:
            parent_id = channel_config_dict["inherit"]
            if parent_id not in config_dict:
                raise ValueError(f"Parent receiver configuration ID '{parent_id}' not found in channel configurations metadata.")
            parent_config: Dict[str, Any] = config_dict[parent_id]
            merged_config = parent_config.copy()
            merged_config.update(channel_config_dict)
            del merged_config["inherit"]
            channel_config_dict = merged_config
        try:
            if "samp_rate" not in channel_config_dict:
                raise ValueError(f"Channel configuration '{channel_config_id}' is missing 'samp_rate' key.")
            if "bands" not in channel_config_dict:
                raise ValueError(f"Channel configuration '{channel_config_id}' is missing 'bands' key.")
            if "sample_format" not in channel_config_dict:
                raise ValueError(f"Channel configuration '{channel_config_id}' is missing 'sample_format' key.")
            samp_rate = channel_config_dict["samp_rate"]
            band_ids = sorted(list(channel_config_dict["bands"]))
            sample_format_dict = channel_config_dict["sample_format"]
            sample_params = SampleParameters.from_dict(sample_format_dict)
            channel_configs[channel_config_id] = ChannelConfiguration(samp_rate=samp_rate, band_ids=band_ids, sample_params=sample_params)
        except Exception as e:
            if verbose:
                logging.warning(e)
    return channel_configs

@dataclass
class CollectMetadata:
    channel_config_id: str
    filename: str
    notes: Optional[str] = None

    @staticmethod
    def from_dict(collect_dict: Dict[str, Any]) -> "CollectMetadata":
        if "channel_config" not in collect_dict:
            raise ValueError("Collect metadata dictionary must contain 'channel_config' key.")
        if "filename" not in collect_dict:
            raise ValueError("Collect metadata dictionary must contain 'filename' key.")
        channel_config_id = collect_dict["channel_config"]
        filename = collect_dict["filename"]
        notes = collect_dict.get("notes")
        return CollectMetadata(channel_config_id=channel_config_id, filename=filename, notes=notes)

@dataclass
class ExperimentMetadata:
    band_configurations: Dict[str, BandConfiguration]
    channel_configurations: Dict[str, ChannelConfiguration]
    collects: Dict[str, CollectMetadata]
    notes: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None

    @staticmethod
    def from_dict(metadata_dict: Dict[str, Any], verbose: bool = False) -> "ExperimentMetadata":
        if "band_configurations" not in metadata_dict:
            raise ValueError("Metadata dictionary must contain 'band_configurations' key.")
        if "channel_configurations" not in metadata_dict:
            raise ValueError("Metadata dictionary must contain 'channel_configurations' key.")
        if "collections" not in metadata_dict:
            raise ValueError("Metadata dictionary must contain 'collections' key.")
        band_configurations = {band_id: BandConfiguration.from_dict(band_dict) for band_id, band_dict in metadata_dict["band_configurations"].items()}
        channel_configurations = parse_channel_configurations_from_dict(metadata_dict["channel_configurations"], verbose)
        collects = {collect_id: CollectMetadata.from_dict(collect_dict) for collect_id, collect_dict in metadata_dict["collections"].items()}
        return ExperimentMetadata(band_configurations=band_configurations, channel_configurations=channel_configurations, collects=collects)


    @property
    def collect_ids(self) -> List[str]:
        return sorted(list(self.collects.keys()))

    @property
    def channel_ids(self) -> List[str]:
        return sorted(list(self.channel_configurations.keys()))
    
    @property
    def band_ids(self) -> List[str]:
        return sorted(list(self.band_configurations.keys()))
    
def parse_experiment_metadata(metadata_dict: Dict[str, Any], verbose: bool = False) -> ExperimentMetadata:
    """
    Retrieve the receiver configuration given a metadata dictionary.

    metadata dictionary has the following structure:

    {
        "collections": {
            "<collect_id>": {
                "rx_config": "<rx_config_id>",
                "filename": "<data_filename>",
                ...}
            }
        "band_configurations": {
            "<band_id>": {
                "center_freq": ...,
                "inter_freq": ...,
            }
        },
        "receiver_configurations": {
            "<rx_config_id>": {
                "samp_rate": 12500000,
                "bands": [...],
                "sample_format": {
                    "is_i_lsb": ...,
                    "is_integer": ...,
                    "is_signed": ...,
                    "is_complex": ...,
                    "bit_depth": ...,
                },
            }
        

    Args:
        metadata_dict (Dict[str, Any]): The metadata dictionary containing collect information.
        collect_name (str): The name of the collect.

    Returns:
        Dict[str, Dict[str, Any]]: The receiver configuration dictionaries for each collect.
    """
    return ExperimentMetadata.from_dict(metadata_dict, verbose=verbose)

def print_experiment_available_collects_and_bands(metadata: ExperimentMetadata, print_prefix: str = ""):
    print(print_prefix + f"Available collects:")
    for collect_id in metadata.collect_ids:
        collect = metadata.collects[collect_id]
        channel_config_id = collect.channel_config_id
        band_ids = metadata.channel_configurations[channel_config_id].band_ids
        print(print_prefix + f"  {collect_id}: " + " ".join(band_ids))

def load_experiment_metadata_from_file(metadata_filepath: str | Path, print_summary: bool = False, print_prefix: str = "", verbose_parsing: bool = False) -> ExperimentMetadata:
    """
    Load the experiment metadata from a YAML file.

    Args:
        metadata_filepath (str | Path): The file path to the metadata YAML file.
    Returns:
        ExperimentMetadata: The parsed experiment metadata.
    """
    with open(metadata_filepath, "r") as f:
        metadata_dict = yaml.safe_load(f)
    metadata = parse_experiment_metadata(metadata_dict, verbose=verbose_parsing)
    if print_summary:
        print_experiment_available_collects_and_bands(metadata, print_prefix)
    return metadata


def list_experiment_names(collects_dir: Path) -> List[str]:
    """
    Experiment names available under `<collects_dir>/`, each expected
    to contain a `metadata.yml` (see `load_experiment_metadata_from_file`).
    """
    return sorted(fp.name for fp in collects_dir.iterdir() if fp.is_dir())


@dataclass
class ResolvedCollect:
    """Everything needed to open one band of one collect and stream its samples."""

    experiment_name: str
    metadata: ExperimentMetadata
    collect_id: str
    band_id: str
    collect_filepath: Path
    samp_rate: float
    sample_params: SampleParameters
    inter_freq_hz: float

    def __repr__(self) -> str:
        return (
            f"ResolvedCollect(\n"
            f"  experiment_name={self.experiment_name!r},\n"
            f"  collect_id={self.collect_id!r},\n"
            f"  band_id={self.band_id!r},\n"
            f"  collect_filepath={self.collect_filepath!r},\n"
            f"  samp_rate={self.samp_rate!r},\n"
            f"  sample_params={self.sample_params!r},\n"
            f"  inter_freq_hz={self.inter_freq_hz!r},\n"
            f")"
        )


def resolve_collect(
    collects_dir: Path,
    experiment_name: str,
    collect_id: str,
    band_id: str,
    print_summary: bool = False,
) -> ResolvedCollect:
    """
    Load `<collects_dir>/<experiment_name>/metadata.yml` and resolve
    `collect_id`/`band_id` (see `list_experiment_names` and, once loaded, the
    metadata's own `.collect_ids`/`.band_ids`) into a concrete file + sample
    format, replacing the "select experiment/collect/band by hardcoded list
    index" boilerplate every notebook otherwise repeats.
    """
    experiment_dir = collects_dir / experiment_name
    metadata = load_experiment_metadata_from_file(
        experiment_dir / "metadata.yml", print_summary=print_summary
    )

    collect_config = metadata.collects[collect_id]
    channel_config = metadata.channel_configurations[collect_config.channel_config_id]
    band_config = metadata.band_configurations[band_id]

    if band_id not in channel_config.band_ids:
        raise ValueError(
            f"Collect {collect_id!r} (channel config {collect_config.channel_config_id!r}) "
            f"does not carry band {band_id!r}; it carries {channel_config.band_ids}. "
            "`collect_ids` and `band_ids` are independently sorted lists, so indexing them "
            "with the same index does not guarantee they refer to the same recording."
        )

    return ResolvedCollect(
        experiment_name=experiment_name,
        metadata=metadata,
        collect_id=collect_id,
        band_id=band_id,
        collect_filepath=experiment_dir / collect_config.filename,
        samp_rate=channel_config.samp_rate,
        sample_params=channel_config.sample_params,
        inter_freq_hz=band_config.inter_freq,
    )