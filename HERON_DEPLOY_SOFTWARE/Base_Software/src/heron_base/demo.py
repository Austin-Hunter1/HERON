"""Demo mode: the full base station against a fake payload, no hardware.

Builds a loopback transport pair, runs the onboard supervisor with the
fake capture backend on a background thread, and starts the TUI on the
other end. Use it to learn the display and to test display changes.
"""

from __future__ import annotations

import logging
import tempfile
import threading
from pathlib import Path

from heron_common.protocol import LoopbackTransport

from heron_base.config import BaseConfig
from heron_base.link_client import LinkClient
from heron_base.tui.app import HeronBaseApp

log = logging.getLogger(__name__)


def run_demo(config: BaseConfig, disk_free_gb: float = 800.0) -> None:
    """Run the TUI against an in-process fake payload."""
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

    client = LinkClient(ground_t, config.link_health, None)
    client.event("DEMO: fake payload on a loopback link; no hardware")
    app = HeronBaseApp(config, client, gnss=None)
    try:
        app.run()
    finally:
        sup.request_stop()
        thread.join(timeout=5)
