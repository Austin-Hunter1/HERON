"""Make the data files for the in-browser ground station simulation.

The simulation at herongnss.org/sim/ runs in JavaScript only (no
server). It must still behave like the real software, so this tool
takes two things from the real Python code and writes them as JSON:

1. ``sim_config.json``: the values the simulation uses (link health
   limits, fallback rule, disk limits, the SDR list, ...). They come
   from the real config templates, validated with the real pydantic
   models. The JavaScript does not hard-code them.
2. ``snapshot_template.json``: one real ``/api/state`` snapshot from
   the real dashboard, run against the real fake payload (demo mode).
   The JavaScript checks its own snapshot against the key layout of
   this file at start and shows a warning when they differ.

``Base_Software/tests/test_web_sim_data.py`` runs this tool and fails
when the committed files are out of date. Then run, in
HERON_DEPLOY_SOFTWARE/:

    uv run python ../website/tools/make_sim_data.py
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "HERON_DEPLOY_SOFTWARE"
ONBOARD_TEMPLATE = DEPLOY / "Onboard_Software" / "config" / "onboard.example.toml"
BASE_TEMPLATE = DEPLOY / "Base_Software" / "config" / "base.example.toml"
OUT_DIR = REPO / "website" / "sim"

# Run the real demo this long at most, while it waits for telemetry and acks.
DEMO_TIMEOUT_S = 10.0


def build_config() -> dict[str, Any]:
    """Read and validate the real templates; keep what the simulation needs."""
    from heron_base.config import load_base_config
    from heron_onboard.config import load_onboard_config

    onboard = load_onboard_config(ONBOARD_TEMPLATE)
    base = load_base_config(BASE_TEMPLATE)
    return {
        "source": {
            "onboard": ONBOARD_TEMPLATE.relative_to(REPO).as_posix(),
            "base": BASE_TEMPLATE.relative_to(REPO).as_posix(),
        },
        "base": {
            "link_health": base.link_health.model_dump(mode="json"),
            "refresh_ms": base.web.refresh_ms,
            "event_lines": base.display.event_lines,
            "gnss_stale_fix_s": base.gnss.stale_fix_s,
        },
        "onboard": {
            "tick_s": onboard.general.tick_s,
            "telemetry": onboard.telemetry.model_dump(mode="json"),
            "control": onboard.control.model_dump(mode="json"),
            "disk": {
                "min_free_gb": onboard.disk.min_free_gb,
                "min_free_pct": onboard.disk.min_free_pct,
                "check_interval_s": onboard.disk.check_interval_s,
            },
            "segment_seconds": onboard.capture.segment_seconds,
            "temp_labels": list(onboard.health.temp_labels),
            "sdrs": [
                {
                    "id": s.id,
                    "model": s.model,
                    "sample_rate_hz": s.sample_rate_hz,
                    "bytes_per_second": s.bytes_per_second,
                    "clock_source": s.clock_source,
                    "time_source": s.time_source,
                    "channels": [{"id": c.id, "band": c.band} for c in s.channels],
                }
                for s in onboard.sdr
            ],
        },
    }


class _GnssStub:
    """A GNSS receiver that always has a fix, so the snapshot has GNSS keys."""

    def status(self):
        from heron_base.gnss.nmea import GgaFix
        from heron_base.gnss.receiver import GnssStatus

        fix = GgaFix("120000.00", 4, 18, 0.7, 40.0, -105.25, 1600.0)
        return GnssStatus(
            port_open=True,
            fix=fix,
            last_fix_time=time.time(),
            bytes_logged=1,
            rtcm_frames=1,
            rtcm_types={1005: 1},
            log_path="gnss.log",
        )


def build_snapshot_template() -> dict[str, Any]:
    """Run the real dashboard on the real fake payload; return one snapshot.

    The snapshot is taken after a START ack, so the telemetry, the SDR
    list and the command history all have entries.
    """
    from heron_base.config import load_base_config
    from heron_base.demo import start_fake_payload
    from heron_base.link_client import LinkClient
    from heron_base.web import WebDashboard
    from heron_common.protocol import CommandName

    base = load_base_config(BASE_TEMPLATE)
    sup, thread, ground_t = start_fake_payload()
    client = LinkClient(ground_t, base.link_health, None)
    dash = WebDashboard(base, client, gnss=_GnssStub())  # type: ignore[arg-type]
    try:
        deadline = time.time() + DEMO_TIMEOUT_S
        sent = False
        while time.time() < deadline:
            dash.step()
            if client.latest is not None and not sent:
                client.send_command(CommandName.START, {"flight_id": "template"})
                sent = True
            if sent and client.history and client.latest and client.latest.sdrs:
                break
            time.sleep(0.05)
        snap = dash.build_snapshot()
    finally:
        sup.request_stop()
        thread.join(timeout=5)
    if snap["telemetry"] is None or not snap["history"]:
        raise SystemExit("the demo gave no telemetry or no ack; cannot build the template")
    return snap


def shape(value: Any) -> Any:
    """Return the key layout of a JSON value: dict keys, and the first list item."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [shape(value[0])] if value and isinstance(value[0], (dict, list)) else []
    return None


def main() -> None:
    """Write both files."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "sim_config.json").write_text(json.dumps(build_config(), indent=2) + "\n")
    (args.out / "snapshot_template.json").write_text(
        json.dumps(build_snapshot_template(), indent=2, default=str) + "\n"
    )
    print(f"wrote {args.out / 'sim_config.json'} and snapshot_template.json")


if __name__ == "__main__":
    main()
