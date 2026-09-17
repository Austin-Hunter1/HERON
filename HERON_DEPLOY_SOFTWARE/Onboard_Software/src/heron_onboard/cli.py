"""The ``heron-onboard`` command.

Subcommands:

- ``run --config FILE``: the flight supervisor (what systemd runs).
- ``run --config FILE --fake-capture``: the same loop without SDRs,
  for link tests on the bench.
- ``record --config FILE --seconds N``: a manual recording with no
  ground link (TEST_BENCH_ROUTINE section 3).
- ``check-config --config FILE``: validate the config and print the
  data-rate sizing.
- ``rebuild-metadata FLIGHT_DIR``: rebuild ``metadata.json`` totals and
  ``metadata.yml`` from the segment sidecars (after a power loss).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from heron_common.config import ConfigError
from heron_common.logging_setup import setup_logging
from heron_common.protocol import make_transport
from heron_common.version import get_git_hash

from heron_onboard.capture import CaptureManager, FakeCaptureBackend, RecorderBackend
from heron_onboard.config import OnboardConfig, load_onboard_config
from heron_onboard.disk_monitor import DiskMonitor
from heron_onboard.health import HealthMonitor
from heron_onboard.supervisor import Supervisor

log = logging.getLogger("heron_onboard")


def _load(path: Path) -> OnboardConfig:
    try:
        return load_onboard_config(path)
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)


def _build_supervisor(cfg: OnboardConfig, fake_capture: bool) -> Supervisor:
    version = get_git_hash()
    backend = FakeCaptureBackend(cfg.sdr) if fake_capture else RecorderBackend(cfg.sdr, cfg.capture)
    capture = CaptureManager(cfg, backend, version)
    disk = DiskMonitor(cfg.disk.data_root, cfg.disk.min_free_gb, cfg.disk.min_free_pct)
    health = HealthMonitor(cfg.health.temp_labels)
    transport = make_transport(cfg.link, "onboard")
    return Supervisor(cfg, transport, capture, disk, health, version)


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    setup_logging(cfg.general.log_level, cfg.general.log_dir, "heron-onboard")
    if args.fake_capture:
        log.warning("FAKE capture backend in use: no SDR data is recorded")
    if not args.fake_capture and not cfg.capture.recorder_binary.exists():
        log.error(
            "recorder binary not found: %s (build it, see recorder/README.md)",
            cfg.capture.recorder_binary,
        )
        return 2
    _build_supervisor(cfg, args.fake_capture).run(max_seconds=args.max_seconds)
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    """Manual bench recording: start, wait N seconds, stop."""
    cfg = _load(args.config)
    setup_logging(cfg.general.log_level, cfg.general.log_dir, "heron-onboard")
    version = get_git_hash()
    backend = (
        FakeCaptureBackend(cfg.sdr) if args.fake_capture else RecorderBackend(cfg.sdr, cfg.capture)
    )
    capture = CaptureManager(cfg, backend, version)
    from heron_onboard.state_machine import make_flight_id

    flight_id = make_flight_id(time.time(), cfg.control.flight_id_prefix, args.flight_id)
    capture.start(flight_id, time.time(), "manual record command")
    deadline = time.time() + args.seconds
    try:
        while time.time() < deadline:
            time.sleep(1.0)
            statuses = capture.poll()
            line = "  ".join(
                f"{s.id}: {'RUN' if s.streaming else 'off'} ovf={s.overflows} "
                f"seg={s.segment} {s.rate_mbps:.1f}MB/s{' FAULT ' + s.fault if s.fault else ''}"
                for s in statuses
            )
            print(line, flush=True)
            if capture.running_count() == 0:
                log.error("all recorders exited early")
                break
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        capture.stop(time.time(), "manual record end")
    return 0


def cmd_check_config(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    rate = cfg.aggregate_bytes_per_second
    print(f"Config OK: {args.config}")
    print(f"SDRs: {len(cfg.sdr)}; channels: {sum(len(s.channels) for s in cfg.sdr)}")
    for s in cfg.sdr:
        print(
            f"  {s.id}: {s.model} serial={s.serial} {s.sample_rate_hz / 1e6:.3f} Msps "
            f"x{len(s.channels)} ch = {s.bytes_per_second / 1e6:.1f} MB/s "
            f"(clock {s.clock_source}, time {s.time_source})"
        )
    print(
        f"Aggregate disk rate: {rate / 1e6:.1f} MB/s at sc8 "
        "(SATA III ceiling ~550 MB/s; keep 2x margin)"
    )
    print(
        f"Link transport: {cfg.link.transport}; fallback: {cfg.control.fallback_mode} "
        f"(grace {cfg.control.link_grace_s:.0f} s)"
    )
    print(
        f"Data root: {cfg.disk.data_root}; stop below {cfg.disk.min_free_gb} GB "
        f"or {cfg.disk.min_free_pct}%"
    )
    return 0


def cmd_rebuild_metadata(args: argparse.Namespace) -> int:
    from heron_onboard.capture.metadata_yaml import rebuild_flight_metadata

    try:
        yaml_path, count = rebuild_flight_metadata(args.flight_dir)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Rebuilt {yaml_path} from {count} segment sidecar(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="heron-onboard", description="HERON payload supervisor")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run the flight supervisor")
    p_run.add_argument("--config", type=Path, required=True)
    p_run.add_argument("--fake-capture", action="store_true", help="no SDRs; link test only")
    p_run.add_argument(
        "--max-seconds", type=float, default=0.0, help="stop after N seconds (0 = run forever)"
    )
    p_run.set_defaults(func=cmd_run)

    p_rec = sub.add_parser("record", help="manual bench recording, no ground link")
    p_rec.add_argument("--config", type=Path, required=True)
    p_rec.add_argument("--seconds", type=float, default=60.0)
    p_rec.add_argument("--flight-id", default=None)
    p_rec.add_argument("--fake-capture", action="store_true")
    p_rec.set_defaults(func=cmd_record)

    p_chk = sub.add_parser("check-config", help="validate a config file")
    p_chk.add_argument("--config", type=Path, required=True)
    p_chk.set_defaults(func=cmd_check_config)

    p_rb = sub.add_parser("rebuild-metadata", help="rebuild metadata files from sidecars")
    p_rb.add_argument("flight_dir", type=Path)
    p_rb.set_defaults(func=cmd_rebuild_metadata)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
