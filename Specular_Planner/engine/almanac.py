"""Parse a GPS YUMA almanac and propagate sat ECEF (ICD-GPS-200)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

MU = 3.986005e14
OMEGA_E_DOT = 7.2921151467e-5
GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)
LEAP_SECONDS = 18

# GPS Block IIF + III that broadcast L5 - verified w Austin. Still should look at doing this dynamically.
GPS_L5_PRNS = {1, 3, 4, 6, 8, 9, 10, 11, 13, 14, 18, 20, 21, 23, 24, 25, 26, 27, 28, 30, 32}
SURGE_L5_PRNS = GPS_L5_PRNS  # alias; planner uses GPS_L5_PRNS - we dont use this tho


@dataclass
class AlmanacSat:
    prn: int
    health: int
    e: float
    toa: float
    i0: float
    omega_dot: float
    sqrt_a: float
    omega0: float
    omega: float
    m0: float
    af0: float
    af1: float
    week_10bit: int


def gps_time(dt: datetime) -> tuple[int, float]:
    """Return (full GPS week, seconds of week) for a UTC datetime."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    total = (dt - GPS_EPOCH).total_seconds() + LEAP_SECONDS
    week = int(total // 604800)
    tow = total - week * 604800
    return week, tow


def parse_yuma(path: Path) -> list[AlmanacSat]:
    text = Path(path).read_text(errors="replace")
    blocks = re.split(r"\*{8,}", text)
    sats: list[AlmanacSat] = []
    field_map = {
        "id": "prn",
        "health": "health",
        "eccentricity": "e",
        "time of applicability(s)": "toa",
        "orbital inclination(rad)": "i0",
        "rate of right ascen(r/s)": "omega_dot",
        "sqrt(a)  (m 1/2)": "sqrt_a",
        "sqrt(a) (m 1/2)": "sqrt_a",
        "right ascen at week(rad)": "omega0",
        "argument of perigee(rad)": "omega",
        "mean anom(rad)": "m0",
        "af0(s)": "af0",
        "af1(s/s)": "af1",
        "week": "week_10bit",
    }
    for block in blocks:
        vals: dict[str, float] = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, raw = line.split(":", 1)
            name = re.sub(r"\s+", " ", key.strip().lower())
            dest = field_map.get(name)
            if dest is None:
                continue
            token = raw.strip().split()[0]
            vals[dest] = float(token)
        if "prn" not in vals or "sqrt_a" not in vals:
            continue
        sats.append(
            AlmanacSat(
                prn=int(vals["prn"]),
                health=int(vals.get("health", 0)),
                e=vals["e"],
                toa=vals["toa"],
                i0=vals["i0"],
                omega_dot=vals["omega_dot"],
                sqrt_a=vals["sqrt_a"],
                omega0=vals["omega0"],
                omega=vals["omega"],
                m0=vals["m0"],
                af0=vals.get("af0", 0.0),
                af1=vals.get("af1", 0.0),
                week_10bit=int(vals["week_10bit"]),
            )
        )
    return sats

# verified w hand calcs
def _kepler(m: float, e: float) -> float:
    e_anom = m
    for _ in range(12):
        e_anom = e_anom - (e_anom - e * math.sin(e_anom) - m) / (1.0 - e * math.cos(e_anom))
    return e_anom


def almanac_week(week: int, week_10bit: int) -> int:
    """Unfold a 10-bit YUMA week onto the nearest full GPS week."""
    alm = week - (week % 1024) + int(week_10bit)
    if alm - week > 512:
        alm -= 1024
    if week - alm > 512:
        alm += 1024
    return alm


def sat_ecef(sat: AlmanacSat, week: int, tow: float) -> tuple[float, float, float]:
    """Almanac → ECEF meters at GPS week/TOW."""
    a = sat.sqrt_a * sat.sqrt_a
    n0 = math.sqrt(MU / (a * a * a))
    tk = (week - almanac_week(week, sat.week_10bit)) * 604800.0 + (tow - sat.toa)
    mk = sat.m0 + n0 * tk
    ek = _kepler(mk, sat.e)
    sin_e, cos_e = math.sin(ek), math.cos(ek)
    vk = math.atan2(math.sqrt(1.0 - sat.e * sat.e) * sin_e, cos_e - sat.e)
    uk = vk + sat.omega
    rk = a * (1.0 - sat.e * cos_e)
    ik = sat.i0
    omega_k = sat.omega0 + (sat.omega_dot - OMEGA_E_DOT) * tk - OMEGA_E_DOT * sat.toa
    xk = rk * math.cos(uk)
    yk = rk * math.sin(uk)
    cos_om, sin_om = math.cos(omega_k), math.sin(omega_k)
    cos_i, sin_i = math.cos(ik), math.sin(ik)
    x = xk * cos_om - yk * cos_i * sin_om
    y = xk * sin_om + yk * cos_i * cos_om
    z = yk * sin_i
    return x, y, z


def age_days(sats: list[AlmanacSat], dt: datetime) -> float:
    if not sats:
        return 999.0
    week, tow = gps_time(dt)
    sat = sats[0]
    age_s = (week - almanac_week(week, sat.week_10bit)) * 604800 + (tow - sat.toa)
    return abs(age_s) / 86400.0
