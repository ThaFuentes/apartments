"""Pins and miles. Property pins only — no building layouts."""
from __future__ import annotations

import math
import os
import re

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


_STATE = {
    "tx": "Texas",
    "texas": "Texas",
    "ok": "Oklahoma",
    "oklahoma": "Oklahoma",
    "nm": "New Mexico",
    "new mexico": "New Mexico",
    "la": "Louisiana",
    "louisiana": "Louisiana",
    "ar": "Arkansas",
    "arkansas": "Arkansas",
    "co": "Colorado",
    "colorado": "Colorado",
    "ks": "Kansas",
    "kansas": "Kansas",
}


def state_name(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    return _STATE.get(text.lower(), text)


def geocode_enabled() -> bool:
    return (os.getenv("APT_GEOCODE") or "").strip().lower() not in ("0", "off", "false", "no")


def geocode(query: str) -> tuple[float, float] | None:
    q = (query or "").strip()
    if not q or not geocode_enabled():
        return None
    rows = _nominatim(q, limit=1)
    if not rows:
        return None
    try:
        return float(rows[0]["lat"]), float(rows[0]["lon"])
    except (KeyError, TypeError, ValueError):
        return None


def _nominatim(query: str, limit: int = 5) -> list:
    try:
        resp = requests.get(
            NOMINATIM,
            params={"q": query, "format": "jsonv2", "addressdetails": 1, "limit": limit},
            headers={"User-Agent": "apt.poweredby.top/1.0 (field record)"},
            timeout=8,
        )
        if resp.status_code >= 400:
            return []
        rows = resp.json() if resp.content else []
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _state_ok(region: str, found: str) -> bool:
    region = (region or "").strip().lower()
    found = (found or "").strip().lower()
    if not region or not found:
        return True
    return state_name(region).lower() == state_name(found).lower()


def place_from_hits(rows: list, name: str, city: str, region: str = "") -> dict | None:
    """Keep a hit only when the name and the city both match. A pin in the wrong town is not an address."""
    name_l = (name or "").strip().lower()
    city_l = (city or "").strip().lower()
    if not name_l or not city_l:
        return None
    best = None
    best_score = -1
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        addr = row.get("address") or {}
        place_city = (addr.get("city") or addr.get("town") or addr.get("village") or addr.get("hamlet") or "").strip()
        display = row.get("display_name") or ""
        blob = f"{row.get('name') or ''} {display}".lower()
        if city_l not in place_city.lower() and city_l not in display.lower():
            continue
        if not _state_ok(region, addr.get("state") or ""):
            continue
        if name_l not in blob:
            continue
        house = (addr.get("house_number") or "").strip()
        road = (addr.get("road") or addr.get("pedestrian") or "").strip()
        street = " ".join(bit for bit in (house, road) if bit).strip()
        score = 5
        if place_city.lower() == city_l:
            score += 3
        if street:
            score += 4
        if (row.get("name") or "").lower().startswith(name_l):
            score += 2
        if not street or score <= best_score:
            continue
        if score > best_score:
            state = state_name(addr.get("state") or region)
            tail = ", ".join(bit for bit in (place_city or city, state, (addr.get("postcode") or "").strip()) if bit)
            try:
                lat = float(row["lat"])
                lng = float(row["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            best = {"address": f"{street}, {tail}".strip(" ,"), "lat": lat, "lng": lng, "label": display}
            best_score = score
    return best


_STREET = re.compile(
    r"(\d{2,6}\s+[A-Za-z0-9.'\- ]{2,42}?(?:Ave|Avenue|St|Street|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Way|Pkwy|Parkway|Ct|Court)\.?)"
    r"\s*,\s*([A-Za-z][A-Za-z .'-]{1,40}?)\s*,\s*"
    r"(Texas|Oklahoma|New Mexico|Louisiana|Arkansas|Colorado|Kansas|TX|OK|NM|LA|AR|CO|KS)"
    r"(?:\s+(\d{5}))?",
    re.I,
)


def address_from_listings(page: str, name: str, city: str, region: str = "", minimum: int = 2, name_anywhere: bool = False) -> str:
    """The street that keeps showing up for this name in this city. A one-off hit in another town is ignored."""
    name_l = (name or "").strip().lower()
    city_l = (city or "").strip().lower()
    if not name_l or not city_l or not page:
        return ""
    counts: dict[str, tuple[int, str]] = {}
    for match in _STREET.finditer(page):
        street = re.sub(r"\s+", " ", match.group(1)).strip(" ,.")
        town = re.sub(r"\s+", " ", (match.group(2) or "")).strip(" ,.")
        found_state = (match.group(3) or "").strip()
        postal = (match.group(4) or "").strip()
        window = page[max(0, match.start() - 600) : match.end() + 200].lower()
        town_l = town.lower()
        if city_l not in town_l and city_l not in window:
            continue
        if name_l not in window and name_l.split()[0] not in window and not (name_anywhere and name_l in page.lower()):
            continue
        tail_city = city.strip()
        tail_state = state_name(found_state or region)
        line = ", ".join(bit for bit in (street, tail_city, f"{tail_state} {postal}".strip()) if bit)
        key = re.sub(r"[^a-z0-9]", "", street.lower())
        count, _old = counts.get(key, (0, line))
        counts[key] = (count + 1, line)
    if not counts:
        return ""
    count, line = max(counts.values(), key=lambda item: item[0])
    if count < minimum:
        return ""
    return line


def _community_html(name: str) -> str:
    """The apartment site often prints the office address when search snippets do not."""
    slug = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    if len(slug) < 4:
        return ""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
    pages = []
    for url in (f"https://www.{slug}apartments.com/", f"https://www.{slug}apartmenthomes.com/"):
        try:
            resp = requests.get(url, headers=headers, timeout=8)
        except Exception:
            continue
        if resp.status_code != 200 or name.lower() not in (resp.text or "").lower():
            continue
        pages.append(resp.text or "")
    return "\n".join(pages)


def _listing_page(name: str, city: str, region: str) -> str:
    query = " ".join(bit for bit in (name, "apartments", city, region, "address") if bit)
    try:
        resp = requests.get(
            "https://www.bing.com/search",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        if resp.status_code >= 400:
            return ""
        return resp.text or ""
    except Exception:
        return ""


def lookup_place(name: str, city: str, region: str = "") -> dict | None:
    """Find the street address and keep it only when it is in that city."""
    name = (name or "").strip()
    city = (city or "").strip()
    region = (region or "").strip()
    if not name or not city or not geocode_enabled():
        return None
    where = ", ".join(bit for bit in (city, region) if bit)
    rows = []
    for query in (f"{name} apartments, {where}", f"{name}, {where}"):
        rows = _nominatim(query, limit=5)
        if rows:
            break
    map_hit = place_from_hits(rows, name, city, region)
    if map_hit and map_hit.get("address"):
        return map_hit
    line = address_from_listings(_listing_page(name, city, state_name(region) or region), name, city, region)
    if not line:
        line = address_from_listings(_community_html(name), name, city, region, minimum=1, name_anywhere=True)
    if not line:
        return None
    point = geocode(line)
    found = {"address": line, "lat": None, "lng": None, "label": line}
    if point:
        found["lat"], found["lng"] = point
    elif rows and name.lower() in f"{rows[0].get('name') or ''} {rows[0].get('display_name') or ''}".lower():
        try:
            found["lat"] = float(rows[0]["lat"])
            found["lng"] = float(rows[0]["lon"])
        except (KeyError, TypeError, ValueError):
            pass
    return found


def left_geofence(lat, lng, prop_lat, prop_lng) -> bool:
    if None in (prop_lat, prop_lng, lat, lng):
        return False
    meters = haversine_meters(lat, lng, prop_lat, prop_lng)
    if meters is None:
        return False
    return meters > GEOFENCE_METERS
