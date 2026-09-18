"""Local web page display: a thin HTTP layer over ``LinkClient`` (D-023).

Why a second display: REQUIREMENTS B6 says the display is a thin layer
over the telemetry interface and can change independently. The
terminal UI (``tui/app.py``) suits an SSH window; a web page suits a
laptop screen, a second monitor, or a tablet on the same network.
Both read the same ``LinkClient`` and send the same commands.

Design:

- Standard library ``http.server`` only. No new dependency, and the
  page (``index.html``) loads no CDN or web font, so it works on a
  field laptop with no internet.
- One loop thread owns the ``LinkClient``: it polls the link, sends
  the commands the browser queued, and publishes a JSON snapshot under
  a lock. HTTP handler threads only read the snapshot or put a command
  on the queue. ``LinkClient`` therefore stays single-threaded.
- The page polls ``GET /api/state`` every ``web.refresh_ms`` and posts
  commands to ``POST /api/command``. Telemetry arrives at 1 Hz, so
  polling is enough; no WebSocket.

HTTP API (all JSON, no authentication):

- ``GET /``: the page.
- ``GET /api/state``: the latest snapshot (see ``build_snapshot``).
- ``POST /api/command`` with ``{"name": "start", "flight_id": "..."}``
  (``flight_id`` optional; names: start, stop, status, ping). Returns
  ``{"ok": true, "message": "..."}`` once the command is queued; the
  ack appears in the snapshot's ``pending``/``history`` fields.

Security: the server binds ``web.host`` (default ``127.0.0.1``, this
laptop only). Anyone who can reach the port can start or stop a
recording. Bind ``0.0.0.0`` only on a trusted network.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import webbrowser
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any

from heron_common.protocol import CommandName

from heron_base.config import BaseConfig
from heron_base.gnss import GnssReceiver
from heron_base.link_client import LinkClient

log = logging.getLogger(__name__)

PAGE_FILE = "index.html"
MAX_BODY_BYTES = 4096
HISTORY_LINES = 10


def load_page() -> bytes:
    """Read the packaged page once at start."""
    return resources.files("heron_base.web").joinpath(PAGE_FILE).read_bytes()


class WebDashboard:
    """Serve the page and keep the snapshot fresh from ``LinkClient``."""

    def __init__(
        self,
        config: BaseConfig,
        client: LinkClient,
        gnss: GnssReceiver | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._cfg = config
        self._client = client
        self._gnss = gnss
        self._clock = clock
        self._lock = threading.Lock()
        self._snapshot_bytes = b"{}"
        self._commands: queue.Queue[tuple[CommandName, dict[str, str]]] = queue.Queue()
        self._stop = threading.Event()
        self._server: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._page = load_page()
        self.commands_queued = 0

    # ----- called from HTTP handler threads ---------------------------------------

    @property
    def page(self) -> bytes:
        return self._page

    def snapshot_json(self) -> bytes:
        with self._lock:
            return self._snapshot_bytes

    def queue_command(self, name: object, flight_id: object = None) -> tuple[bool, str]:
        """Validate a browser request and queue it for the loop thread."""
        try:
            command = CommandName(str(name))
        except ValueError:
            return False, f"unknown command {name!r}"
        args: dict[str, str] = {}
        if command == CommandName.START and flight_id:
            args["flight_id"] = str(flight_id)[:64]
        self._commands.put((command, args))
        self.commands_queued += 1
        return True, f"{command} queued"

    # ----- the loop thread --------------------------------------------------------

    def step(self) -> None:
        """One loop iteration: poll the link, send queued commands, refresh."""
        self._client.poll()
        while True:
            try:
                command, args = self._commands.get_nowait()
            except queue.Empty:
                break
            self._client.send_command(command, args)
        snapshot = json.dumps(self.build_snapshot(), separators=(",", ":"), default=str)
        with self._lock:
            self._snapshot_bytes = snapshot.encode("utf-8")

    def build_snapshot(self) -> dict[str, Any]:
        """Everything the page shows, as plain JSON-ready data."""
        now = self._clock()
        client = self._client
        tlm = client.latest
        pending = client.pending
        return {
            "server_time": now,
            "refresh_ms": self._cfg.web.refresh_ms,
            "link": {
                "health": str(client.health(now)),
                "age_s": client.telemetry_age(now),
                "telemetry_count": client.telemetry_count,
                "bad_frames": client.bad_frames,
                "transport": client.transport_description,
            },
            "telemetry": tlm.model_dump(mode="json") if tlm is not None else None,
            "pending": pending.summary() if pending is not None and not pending.done else None,
            "history": [p.summary() for p in list(client.history)[-HISTORY_LINES:]],
            "events": list(client.events),
            "gnss": self._gnss_snapshot(now),
        }

    def _gnss_snapshot(self, now: float) -> dict[str, Any]:
        if self._gnss is None or not self._cfg.gnss.enabled:
            return {"enabled": False}
        st = self._gnss.status()
        return {
            "enabled": True,
            "port_open": st.port_open,
            "error": st.error,
            "fix": st.fix_text(now, self._cfg.gnss.stale_fix_s),
            "bytes_logged": st.bytes_logged,
            "rtcm_frames": st.rtcm_frames,
            "rtcm_types": {str(k): v for k, v in sorted(st.rtcm_types.items())},
            "log_path": st.log_path,
        }

    # ----- server lifecycle -------------------------------------------------------

    @property
    def port(self) -> int:
        """The bound port (useful when config says 0 = any free port)."""
        if self._server is None:
            return self._cfg.web.port
        return int(self._server.server_address[1])

    @property
    def url(self) -> str:
        host = self._cfg.web.host
        if host in ("0.0.0.0", ""):
            host = "127.0.0.1"
        return f"http://{host}:{self.port}/"

    def start_server(self) -> None:
        """Bind the port and serve on a daemon thread."""
        dashboard = self
        handler = type("BoundDashboardHandler", (DashboardHandler,), {"dashboard": dashboard})
        server = ThreadingHTTPServer((self._cfg.web.host, self._cfg.web.port), handler)
        server.daemon_threads = True
        self._server = server
        self._server_thread = threading.Thread(
            target=server.serve_forever, name="heron-web", daemon=True
        )
        self._server_thread.start()
        log.info("web display at %s", self.url)

    def stop_server(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._server_thread is not None:
            self._server_thread.join(timeout=5)
            self._server_thread = None

    def request_stop(self) -> None:
        self._stop.set()

    def run(self, open_browser: bool | None = None) -> None:
        """Serve until Ctrl-C or ``request_stop``."""
        if open_browser is None:
            open_browser = self._cfg.web.open_browser
        self.start_server()
        self._client.open()
        if self._gnss is not None:
            self._gnss.start()
        print(f"HERON web display: {self.url}  (Ctrl-C to stop)", flush=True)
        if open_browser:
            threading.Timer(0.5, webbrowser.open, args=(self.url,)).start()
        try:
            while not self._stop.is_set():
                self.step()
                time.sleep(self._cfg.link_health.poll_s)
        except KeyboardInterrupt:
            log.info("interrupted")
        finally:
            self.stop_server()
            if self._gnss is not None:
                self._gnss.stop()
            self._client.close()


class DashboardHandler(BaseHTTPRequestHandler):
    """Serve the page and the two API routes. Bound to one dashboard."""

    dashboard: WebDashboard
    server_version = "HERONBase/0.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        log.debug("web %s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(HTTPStatus.OK, "text/html; charset=utf-8", self.dashboard.page)
        elif path == "/api/state":
            self._send(HTTPStatus.OK, "application/json", self.dashboard.snapshot_json())
        else:
            self._send(HTTPStatus.NOT_FOUND, "text/plain", b"not found")

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path != "/api/command":
            self._send(HTTPStatus.NOT_FOUND, "text/plain", b"not found")
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, False, "body too large")
            return
        body = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(body or b"{}")
        except json.JSONDecodeError:
            self._json(HTTPStatus.BAD_REQUEST, False, "body is not JSON")
            return
        if not isinstance(data, dict):
            self._json(HTTPStatus.BAD_REQUEST, False, "body is not an object")
            return
        ok, message = self.dashboard.queue_command(data.get("name"), data.get("flight_id"))
        self._json(HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST, ok, message)

    def _json(self, status: HTTPStatus, ok: bool, message: str) -> None:
        payload = json.dumps({"ok": ok, "message": message}).encode("utf-8")
        self._send(status, "application/json", payload)

    def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
