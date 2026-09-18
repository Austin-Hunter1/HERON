"""Demo mode: the full base station against a fake payload, no hardware.

Builds a loopback transport pair, runs the onboard supervisor with the
fake capture backend on a background thread, and starts a display on
the other end: the terminal UI by default, or the web page with
``web=True``. Use it to learn the displays and to test display changes.
"""

from __future__ import annotations

import logging
import tempfile
import threading
from pathlib import Path

from heron_common.protocol import LoopbackTransport

from heron_base.config import BaseConfig
from heron_base.link_client import LinkClient

log = logging.getLogger(__name__)


def start_fake_payload(disk_free_gb: float = 800.0):
    """Start the onboard supervisor with fake capture on a thread.

    Return ``(supervisor, thread, ground_transport)``. The caller owns
    the ground end of the loopback link and must call
    ``supervisor.request_stop()`` when done.
    """
    try:
        from heron_common.version import get_git_hash
        from heron_onboard.capture import CaptureManager, FakeCaptureBackend
        from heron_onboard.config import validate_onboard_dict
        from heron_onboard.disk_monitor import GB, DiskMonitor
        from heron_onboard.health import HealthMonitor
        from heron_onboard.supervisor import Supervisor
        from heron_onboard.testing import minimal_config_dict
    except ImportError as exc:
        raise SystemExit(f"demo needs the heron-onboard package in the workspace: {exc}") from exc

    tmp = Path(tempfile.mkdtemp(prefix="heron_demo_"))
    onboard_cfg = validate_onboard_dict(
        minimal_config_dict(tmp, control={"link_grace_s": 30, "link_lost_s": 5})
    )
    onboard_t, ground_t = LoopbackTransport.pair()
    backend = FakeCaptureBackend(onboard_cfg.sdr)
    capture = CaptureManager(onboard_cfg, backend, get_git_hash())
    disk = DiskMonitor(
        tmp,
        20,
        5,
        usage_fn=lambda p: (1000 * GB, int((1000 - disk_free_gb) * GB), int(disk_free_gb * GB)),
    )
    sup = Supervisor(onboard_cfg, onboard_t, capture, disk, HealthMonitor([]), "demo")
    thread = threading.Thread(target=sup.run, name="fake-payload", daemon=True)
    thread.start()
    return sup, thread, ground_t


def run_demo(
    config: BaseConfig,
    disk_free_gb: float = 800.0,
    web: bool = False,
    open_browser: bool = True,
) -> None:
    """Run a display against an in-process fake payload."""
    sup, thread, ground_t = start_fake_payload(disk_free_gb)
    client = LinkClient(ground_t, config.link_health, None)
    client.event("DEMO: fake payload on a loopback link; no hardware")
    try:
        if web:
            from heron_base.web import WebDashboard

            WebDashboard(config, client, gnss=None).run(open_browser=open_browser)
        else:
            from heron_base.tui.app import HeronBaseApp

            HeronBaseApp(config, client, gnss=None).run()
    finally:
        sup.request_stop()
        thread.join(timeout=5)
