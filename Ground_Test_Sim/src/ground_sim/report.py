"""Write the CSV tables and the interactive HTML page.

Why: the page is one self-contained file (no CDN, no map tiles), so it
works offline in the field. All data goes in as one JSON block.
"""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ground_sim.config import SimConfig
from ground_sim.simulate import Track

TEMPLATE = Path(__file__).with_name("report_template.html")
DATA_MARK = "__HERON_DATA__"


def track_summary(track: Track, start_utc: datetime, tz: ZoneInfo) -> dict:
    """Return one table row (as a dict) for a track."""
    first, last = track.samples[0], track.samples[-1]
    els = [s.el_deg for s in track.samples]
    t0 = (start_utc + timedelta(seconds=first.t_s)).astimezone(tz)
    t1 = (start_utc + timedelta(seconds=last.t_s)).astimezone(tz)
    return {
        "sid": track.sid,
        "constellation": track.constellation,
        "body": first.body,
        "start_local": t0.strftime("%H:%M:%S"),
        "stop_local": t1.strftime("%H:%M:%S"),
        "start_s": first.t_s,
        "stop_s": last.t_s,
        "duration_min": round((last.t_s - first.t_s) / 60.0, 2),
        "el_start_deg": round(first.el_deg, 1),
        "el_stop_deg": round(last.el_deg, 1),
        "el_min_deg": round(min(els), 1),
        "el_max_deg": round(max(els), 1),
        "inc_start_deg": round(first.incidence_deg, 1),
        "inc_stop_deg": round(last.incidence_deg, 1),
        "inc_min_deg": round(90.0 - max(els), 1),
        "inc_max_deg": round(90.0 - min(els), 1),
        "az_start_deg": round(first.az_deg, 1),
        "az_stop_deg": round(last.az_deg, 1),
        "fresnel_across_max_m": round(max(s.across_m for s in track.samples), 2),
        "fresnel_along_max_m": round(max(s.along_m for s in track.samples), 2),
    }


def reflection_windows(track: Track) -> list[Track]:
    """Split a pass into runs where the specular point is on water.

    Why: a satellite is only "available" as a reflection while its specular
    point is on water. One pass can give several windows (a shoreline).
    """
    windows: list[Track] = []
    current: Track | None = None
    for s in track.samples:
        if not s.available:
            current = None
            continue
        if current is not None and current.samples[-1].body != s.body:
            current = None  # a new water body starts a new window
        if current is None:
            current = Track(track.sid, track.constellation)
            windows.append(current)
        current.samples.append(s)
    return windows


def build_payload(
    cfg: SimConfig,
    tracks: list[Track],
    almanac_age_days: float,
    water_features: dict,
    nearest_water_m: float | None,
    geometry: dict,
) -> dict:
    """Return the data for the page: meta, map, passes and reflection windows."""
    tz = ZoneInfo(cfg.time.timezone)
    start_local, end_local = cfg.time.window()
    start_utc = start_local.astimezone(UTC)
    windows = []
    for index, tr in enumerate(tracks):
        for win in reflection_windows(tr):
            row = track_summary(win, start_utc, tz)
            row["pass"] = index
            fracs = [s.water_frac for s in win.samples]
            row["water_frac_mean"] = round(sum(fracs) / len(fracs), 2)
            windows.append(row)
    windows.sort(key=lambda w: (w["start_s"], w["sid"]))
    return {
        "meta": {
            "lat": cfg.site.latitude_deg,
            "lon": cfg.site.longitude_deg,
            "height_m": cfg.site.receiver_height_agl_m,
            "mask_deg": cfg.signals.elevation_mask_deg,
            "carrier_hz": cfg.signals.carrier_hz,
            "start_local": start_local.isoformat(),
            "end_local": end_local.isoformat(),
            "tz": cfg.time.timezone,
            "duration_s": (end_local - start_local).total_seconds(),
            "almanac_age_days": round(almanac_age_days, 1),
            "nearest_water_m": None if nearest_water_m is None else round(nearest_water_m),
            "terrain": geometry["terrain"],
            "reflect_height_m": round(geometry["reflect_height_m"], 1),
            "ground_elev_m": geometry.get("ground_elev_m"),
            "bodies": geometry["bodies"],
        },
        "map": {
            "tile_url": cfg.map.tile_url,
            "attribution": cfg.map.attribution,
            "max_native_zoom": cfg.map.max_native_zoom,
            "radius_m": cfg.map.radius_m,
        },
        "water": water_features,
        "windows": windows,
        "passes": [
            {
                "sid": tr.sid,
                "constellation": tr.constellation,
                "start_s": tr.samples[0].t_s,
                "stop_s": tr.samples[-1].t_s,
                # [t_s, az, el, east, north, along, across, state, water_frac]
                # state: 0 not on water, 1 on water but blocked by terrain, 2 available
                "samples": [
                    [
                        s.t_s,
                        round(s.az_deg, 2),
                        round(s.el_deg, 2),
                        round(s.east_m, 3),
                        round(s.north_m, 3),
                        round(s.along_m, 3),
                        round(s.across_m, 3),
                        2 if s.available else 1 if s.on_water else 0,
                        round(s.water_frac, 3),
                    ]
                    for s in tr.samples
                ],
            }
            for tr in tracks
        ],
    }


def write_tracks_csv(payload: dict, path: Path) -> None:
    """Write the reflection window table (one row per window on water) to `path`."""
    rows = payload["windows"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["sid"])
        writer.writeheader()
        writer.writerows(rows)


def write_samples_csv(payload: dict, path: Path) -> None:
    """Write every sample (one row per satellite and time) to `path`.

    `state`: 0 not on water, 1 on water but blocked by terrain, 2 available.
    """
    header = [
        "sid", "t_s", "az_deg", "el_deg", "east_m", "north_m",
        "along_m", "across_m", "state", "water_frac",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        for tr in payload["passes"]:
            for s in tr["samples"]:
                writer.writerow([tr["sid"], *s])


def write_html(payload: dict, path: Path) -> None:
    """Write the interactive page to `path`."""
    template = TEMPLATE.read_text(encoding="utf-8")
    # "</" inside a script block could end it early, so it is escaped.
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    path.write_text(template.replace(DATA_MARK, data), encoding="utf-8")
