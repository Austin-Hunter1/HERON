"""The ``heron-base`` command.

- ``tui --config FILE``: the live display with start/stop keys.
- ``send start|stop|status|ping --config FILE``: one command, wait for
  the ack, print it, exit (scripts and quick checks).
- ``monitor --config FILE``: print one telemetry line per second (for
  a log window or an SSH session with no TUI).
- ``demo``: the TUI against a fake payload, no hardware.
- ``check-config --config FILE``.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from heron_common.config import ConfigError
from heron_common.logging_setup import setup_logging
from heron_common.protocol import CommandName, make_transport

from heron_base.config import BaseConfig, load_base_config
from heron_base.flight_log import FlightLog
from heron_base.link_client import LinkClient

log = logging.getLogger("heron_base")


def _load(path: Path) -> BaseConfig:
    try:
        return load_base_config(path)
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)


def _client(cfg: BaseConfig, with_log: bool = True) -> LinkClient:
    transport = make_transport(cfg.link, "base")
    flight_log = FlightLog(cfg.general.flight_log_dir) if with_log else None
    return LinkClient(transport, cfg.link_health, flight_log, event_lines=cfg.display.event_lines)


def cmd_tui(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    # The TUI owns the terminal; file logging only.
    setup_logging(cfg.general.log_level, cfg.general.log_dir, "heron-base")
    logging.getLogger().handlers = [
        h
        for h in logging.getLogger().handlers
        if not isinstance(h, logging.StreamHandler) or hasattr(h, "baseFilename")
    ]
    from heron_base.gnss import GnssReceiver
    from heron_base.gnss.correction import make_sink
    from heron_base.tui.app import HeronBaseApp

    gnss = GnssReceiver(cfg.gnss, make_sink(cfg.gnss.correction)) if cfg.gnss.enabled else None
    HeronBaseApp(cfg, _client(cfg), gnss).run()
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    setup_logging("WARNING", cfg.general.log_dir, "heron-base")
    client = _client(cfg)
    client.open()
    cmd_args = {"flight_id": args.flight_id} if args.flight_id else {}
    pending = client.send_command(CommandName(args.name), cmd_args)
    ack = client.wait_for_ack(pending)
    client.close()
    if ack is None:
        print(f"{args.name}: no ack (link down?)")
        return 1
    print(f"{args.name}: {'ok' if ack.ok else 'REFUSED'} - {ack.message} (state {ack.state})")
    return 0 if ack.ok else 1


def cmd_monitor(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    setup_logging("WARNING", cfg.general.log_dir, "heron-base")
    client = _client(cfg)
    client.open()
    last_seq = None
    try:
        while True:
            client.poll()
            tlm = client.latest
            now = time.time()
            if tlm is not None and tlm.seq != last_seq:
                last_seq = tlm.seq
                sdrs = " ".join(
                    f"{s.id}:{'R' if s.streaming else '-'}/ovf{s.overflows}"
                    f"{'/F' if s.fault else ''}"
                    for s in tlm.sdrs
                )
                stamp = time.strftime("%H:%M:%S", time.gmtime(now))
                print(
                    f"{stamp}Z {client.health(now):9} {tlm.state:9} "
                    f"{tlm.flight_id or '-':18} {tlm.data_rate_mbps:6.1f}MB/s "
                    f"disk {tlm.disk_free_gb:7.1f}GB cpu {tlm.cpu_pct:3.0f}% {sdrs}",
                    flush=True,
                )
            time.sleep(cfg.link_health.poll_s)
    except KeyboardInterrupt:
        pass
    finally:
        client.close()
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from heron_base.demo import run_demo

    cfg = _load(args.config) if args.config else BaseConfig()
    setup_logging("INFO", cfg.general.log_dir, "heron-base-demo")
    logging.getLogger().handlers = [
        h for h in logging.getLogger().handlers if hasattr(h, "baseFilename")
    ]
    run_demo(cfg)
    return 0


def cmd_check_config(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    print(f"Config OK: {args.config}")
    print(f"Link transport: {cfg.link.transport}")
    print(
        f"Link health: degraded > {cfg.link_health.degraded_s} s, lost > {cfg.link_health.lost_s} s"
    )
    print(
        f"GNSS: {'enabled' if cfg.gnss.enabled else 'disabled'} {cfg.gnss.port} @ {cfg.gnss.baud}; "
        f"correction sink {cfg.gnss.correction.sink}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="heron-base", description="HERON ground station")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tui", help="live display with start/stop keys")
    p.add_argument("--config", type=Path, required=True)
    p.set_defaults(func=cmd_tui)

    p = sub.add_parser("send", help="send one command and wait for the ack")
    p.add_argument("name", choices=[c.value for c in CommandName])
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--flight-id", default=None)
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("monitor", help="print telemetry lines")
    p.add_argument("--config", type=Path, required=True)
    p.set_defaults(func=cmd_monitor)

    p = sub.add_parser("demo", help="TUI against a fake payload, no hardware")
    p.add_argument("--config", type=Path, default=None)
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("check-config", help="validate a config file")
    p.add_argument("--config", type=Path, required=True)
    p.set_defaults(func=cmd_check_config)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
