"""HTTP adapter for the local-only flight service."""
import secrets
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, request

from engine.flight import FlightError, serial_port_records


def flight_api(service, name="flight", prefix="/api/flight"):
    bp = Blueprint(name, __name__, url_prefix=prefix)
    token = secrets.token_urlsafe(32)

    @bp.before_request
    def local_only():
        if request.remote_addr not in {"127.0.0.1", "::1"} or urlsplit(request.host_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
            return jsonify(error="Aircraft control is only available on this computer."), 403
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if origin and origin != request.host_url.rstrip("/"):
                return jsonify(error="Cross-origin aircraft commands are blocked."), 403
            if not secrets.compare_digest(request.headers.get("X-Flight-Token", ""), token):
                return jsonify(error="Reload the planner before sending aircraft commands."), 403
            if not request.is_json:
                return jsonify(error="A JSON request is required."), 400

    @bp.after_request
    def no_cache(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @bp.errorhandler(FlightError)
    @bp.errorhandler(ValueError)
    @bp.errorhandler(OSError)
    def flight_error(exc):
        return jsonify(error=str(exc)), 409

    @bp.get("/session")
    def session():
        return jsonify(token=token)

    @bp.get("/ports")
    def ports():
        try:
            return jsonify(ports=serial_port_records())
        except ImportError:
            return jsonify(ports=[], error="Install requirements.txt to use USB or ELRS serial.")

    @bp.get("/status")
    def status():
        return jsonify(service.snapshot(request.args.get("plan_id")))

    @bp.post("/connect")
    def connect():
        return jsonify(service.connect(request.get_json() or {}))

    @bp.post("/disconnect")
    def disconnect():
        return jsonify(service.disconnect())

    @bp.post("/auto")
    def auto():
        return jsonify(service.set_auto((request.get_json() or {}).get("enabled", True)))

    @bp.post("/checks")
    def checks():
        data = request.get_json() or {}
        return jsonify(service.set_check(str(data.get("key", "")), bool(data.get("enabled", True))))

    @bp.post("/rtl")
    def rtl():
        return jsonify(service.rtl())

    @bp.post("/<action>")
    def action(action):
        data = request.get_json() or {}
        return jsonify(service.submit(action, data.get("plan_id"), data.get("confirmation"))), 202

    return bp
