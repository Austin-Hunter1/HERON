"""Payload Setup: the bench web app served by the NUC (open http://<nuc>:8080 over the Ethernet cable).

Radio/channel configuration lives here, not in the ground station. It talks to UHD directly on the
NUC, so "Find radios" and the test recording are real. The planner only gets start/stop/auto/status.
"""
import copy
import mimetypes
import shutil
import socket
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from . import analysis
from .config import ProfileStore, estimate, validate

WEB = Path(__file__).resolve().parent / "web"
mimetypes.add_type("image/avif", ".avif")  # Python 3.12 (Ubuntu 24.04) does not know it
mimetypes.add_type("font/woff2", ".woff2")
BUSY_STATES = ("AUTO_ARMED", "RECORDING", "FINALIZING", "PREPARING")


def create_app(controller, state_dir, on_applied=lambda: None):
    app = Flask(__name__, static_folder=None)
    store = ProfileStore(Path(state_dir) / "setups.json")
    test = dict(running=False, until=None, result=None, error=None, session=None)
    lock = threading.Lock()

    @app.before_request
    def guard():
        if request.method == "POST":
            # Blocks cross-site form posts: browsers cannot add this header without CORS approval.
            if request.headers.get("X-Heron") != "1":
                return jsonify(error="Missing X-Heron header."), 403
            if not request.is_json or (request.content_length or 0) > 64000:
                return jsonify(error="A small JSON body is required."), 400

    @app.after_request
    def no_cache(resp):
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(ValueError)
    @app.errorhandler(RuntimeError)
    @app.errorhandler(OSError)
    def err(exc):
        return jsonify(error=str(exc)), 409

    def body():
        return request.get_json(silent=True) or {}

    def locked_check():
        if controller.state in BUSY_STATES:
            raise ValueError("The payload is " + {"AUTO_ARMED": "armed for auto-record",
                                                   "RECORDING": "recording"}.get(controller.state, "busy")
                             + ". Stop it from the ground station first.")

    @app.get("/")
    def index():
        return send_from_directory(WEB, "index.html")

    @app.get("/static/<path:name>")
    def static_file(name):
        return send_from_directory(WEB, name)

    @app.get("/api/state")
    def state():
        snap = controller.snapshot()
        storage = controller.backend.storage_root(controller.config or store.list()[0])
        disk = None
        probe = Path(storage)
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            u = shutil.disk_usage(probe)
            disk = dict(path=str(probe), total_gb=round(u.total / 1e9, 1), free_gb=round(u.free / 1e9, 1))
        except OSError:
            pass
        with lock:
            t = dict(test, remaining_s=max(0, round(test["until"] - time.monotonic(), 1)) if test["running"] else 0)
            t.pop("until")
        return jsonify(status=snap, active=copy.deepcopy(controller.config), host=socket.gethostname(),
                       disk=disk, test=t, sessions=analysis.list_sessions(storage, 8),
                       locked=controller.state in BUSY_STATES)

    @app.get("/api/setups")
    def setups():
        return jsonify(setups=store.list())

    @app.post("/api/setups/save")
    def save():
        c = store.save(body().get("config"))
        return jsonify(config=c, setups=store.list())

    @app.post("/api/setups/delete")
    def delete():
        store.delete(body().get("name"))
        return jsonify(setups=store.list())

    @app.post("/api/estimate")
    def check():
        c = validate(body().get("config"))
        return jsonify(estimate=estimate(c))

    @app.post("/api/apply")
    def apply():
        locked_check()
        config = body().get("config")
        controller.execute("prepare", dict(config=config))
        on_applied()
        return jsonify(status=controller.snapshot())

    @app.post("/api/discover")
    def discover():
        locked_check()
        return jsonify(devices=controller.backend.discover())

    @app.post("/api/test")
    def start_test():
        seconds = body().get("seconds", 5)
        if type(seconds) not in (int, float) or not 1 <= seconds <= 60:
            raise ValueError("Test length must be 1-60 seconds.")
        with lock:
            if test["running"]:
                raise ValueError("A test recording is already running.")
            if controller.state != "READY":
                raise ValueError("Apply a setup first." if controller.state == "IDLE" else
                                 "Test recordings need the payload READY (now " + controller.state + ").")
            if controller.cube.get("armed"):
                raise ValueError("The aircraft is armed. Test recordings are for the bench.")
            controller.execute("start", dict(origin="test"))
            session_dir = controller.backend.session_path()
            test.update(running=True, until=time.monotonic() + seconds, result=None, error=None,
                        session=controller.session)

        def finish():
            time.sleep(seconds)
            result = error = None
            try:
                if controller.state == "RECORDING" and controller.origin == "test":
                    controller.execute("stop")
                result = analysis.analyse_session(session_dir) if session_dir else None
                if result is None:
                    error = "Recorder did not report a session folder."
            except Exception as exc:
                error = str(exc)
            with lock:
                test.update(running=False, result=result, error=error)

        threading.Thread(target=finish, daemon=True, name="test-recording").start()
        return jsonify(ok=True)

    @app.post("/api/stop")
    def stop():
        controller.execute("stop")
        return jsonify(status=controller.snapshot())

    return app


def serve(controller, state_dir, host, port, on_applied=lambda: None):
    from werkzeug.serving import make_server
    server = make_server(host, port, create_app(controller, state_dir, on_applied), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True, name="setup-http").start()
    controller.event(f"Payload Setup on http://{socket.gethostname()}:{port}")
    return server
