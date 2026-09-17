"""Per-flight ``metadata.yml`` in the SDR team's schema (D-022).

The data-processing side describes captures with a YAML file of this
shape (example from the team, 2026-09-14)::

    band_configurations:
      L5: {center_freq: 1176450000.0, inter_freq: 0.0}
    channel_configurations:
      b200_l5:
        samp_rate: 25000000.0
        bands: [L5]
        sample_format: {bit_depth: 16, is_complex: true, is_integer: true,
                        is_signed: true, is_i_lsb: true}
    collections:
      20260914_150800_l5_rhcp_120s:
        channel_config: b200_l5
        filename: 20260914_150800_l5_rhcp_capture.dat
        notes: "... NOTE: capture log reported an overflow ..."

HERON writes the same file next to its own ``metadata.json`` so the
processing tools read a flight with no conversion:

- one ``band_configurations`` entry per band in the ``[bands]`` config
  table that a channel uses;
- one ``channel_configurations`` entry per SDR channel, keyed
  ``<sdr_id>_<channel_id>``;
- one ``collections`` entry per segment file, built from the segment
  sidecar the recorder writes, with an overflow note when samples were
  lost in that segment.

``rebuild_flight_metadata`` regenerates both metadata files from the
sidecars after a power loss (the config copy in ``metadata.json`` is
the source of the channel settings).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from heron_onboard.capture.metadata import METADATA_FILENAME, write_metadata
from heron_onboard.config import ChannelConfig, OnboardConfig, SdrConfig, validate_onboard_dict

log = logging.getLogger(__name__)

METADATA_YAML_FILENAME = "metadata.yml"


def channel_config_key(sdr: SdrConfig, ch: ChannelConfig) -> str:
    """The ``channel_configurations`` key of one channel."""
    return f"{sdr.id}_{ch.id}"


def sample_format(cpu_format: str) -> dict[str, Any]:
    """The team's sample-format flags for our file format.

    Both ``sc8`` and ``sc16`` are signed integer, complex, interleaved
    with I first (``is_i_lsb``), little-endian.
    """
    return {
        "bit_depth": 8 if cpu_format == "sc8" else 16,
        "is_complex": True,
        "is_integer": True,
        "is_signed": True,
        "is_i_lsb": True,
    }


def scan_sidecars(flight_dir: Path) -> list[dict[str, Any]]:
    """Read every segment sidecar under ``flight_dir``.

    Sidecars sit at ``<sdr_id>/<channel_id>/<file>.json``. Unreadable
    ones are logged and skipped, so one bad file cannot block the rest.
    The result is sorted by SDR, channel, and segment number.
    """
    sidecars = []
    for path in sorted(flight_dir.glob("*/*/*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("skipping sidecar %s: %s", path, exc)
            continue
        if not isinstance(data, dict) or "channel_id" not in data:
            continue
        data["_relpath"] = path.relative_to(flight_dir).with_suffix("").as_posix()
        sidecars.append(data)
    sidecars.sort(
        key=lambda d: (d.get("sdr_id", ""), d.get("channel_id", ""), int(d.get("seq", 0)))
    )
    return sidecars


def _band_and_centre(config: OnboardConfig, ch: ChannelConfig) -> tuple[str, float]:
    """Return the band name and its carrier centre for a channel.

    A channel with no ``band`` uses its own id as the band name and its
    tuned frequency as the centre, so the file stays valid.
    """
    if ch.band and ch.band in config.bands:
        return ch.band, config.bands[ch.band]
    return ch.id, ch.center_freq_hz


def _collection_notes(sdr: SdrConfig, ch: ChannelConfig, sc: dict[str, Any]) -> str:
    """Human-readable notes for one segment, in the team's style."""
    rate = float(sc.get("sample_rate_hz", sdr.sample_rate_hz))
    n = int(sc.get("num_samples", 0))
    duration = n / rate if rate > 0 else 0.0
    timebase = "PPS-aligned UTC" if sdr.pps_aligned else "host clock, not PPS-aligned (see Q-013)"
    label = ch.antenna_label or ch.antenna
    bw_mhz = float(sc.get("bandwidth_hz", ch.bandwidth_hz)) / 1e6
    partial = "" if sc.get("complete", True) else " (partial, last of the recording)"
    parts = [
        f"HERON {sdr.model} serial {sdr.serial}, channel {ch.id} ({label}), "
        f"gain {sc.get('gain_db', ch.gain_db)} dB, bw {bw_mhz:g} MHz, "
        f"port {sc.get('antenna', ch.antenna)}.",
        f"Segment {int(sc.get('seq', 0))}{partial}: {n} samples ({duration:.3f} s), "
        f"first sample device time {float(sc.get('first_sample_device_time', -1.0)):.6f} s "
        f"({timebase}).",
    ]
    overflows = int(sc.get("overflows_in_segment", 0))
    drops = int(sc.get("host_drops_in_segment", 0))
    if overflows:
        parts.append(
            f"NOTE: {overflows} overflow indication(s) in this segment (samples lost between "
            "the SDR and the host) -- expect possible discontinuities in the stream."
        )
    if drops:
        parts.append(
            f"NOTE: {drops} host-side drop(s) in this segment (disk fell behind) -- "
            "expect gaps of 10 ms each."
        )
    return " ".join(parts)


