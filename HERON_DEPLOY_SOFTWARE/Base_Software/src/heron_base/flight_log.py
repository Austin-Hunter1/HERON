"""JSONL flight log: every telemetry, command, ack, and event (B5).

One file per base-station session. Each line is one JSON object with
``t`` (UTC ISO time), ``kind``, and the payload. A flight can be
replayed from this file (see TESTING.md, replay test).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from heron_common.timeutil import utc_iso, utc_stamp


class FlightLog:
    """Append-only JSONL writer."""

    def __init__(self, directory: Path, session_time: float | None = None) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"base_{utc_stamp(session_time)}.jsonl"
        self._fh = self.path.open("a", encoding="utf-8")
        self.lines = 0

    def write(self, kind: str, payload: dict[str, Any], now: float | None = None) -> None:
        record = {"t": utc_iso(now), "kind": kind, **payload}
        self._fh.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")
        self._fh.flush()
        self.lines += 1

    def close(self) -> None:
        self._fh.close()


def read_flight_log(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL flight log back into a list of dicts."""
    records = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records
