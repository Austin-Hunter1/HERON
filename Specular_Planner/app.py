#!/usr/bin/env python3
"""Local web UI for the Boulder Reservoir specular planner."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

from engine.export import qgc_wpl, run_card, validate_wpl
from engine.flight import DEFAULT_SKIPPED_CHECKS, LIVE_TRANSPORTS, FlightService
from engine.flight_api import flight_api
from engine.planner import (
    DEFAULT_H_AGL,
    DEFAULT_HOVER,
    DEFAULT_LOITER,
    DEFAULT_MASK,
    DEFAULT_PAD,
    DEFAULT_SPEED,
    DEFAULT_TARGET,
    WATER_H,
    default_window,
    load_lake,
    plan,
    sky_snapshot,
)
from engine.catalog import (
    check_source,
    describe,
    download_path,
    due_daily,
    preview,
    sync_planning,
    sync_source,
)
from engine.gnss import counts_by_const, filter_sats, load_l5_sats

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
STATIC = ROOT / "static"
OUTPUT = ROOT / "output"

app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")
flight = FlightService(LIVE_TRANSPORTS, DATA / "launch_checks_fly.json", DEFAULT_SKIPPED_CHECKS)
sim = FlightService({"demo"}, DATA / "launch_checks_sim.json", DEFAULT_SKIPPED_CHECKS)
app.register_blueprint(flight_api(flight))
app.register_blueprint(flight_api(sim, name="sim", prefix="/api/sim"))

_SATS = None
_LAKE = None
_LOCK = threading.Lock()


def sats():
    global _SATS
    if _SATS is None:
        _SATS = load_l5_sats(DATA)
    return _SATS


def reload_sats():
    global _SATS
    _SATS = load_l5_sats(DATA)
    return _SATS


def lake():
    global _LAKE
    if _LAKE is None:
        water = DATA / "waterbodies.geojson"
        _LAKE = load_lake(water if water.exists() else DATA / "boulder_reservoir.geojson")
    return _LAKE


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/api/defaults")
def defaults():
    start, dur = default_window()
    return jsonify(
        {
            "pad": DEFAULT_PAD,
            "hover": DEFAULT_HOVER,
            "h_agl": DEFAULT_H_AGL,
            "speed_mps": DEFAULT_SPEED,
            "loiter_s": DEFAULT_LOITER,
            "elev_mask": DEFAULT_MASK,
            "start": start.strftime("%Y-%m-%dT%H:%M"),
            "duration_min": dur,
            "lake": lake(),
            "n_water": len(lake().get("features") or []),
            "target": DEFAULT_TARGET,
            "almanac_sats": len(sats()),
            "constellations": counts_by_const(sats()),
        }
    )


def _parse_start(raw):
    if not raw:
        start, _ = default_window()
        return start
    start = datetime.fromisoformat(raw)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return start


@app.get("/api/sky")
def api_sky():
    start = _parse_start(request.args.get("start"))
    lat = float(request.args.get("lat", DEFAULT_PAD["lat"]))
    lon = float(request.args.get("lon", DEFAULT_PAD["lon"]))
    mask = float(request.args.get("mask", DEFAULT_MASK))
    h_agl = float(request.args.get("h_agl", DEFAULT_H_AGL))
    raw_c = request.args.get("constellations") or "G,E,C"
    consts = [c.strip().upper() for c in raw_c.split(",") if c.strip()]
    rows = sky_snapshot(
        filter_sats(sats(), consts or ["G", "E", "C"]),
        start,
        lat,
        lon,
        WATER_H + h_agl,
        mask,
    )
    n_up = sum(1 for r in rows if r["above_mask"])
    return jsonify(
        {
            "start": start.isoformat(),
            "lat": lat,
            "lon": lon,
            "mask": mask,
            "n": len(rows),
            "n_above_mask": n_up,
            "sats": rows,
        }
    )


@app.post("/api/plan")
def api_plan():
    body = request.get_json(force=True) or {}
    start_raw = body.get("start")
    if not start_raw:
        start, _ = default_window()
    else:
        start = datetime.fromisoformat(start_raw)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
    pad = body.get("pad") or DEFAULT_PAD
    hover = body.get("hover") or DEFAULT_HOVER
    mode = body.get("mode") or "click"
    loiter = max(0.0, float(body.get("loiter_s", DEFAULT_LOITER)))
    tile_raw = body.get("tile_m")
    tel_raw = body.get("target_el")
    target_el = None
    if mode == "survey" and tel_raw not in (None, ""):
        target_el = float(tel_raw)
        if target_el < 5 or target_el > 90:
            return jsonify({"error": "Aim elevation must be between 5° and 90°."}), 400
    consts = body.get("constellations")
    if consts is None:
        consts = ["G", "E", "C"]
    try:
        result = plan(
            sats(),
            lake(),
            start=start,
            duration_min=int(body.get("duration_min", 40)),
            pad=pad,
            hover=hover,
            targets=body.get("targets"),
            h_agl=float(body.get("h_agl", DEFAULT_H_AGL)),
            speed_mps=float(body.get("speed_mps", DEFAULT_SPEED)),
            loiter_s=loiter,
            elev_mask=float(body.get("elev_mask", DEFAULT_MASK)),
            constellations=consts,
            step_s=int(body.get("step_s", 15)),
            mode=mode,
            click_lat=body.get("click_lat"),
            click_lon=body.get("click_lon"),
            click_lat2=body.get("click_lat2"),
            click_lon2=body.get("click_lon2"),
            tile_m=float(tile_raw) if tile_raw not in (None, "", 0) else None,
            max_wp=int(body.get("max_wp", 200)),
            waypoints=body.get("waypoints"),
            target_el=target_el,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    OUTPUT.mkdir(exist_ok=True)
    stamp = start.strftime("%Y%m%d_%H%M")
    wp_name = f"HERON_{stamp}.waypoints"
    card_name = f"HERON_{stamp}_runcard.txt"
    wpl = qgc_wpl(result)
    check = validate_wpl(wpl, result)
    result["flight_plan_id"] = flight.register_plan(wpl, result["meta"])
    sim.register_plan(wpl, result["meta"], result["flight_plan_id"])
    # Explicit UTF-8 bytes: Windows text mode is cp1252 (no arrows) and rewrites \n, breaking upload verification.
    (OUTPUT / wp_name).write_bytes(wpl.encode("utf-8"))
    (OUTPUT / card_name).write_bytes(run_card(result).encode("utf-8"))
    (OUTPUT / f"HERON_{stamp}.json").write_bytes(json.dumps(result["meta"], indent=2).encode("utf-8"))
    result["exports"] = {
        "waypoints": f"/api/export/{wp_name}",
        "runcard": f"/api/export/{card_name}",
        "filename": wp_name,
        "mission": check,
    }
    result["stamp"] = stamp
    return jsonify(result)


@app.get("/api/almanacs")
def api_almanacs():
    return jsonify({"items": describe(sats()), "constellations": counts_by_const(sats())})


@app.post("/api/almanacs/<sid>/check")
def api_almanac_check(sid: str):
    try:
        return jsonify(check_source(sid))
    except KeyError:
        return jsonify({"error": "unknown almanac"}), 404
    except Exception as e:
        return jsonify({"id": sid, "ok": False, "state": "error", "message": str(e)}), 500


def _with_sat_counts(info: dict) -> dict:
    info["constellations"] = counts_by_const(sats())
    info["almanac_sats"] = len(sats())
    return info


@app.post("/api/almanacs/sync")
def api_almanac_sync():
    """Check remotes. Write only if a file is missing or the bytes differ."""
    body = request.get_json(silent=True) or {}
    sid = body.get("id") or request.args.get("id")
    force = bool(body.get("force", True if sid else False))
    try:
        with _LOCK:
            if sid:
                info = sync_source(sid)
                if info.get("state") == "updated":
                    reload_sats()
                return jsonify(_with_sat_counts(info))
            if not force and not due_daily():
                return jsonify(
                    {
                        "ok": True,
                        "skipped": True,
                        "updated": 0,
                        "current": 0,
                        "results": [],
                        "message": "not due yet",
                    }
                )
            bundle = sync_planning()
            if bundle.get("updated"):
                reload_sats()
            return jsonify(_with_sat_counts(bundle))
    except KeyError:
        return jsonify({"error": "unknown almanac"}), 404
    except Exception as e:
        return jsonify({"ok": False, "state": "error", "message": str(e)}), 500


@app.post("/api/almanacs/<sid>/refresh")
def api_almanac_refresh(sid: str):
    try:
        with _LOCK:
            info = sync_source(sid)
            if info.get("state") == "updated":
                reload_sats()
        return jsonify(_with_sat_counts(info))
    except KeyError:
        return jsonify({"error": "unknown almanac"}), 404
    except Exception as e:
        return jsonify({"id": sid, "ok": False, "state": "error", "message": str(e)}), 500


@app.get("/api/almanacs/<sid>/preview")
def api_almanac_preview(sid: str):
    try:
        return jsonify(preview(sid))
    except KeyError:
        return jsonify({"error": "unknown almanac"}), 404
    except FileNotFoundError:
        return jsonify({"error": "file not on disk"}), 404


@app.get("/api/almanacs/<sid>/file")
def api_almanac_file(sid: str):
    try:
        path = download_path(sid)
        return send_file(path, as_attachment=True, download_name=path.name)
    except KeyError:
        return jsonify({"error": "unknown almanac"}), 404
    except FileNotFoundError:
        return jsonify({"error": "file not on disk"}), 404


@app.get("/api/export/<name>")
def export_file(name: str):
    safe = Path(name).name
    return send_from_directory(OUTPUT, safe, as_attachment=True)


def start_almanac_watch() -> None:
    def loop():
        time.sleep(3)
        while True:
            try:
                if due_daily():
                    print("almanac auto-sync: checking remotes…", flush=True)
                    with _LOCK:
                        info = sync_planning()
                        if info.get("updated"):
                            reload_sats()
                    print(
                        "almanac auto-sync:",
                        f"{info.get('updated', 0)} updated,",
                        f"{info.get('current', 0)} already current",
                        flush=True,
                    )
            except Exception as exc:
                print("almanac auto-sync failed:", exc, flush=True)
            time.sleep(3600)

    threading.Thread(target=loop, name="almanac-sync", daemon=True).start()
    print("almanac auto-sync: daily check armed", flush=True)


def main():
    start_almanac_watch()
    flight.start_auto_connect()
    print("Specular planner  http://127.0.0.1:5055")
    app.run(host="127.0.0.1", port=5055, debug=False)


if __name__ == "__main__":
    main()
