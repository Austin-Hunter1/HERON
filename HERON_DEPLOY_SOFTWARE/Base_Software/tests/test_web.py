"""Tests of the web display against a fake payload (D-023).

The dashboard loop is driven by hand (``step()``), the HTTP server
runs on its own thread on a free port, and a real HTTP client talks
to it. The fake payload is the onboard supervisor with fake capture on
a loopback link, as in the TUI test.
"""

import http.client
import json
import threading
import time
from pathlib import Path

import pytest
from heron_base.config import BaseConfig
from heron_base.link_client import LinkClient
from heron_base.web import WebDashboard
from heron_base.web.server import load_page
from heron_common.protocol import LoopbackTransport

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


def _wait(dashboard: WebDashboard, predicate, timeout: float = 5.0) -> None:
    """Step the dashboard loop until ``predicate()`` holds or time runs out."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        dashboard.step()
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def _request(port: int, method: str, path: str, body: dict | None = None, raw: bytes | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    headers = {"Content-Type": "application/json"} if data is not None else {}
    conn.request(method, path, body=data, headers=headers)
    response = conn.getresponse()
    payload = response.read()
    conn.close()
    return response.status, response.getheader("Content-Type", ""), payload


def test_page_is_self_contained():
    """The page must work offline: no external scripts, styles, or fonts."""
    page = load_page().decode("utf-8")
    assert "HERON ground station" in page
    for forbidden in ("http://", "https://", "<link ", "@import", 'src="//'):
        assert forbidden not in page, forbidden


def test_snapshot_without_telemetry(tmp_path: Path):
    """Before any telemetry the snapshot is valid and says so."""
    _ground, ground_t = LoopbackTransport.pair()
    client = LinkClient(ground_t, BaseConfig().link_health, None)
    dashboard = WebDashboard(BaseConfig(), client, None)
    dashboard.step()
    snapshot = json.loads(dashboard.snapshot_json())
    assert snapshot["telemetry"] is None
    assert snapshot["link"]["health"] == "NO DATA"
    assert snapshot["gnss"] == {"enabled": False}
    assert snapshot["pending"] is None


def test_web_dashboard_serves_state_and_commands(tmp_path: Path):
    sup, ground_t = _fake_payload(tmp_path)
    thread = threading.Thread(target=sup.run, kwargs={"max_seconds": 20}, daemon=True)
    thread.start()
    config = BaseConfig()
    config.web.port = 0  # Any free port.
    config.link_health.poll_s = 0.02
    client = LinkClient(ground_t, config.link_health, None)
    dashboard = WebDashboard(config, client, gnss=None)
    dashboard.start_server()
    client.open()
    try:
        port = dashboard.port
        assert port > 0 and dashboard.url == f"http://127.0.0.1:{port}/"

        status, ctype, body = _request(port, "GET", "/")
        assert status == 200 and ctype.startswith("text/html") and b"HERON" in body

        _wait(dashboard, lambda: client.latest is not None)
        status, ctype, body = _request(port, "GET", "/api/state")
        assert status == 200 and ctype.startswith("application/json")
        snapshot = json.loads(body)
        assert snapshot["link"]["health"] == "CONNECTED"
        assert snapshot["telemetry"]["state"] == "IDLE"
        assert len(snapshot["telemetry"]["sdrs"]) == 2
        assert snapshot["link"]["transport"].startswith("loopback")

        status, _, body = _request(
            port, "POST", "/api/command", {"name": "start", "flight_id": "web_test"}
        )
        assert status == 200 and json.loads(body)["ok"] is True
        _wait(dashboard, lambda: client.latest is not None and client.latest.state == "RECORDING")
        assert client.latest.flight_id == "web_test"
        assert client.history and client.history[-1].ack is not None and client.history[-1].ack.ok
        snapshot = json.loads(dashboard.snapshot_json())
        assert any("start" in line and "ok" in line for line in snapshot["history"])

        status, _, body = _request(port, "POST", "/api/command", {"name": "stop"})
        assert status == 200
        _wait(dashboard, lambda: client.latest is not None and client.latest.state == "IDLE")

        # Bad requests are refused, never crash the server.
        status, _, body = _request(port, "POST", "/api/command", {"name": "reboot"})
        assert status == 400 and "unknown command" in json.loads(body)["message"]
        status, _, _ = _request(port, "POST", "/api/command", raw=b"{not json")
        assert status == 400
        status, _, _ = _request(port, "POST", "/api/command", raw=b"[1,2]")
        assert status == 400
        status, _, _ = _request(port, "GET", "/nope")
        assert status == 404
        status, _, _ = _request(port, "POST", "/nope", {"name": "ping"})
        assert status == 404
        assert dashboard.commands_queued == 2
    finally:
        dashboard.stop_server()
        client.close()
        sup.request_stop()
        thread.join(timeout=5)
