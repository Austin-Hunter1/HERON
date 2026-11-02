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

from flask import Flask, jsonify, request, send_file, send_from_directory

from . import __version__, analysis, recordings, storage
from .config import ProfileStore, estimate, validate

WEB = Path(__file__).resolve().parent / "web"
mimetypes.add_type("image/avif", ".avif")  # Python 3.12 (Ubuntu 24.04) does not know it
mimetypes.add_type("font/woff2", ".woff2")
BUSY_STATES = ("AUTO_ARMED", "RECORDING", "FINALIZING", "PREPARING")
MAX_TEST_SECONDS = 3600         # timed bench recordings up to 1 hour; 0 = until Stop


def create_app(controller, state_dir, on_applied=lambda: None, ssd_dir=None):
    app = Flask(__name__, static_folder=None)
    ssd_dir = str(ssd_dir or storage.default_ssd_dir())
    store = ProfileStore(Path(state_dir) / "setups.json")
    # seconds == 0 means "record until Stop is pressed".
    test = dict(running=False, until=None, started=None, seconds=None, result=None, error=None, session=None)
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

    def where():
        """Where recordings go right now (data drive, or the NUC SSD when no data drive is mounted)."""
        snap_where = (controller.backend.status() or {}).get("storage") if controller.config else None
        return snap_where or storage.resolve(controller.config or store.list()[0], ssd_dir)

    def busy_check(what):
        with lock:
            testing = test["running"]
        if controller.state in BUSY_STATES or testing:
            raise ValueError(what + " is paused while the payload is recording or armed, so the disk keeps up.")

    @app.get("/api/state")
    def state():
        snap = controller.snapshot()
        w = snap.get("storage") or where()
        disk = None
        probe = Path(w["path"])
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            u = shutil.disk_usage(probe)
            disk = dict(path=w["path"], drive=w["drive"], fallback=w["fallback"], configured=w["configured"],
                        total_gb=round(u.total / 1e9, 1), free_gb=round(u.free / 1e9, 1))
        except OSError:
            pass
        with lock:
            t = dict(test)
            now = time.monotonic()
            t["elapsed_s"] = round(now - test["started"], 1) if test["running"] else 0
            t["remaining_s"] = (max(0, round(test["until"] - now, 1)) if test["until"] else None) if test["running"] else 0
            t.pop("until"); t.pop("started")
        return jsonify(status=snap, active=copy.deepcopy(controller.config), host=socket.gethostname(),
                       version=__version__, disk=disk, ssd_dir=ssd_dir, test=t,
                       locked=controller.state in BUSY_STATES)

    def roots():
        cfg = controller.config or store.list()[0]
        found = [Path(controller.backend.storage_root(cfg))]
        for r in storage.candidate_roots(cfg, ssd_dir):
            if r not in found:
                found.append(r)
        return found

    @app.get("/api/recordings")
    def recording_list():
        return jsonify(sessions=recordings.list_sessions(roots()),
                       folders=[dict(path=str(r), drive=storage.drive_label(r), exists=r.is_dir()) for r in roots()])

    @app.get("/api/recordings/view")
    def recording_view():
        busy_check("Viewing recordings")
        a = request.args
        session_dir = recordings.find_session(roots(), a.get("session"))
        span = a.get("span", type=float)
        return jsonify(recordings.sample_channel(session_dir, a.get("radio"), a.get("channel", 0, type=int),
                                                 a.get("start", 0.0, type=float), span or None,
                                                 a.get("rows", 240, type=int), a.get("nfft", 512, type=int),
                                                 a.get("avg", 4, type=int)))

    @app.get("/api/recordings/file")
    def recording_file():
        busy_check("Downloading")
        a = request.args
        session_dir = recordings.find_session(roots(), a.get("session"))
        path = recordings.file_path(session_dir, a.get("radio") or None, a.get("name"))
        prefix = "_".join(x for x in (session_dir.name, a.get("radio")) if x)
        return send_file(path, as_attachment=True, download_name=f"{prefix}_{path.name}",
                         mimetype="application/octet-stream", conditional=True)

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
        if type(seconds) not in (int, float) or not (seconds == 0 or 1 <= seconds <= MAX_TEST_SECONDS):
            raise ValueError(f"Test length must be 1-{MAX_TEST_SECONDS // 60} minutes, or 0 to record until Stop.")
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
            session = controller.session
            started = time.monotonic()
            test.update(running=True, started=started, until=started + seconds if seconds else None, seconds=seconds,
                        result=None, error=None, session=session)

        def still_ours():
            return controller.state == "RECORDING" and controller.origin == "test" and controller.session == session

        def finish():
            # Wait for the timer, for Stop on the page, or for the recording to end some other way
            # (disk reserve reached, recorder fault, ground station Stop, disarm).
            while still_ours() and (not seconds or time.monotonic() < started + seconds):
                time.sleep(0.2)
            result = error = None
            try:
                if still_ours():
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

    @app.post("/api/test/stop")
    def stop_test():
        """Stops a test recording made from this page; never a flight recording."""
        if controller.state == "RECORDING" and controller.origin == "test":
            controller.execute("stop")
        return jsonify(status=controller.snapshot())

    @app.post("/api/stop")
    def stop():
        controller.execute("stop")
        return jsonify(status=controller.snapshot())

    return app


def serve(controller, state_dir, host, port, on_applied=lambda: None, ssd_dir=None):
    from werkzeug.serving import make_server
    server = make_server(host, port, create_app(controller, state_dir, on_applied, ssd_dir), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True, name="setup-http").start()
    controller.event(f"Payload Setup on http://{socket.gethostname()}:{port}")
    return server
