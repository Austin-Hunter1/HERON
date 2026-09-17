"""Per-flight ``metadata.json`` (REQUIREMENTS O5, DATA_FORMATS.md).

Every recording is self-describing. The metadata file records the
config in use, the SDR serial numbers, the software version, the UTC
start and stop times, and the timing scheme, so a post-processing
engineer needs nothing else to read the data.

The file is written at start (so a power loss still leaves it) and
rewritten at stop with the totals.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from heron_common.protocol import SdrStatus
from heron_common.timeutil import utc_iso

from heron_onboard.config import OnboardConfig

METADATA_VERSION = 1
METADATA_FILENAME = "metadata.json"


def build_metadata(
    config: OnboardConfig,
    flight_id: str,
    file_prefix: str,
    start_unix: float,
    version: str,
    timing: dict[str, Any],
    start_reason: str,
) -> dict[str, Any]:
    """Build the start-of-flight metadata dict."""
    return {
        "metadata_version": METADATA_VERSION,
        "flight_id": flight_id,
        "file_prefix": file_prefix,
        "software_version": version,
        "start_utc": utc_iso(start_unix),
        "start_unix": start_unix,
        "start_reason": start_reason,
        "stop_utc": None,
        "stop_unix": None,
        "timing": {
            "sync_epoch_unix": timing.get("sync_epoch"),
            "stream_start_unix": timing.get("start_time"),
            "per_sdr": {
                s.id: {
                    "clock_source": s.clock_source,
                    "time_source": s.time_source,
                    "pps_aligned": s.pps_aligned,
                }
                for s in config.sdr
            },
            "note": (
                "A recorder with a PPS time source sets its device clock to sync_epoch_unix + 1 "
                "at the PPS edge of that second. A recorder without PPS sets its clock from the "
                "host time (milliseconds). All recorders start streaming at stream_start_unix. "
                "Segment sidecar files carry the device time of their first sample. Units "
                "without PPS need a post-flight offset estimate (Q-013)."
            ),
        },
        "format": {
            "cpu_format": config.capture.cpu_format,
            "wire_format": config.capture.wire_format,
            "bytes_per_sample": 2 if config.capture.cpu_format == "sc8" else 4,
            "sample_layout": "interleaved I,Q per sample, little-endian, one file per channel",
            "segment_seconds": config.capture.segment_seconds,
            "file_pattern": (
                f"<sdr_id>/<channel_id>/{file_prefix}_<seq>.{config.capture.file_extension}"
            ),
            "sidecar": "same name with .json: first-sample device time, sample count, overflows",
        },
        "sdrs": [
            {
                "id": s.id,
                "model": s.model,
                "serial": s.serial,
                "sample_rate_hz": s.sample_rate_hz,
                "clock_source": s.clock_source,
                "time_source": s.time_source,
                "device_args": s.device_args,
                "channels": [c.model_dump() for c in s.channels],
            }
            for s in config.sdr
        ],
        "config": config.model_dump(mode="json"),
        "totals": {},
    }


def finalize_metadata(
    meta: dict[str, Any], stop_unix: float, statuses: list[SdrStatus]
) -> dict[str, Any]:
    """Add the stop time and per-SDR totals."""
    meta = dict(meta)
    meta["stop_utc"] = utc_iso(stop_unix)
    meta["stop_unix"] = stop_unix
    meta["totals"] = {
        s.id: {
            "samples": s.samples,
            "overflows": s.overflows,
            "host_drops": s.host_drops,
            "segments": s.segment,
            "fault": s.fault,
        }
        for s in statuses
    }
    return meta


def write_metadata(flight_dir: Path, meta: dict[str, Any]) -> Path:
    """Write ``metadata.json`` atomically (write temp, then rename)."""
    flight_dir.mkdir(parents=True, exist_ok=True)
    target = flight_dir / METADATA_FILENAME
    tmp = flight_dir / (METADATA_FILENAME + ".tmp")
    tmp.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    tmp.replace(target)
    return target
