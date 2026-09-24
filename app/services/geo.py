"""Pins and miles. Property pins only — no building layouts."""
from __future__ import annotations

import math
import os

import requests

ROAD_FACTOR = 1.3
GEOFENCE_METERS = 800
NOMINATIM = "https://nominatim.openstreetmap.org/search"


def haversine_miles(lat1, lng1, lat2, lng2) -> float | None:
    if None in (lat1, lng1, lat2, lng2):
        return None
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(r * c * ROAD_FACTOR, 1)


def haversine_meters(lat1, lng1, lat2, lng2) -> float | None:
    miles = haversine_miles(lat1, lng1, lat2, lng2)
    if miles is None:
        return None
    return miles / ROAD_FACTOR * 1609.344


def geocode(query: str) -> tuple[float, float] | None:
    q = (query or "").strip()
    if not q or (os.getenv("APT_GEOCODE") or "").strip().lower() in ("0", "off", "false", "no"):
        return None
    try:
        resp = requests.get(
            NOMINATIM,
            params={"q": q, "format": "jsonv2", "limit": 1},
            headers={"User-Agent": "apt.poweredby.top/1.0 (field record)"},
            timeout=8,
        )
        if resp.status_code >= 400:
            return None
        rows = resp.json() if resp.content else []
        if not rows:
            return None
        return float(rows[0]["lat"]), float(rows[0]["lon"])
    except Exception:
        return None


def left_geofence(lat, lng, prop_lat, prop_lng) -> bool:
    if None in (prop_lat, prop_lng, lat, lng):
        return False
    meters = haversine_meters(lat, lng, prop_lat, prop_lng)
    if meters is None:
        return False
    return meters > GEOFENCE_METERS