def build_metadata_yaml(
    config: OnboardConfig, file_prefix: str, sidecars: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build the YAML document as a dict (insertion order kept)."""
    bands: dict[str, Any] = {}
    channels: dict[str, Any] = {}
    lookup: dict[tuple[str, str], tuple[SdrConfig, ChannelConfig]] = {}
    for sdr in config.sdr:
        for ch in sdr.channels:
            band, centre = _band_and_centre(config, ch)
            bands.setdefault(
                band,
                {"center_freq": float(centre), "inter_freq": float(ch.center_freq_hz - centre)},
            )
            channels[channel_config_key(sdr, ch)] = {
                "samp_rate": float(sdr.sample_rate_hz),
                "bands": [band],
                "sample_format": sample_format(config.capture.cpu_format),
            }
            lookup[(sdr.id, ch.id)] = (sdr, ch)

    collections: dict[str, Any] = {}
    for sc in sidecars:
        pair = lookup.get((str(sc.get("sdr_id")), str(sc.get("channel_id"))))
        if pair is None:
            log.warning(
                "sidecar for unknown channel %s/%s skipped", sc.get("sdr_id"), sc.get("channel_id")
            )
            continue
        sdr, ch = pair
        key = f"{file_prefix}_{sdr.id}_{ch.id}_{int(sc.get('seq', 0)):05d}"
        collections[key] = {
            "channel_config": channel_config_key(sdr, ch),
            "filename": sc["_relpath"],
            "notes": _collection_notes(sdr, ch, sc),
        }

    return {
        "band_configurations": bands,
        "channel_configurations": channels,
        "collections": collections,
    }


def write_metadata_yaml(flight_dir: Path, config: OnboardConfig, file_prefix: str) -> Path:
    """Scan the sidecars and write ``metadata.yml`` atomically."""
    doc = build_metadata_yaml(config, file_prefix, scan_sidecars(flight_dir))
    target = flight_dir / METADATA_YAML_FILENAME
    tmp = flight_dir / (METADATA_YAML_FILENAME + ".tmp")
    tmp.write_text(
        yaml.safe_dump(
            doc, sort_keys=False, default_flow_style=False, allow_unicode=True, width=100
        ),
        encoding="utf-8",
    )
    tmp.replace(target)
    return target


def rebuild_flight_metadata(flight_dir: Path) -> tuple[Path, int]:
    """Rebuild ``metadata.yml`` (and ``metadata.json`` totals) from sidecars.

    Use after a power loss, when the supervisor never wrote the final
    metadata. The config copy inside ``metadata.json`` supplies the
    channel settings. Return the YAML path and the sidecar count.
    """
    meta_path = flight_dir / METADATA_FILENAME
    if not meta_path.exists():
        raise ValueError(f"{meta_path} not found; cannot rebuild without the config copy")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    config = validate_onboard_dict(meta["config"], source=str(meta_path))
    sidecars = scan_sidecars(flight_dir)
    if meta.get("stop_unix") is None:
        totals: dict[str, dict[str, Any]] = {}
        last_host_time = 0.0
        for sc in sidecars:
            t = totals.setdefault(
                str(sc.get("sdr_id")),
                {"samples": 0, "overflows": 0, "host_drops": 0, "segments": 0, "fault": None},
            )
            t["samples"] += int(sc.get("num_samples", 0))
            t["overflows"] += int(sc.get("overflows_in_segment", 0))
            t["host_drops"] += int(sc.get("host_drops_in_segment", 0))
            t["segments"] = max(t["segments"], int(sc.get("seq", 0)) + 1)
            last_host_time = max(last_host_time, float(sc.get("first_sample_host_time", 0.0)))
        meta["totals"] = totals
        meta["stop_reason"] = "unknown: rebuilt from sidecars (power loss?)"
        meta["stop_unix"] = last_host_time or None
        meta["rebuilt"] = True
        write_metadata(flight_dir, meta)
    path = write_metadata_yaml(flight_dir, config, meta["file_prefix"])
    return path, len(sidecars)
