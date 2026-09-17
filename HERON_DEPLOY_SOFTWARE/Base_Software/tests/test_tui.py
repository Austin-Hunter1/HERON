"""Headless test of the terminal UI against a fake payload.

Textual's ``run_test`` drives the app without a terminal. The fake
payload is the real onboard supervisor with the fake capture backend
on a loopback link, running on a thread with the real clock. This
test protects: the app composes, telemetry reaches the display, and
the START dialog sends a START that the payload accepts.
"""

import asyncio
import threading
from pathlib import Path

import pytest
from heron_base.config import BaseConfig
from heron_base.link_client import LinkClient
from heron_base.tui.app import ConfirmScreen, HeronBaseApp
from heron_common.protocol import LoopbackTransport
from textual.widgets import DataTable

heron_onboard = pytest.importorskip("heron_onboard")


def _fake_payload(tmp_path: Path):
    from heron_onboard.capture import CaptureManager, FakeCaptureBackend
    from heron_onboard.config import validate_onboard_dict
    from heron_onboard.disk_monitor import GB, DiskMonitor
    from heron_onboard.health import HealthMonitor
    from heron_onboard.supervisor import Supervisor
    from heron_onboard.testing import minimal_config_dict

    cfg = validate_onboard_dict(
        minimal_config_dict(tmp_path, general={"tick_s": 0.02}, telemetry={"rate_hz": 5})
    )
    onboard_t, ground_t = LoopbackTransport.pair()
    capture = CaptureManager(cfg, FakeCaptureBackend(cfg.sdr), "test")
    disk = DiskMonitor(tmp_path, 1, 1, usage_fn=lambda p: (100 * GB, 10 * GB, 90 * GB))
    sup = Supervisor(cfg, onboard_t, capture, disk, HealthMonitor([]), "test")
    return sup, ground_t


def test_tui_shows_telemetry_and_starts_recording(tmp_path: Path):
    sup, ground_t = _fake_payload(tmp_path)
    thread = threading.Thread(target=sup.run, kwargs={"max_seconds": 20}, daemon=True)
    thread.start()
    config = BaseConfig()
    config.link_health.poll_s = 0.02
    config.display.refresh_hz = 10
    client = LinkClient(ground_t, config.link_health, None)
    app = HeronBaseApp(config, client, gnss=None)

    async def drive() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(1.0)
            assert client.latest is not None and client.latest.state == "IDLE"
            table = app.query_one("#sdrs", DataTable)
            assert table.row_count == 2
            await pilot.press("s")
            await pilot.pause(0.2)
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.click("#yes")
            await pilot.pause(1.0)
            assert client.history and client.history[-1].ack is not None
            assert client.history[-1].ack.ok
            assert client.latest.state == "RECORDING"
            await pilot.press("x")
            await pilot.pause(0.2)
            await pilot.click("#no")
            await pilot.pause(0.3)
            assert client.latest.state == "RECORDING"  # "No" sends nothing.

    try:
        asyncio.run(drive())
    finally:
        sup.request_stop()
        thread.join(timeout=5)
