"""Places, unit lists, and the card she opens to see what was done."""
from __future__ import annotations

import re
from datetime import datetime

from app.models import Equipment, Job, Property, Unit, UnitVisit

_EMPTY = datetime.min


def unit_sort_key(number: str) -> tuple:
    match = re.match(r"(\d+)", number or "")
    return (int(match.group(1)) if match else 10**9, (number or "").lower())


def gear_blurb(item: Equipment) -> str:
    bits = [item.brand, item.size_label, item.kind]
    text = " ".join(bit for bit in bits if bit).strip()
    if item.model_number:
        text = f"{text} {item.model_number}".strip()
    return text or "Equipment"


def place_groups(city_id: int | None = None) -> list[dict]:
    query = Property.query.filter(Property.deleted_at.is_(None))
    if city_id:
        query = query.filter(Property.city_id == city_id)
    props = query.all()
    if not props:
        return []
    ids = [prop.id for prop in props]
    jobs = Job.query.filter(Job.property_id.in_(ids), Job.deleted_at.is_(None)).all()
    visits = UnitVisit.query.filter(UnitVisit.property_id.in_(ids)).all()
    units = Unit.query.filter(Unit.property_id.in_(ids), Unit.deleted_at.is_(None)).all()
    jobs_by = {}
    for job in jobs:
        jobs_by.setdefault(job.property_id, []).append(job)
    visits_by = {}
    for visit in visits:
        visits_by.setdefault(visit.property_id, []).append(visit)
    unit_counts = {}
    for unit in units:
        unit_counts[unit.property_id] = unit_counts.get(unit.property_id, 0) + 1
    grouped: dict[int, dict] = {}
    for prop in props:
        city = prop.city
        label = city.name if city else "No city"
        if city and city.region:
            label = f"{label}, {city.region}"
        prop_jobs = jobs_by.get(prop.id) or []
        prop_visits = visits_by.get(prop.id) or []
        latest_job = max(prop_jobs, key=lambda row: row.created_at or _EMPTY) if prop_jobs else None
        latest_visit = max(prop_visits, key=lambda row: row.started_at or _EMPTY) if prop_visits else None
        last = None
        title = ""
        if latest_job and (not latest_visit or (latest_job.created_at or _EMPTY) >= (latest_visit.started_at or _EMPTY)):
            last = latest_job.created_at
            title = latest_job.title
        elif latest_visit:
            last = latest_visit.started_at
            title = latest_visit.note or ""
        bucket = grouped.setdefault(
            prop.city_id or 0,
            {"city": label, "city_id": prop.city_id, "places": []},
        )
        bucket["places"].append(
            {
                "id": prop.id,
                "name": prop.name,
                "unit_count": unit_counts.get(prop.id, 0),
                "last": last,
                "last_title": title,
            }
        )
    groups = sorted(grouped.values(), key=lambda row: row["city"].lower())
    for group in groups:
        group["places"].sort(
            key=lambda row: (row["last"] is None, -(row["last"].timestamp() if row["last"] else 0), row["name"].lower())
        )
    return groups


def unit_cards(property_id: int, sort: str = "recent", query: str = "") -> dict:
    units = Unit.query.filter_by(property_id=property_id).filter(Unit.deleted_at.is_(None)).all()
    jobs = Job.query.filter_by(property_id=property_id).filter(Job.deleted_at.is_(None)).all()
    gear = (
        Equipment.query.filter(Equipment.property_id == property_id, Equipment.deleted_at.is_(None))
        .order_by(Equipment.id.desc())
        .all()
    )
    jobs_by: dict[int | None, list] = {}
    for job in jobs:
        jobs_by.setdefault(job.unit_id, []).append(job)
    gear_by: dict[int, list] = {}
    for item in gear:
        if item.unit_id:
            gear_by.setdefault(item.unit_id, []).append(item)
    needle = (query or "").strip().lower()
    cards = []
    for unit in units:
        if needle and needle not in (unit.unit_number or "").lower():
            continue
        unit_jobs = sorted(jobs_by.get(unit.id) or [], key=lambda row: row.created_at or _EMPTY, reverse=True)
        lines = [gear_blurb(item) for item in (gear_by.get(unit.id) or [])]
        cards.append(
            {
                "unit": unit,
                "last": unit_jobs[0].created_at if unit_jobs else None,
                "last_title": unit_jobs[0].title if unit_jobs else "",
                "job_count": len(unit_jobs),
                "gear_lines": lines[:2],
                "gear_more": max(len(lines) - 2, 0),
            }
        )
    if sort == "number":
        cards.sort(key=lambda row: unit_sort_key(row["unit"].unit_number))
    else:
        cards.sort(key=lambda row: (row["last"] is None, -(row["last"].timestamp() if row["last"] else 0), unit_sort_key(row["unit"].unit_number)))
    loose = sorted(jobs_by.get(None) or [], key=lambda row: row.created_at or _EMPTY, reverse=True)
    return {"cards": cards, "loose_jobs": loose, "total": len(units)}
