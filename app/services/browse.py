"""Properties, unit lists, and the card she opens to see what was done."""
from __future__ import annotations

import re
from datetime import datetime

from app.builddb.builddb import db
from app.models import Equipment, Job, Property, Unit, UnitVisit

_EMPTY = datetime.min


def visible_jobs():
    """Work that is still on a property and, when it names a unit, that unit is still there."""
    return (
        Job.query.filter(Job.deleted_at.is_(None))
        .join(Property, Property.id == Job.property_id)
        .filter(Property.deleted_at.is_(None))
        .outerjoin(Unit, Unit.id == Job.unit_id)
        .filter(db.or_(Job.unit_id.is_(None), Unit.deleted_at.is_(None)))
    )


def unit_sort_key(number: str) -> tuple:
    match = re.match(r"(\d+)", number or "")
    return (int(match.group(1)) if match else 10**9, (number or "").lower())


def gear_blurb(item: Equipment) -> str:
    from app.services.equipment import kind_label

    bits = [item.brand, item.style, item.color, item.size_label, kind_label(item.kind)]
    text = " ".join(bit for bit in bits if bit).strip()
    if item.model_number:
        text = f"{text} {item.model_number}".strip()
    if item.serial_number:
        text = f"{text} SN {item.serial_number}".strip()
    if item.notes:
        text = f"{text} — {item.notes[:60]}".strip()
    return text or "Equipment"


def home_board(user_id: int, user=None) -> dict:
    """The numbers and lists on the home dashboard."""
    from app.models import PlanItem
    from app.services.access import access_map, sees_all
    from app.services.clock import local_today
    from app.services.miles import traveled_total

    today = local_today()
    groups = place_groups(user=user)
    places = []
    for group in groups:
        if group.get("pinned"):
            continue
        for place in group["places"]:
            places.append({**place, "city_label": group["city"]})
    places.sort(key=lambda row: (row.get("pinned") is not True, row["last"] is None, -(row["last"].timestamp() if row["last"] else 0), row["name"].lower()))
    pinned = []
    for group in groups:
        if group.get("pinned"):
            pinned.extend({**place, "city_label": "Pinned"} for place in group["places"])
    been = (pinned + [row for row in places if row["last"]])[:5]
    allowed = None if user is None or sees_all(user) else set(access_map(user))
    open_items = (
        PlanItem.query.filter(PlanItem.deleted_at.is_(None), PlanItem.status.in_(("open", "partial")))
        .order_by(PlanItem.id.desc())
        .limit(6)
        .all()
    )
    if allowed is not None:
        open_items = [item for item in open_items if item.property_id in allowed]
    plan = []
    for item in open_items:
        prop = item.property
        left = ""
        if item.status == "partial":
            left = f"{item.done_qty} of {item.planned_qty}"
        plan.append(
            {
                "title": item.title,
                "unit": item.unit_number or "",
                "status": item.status,
                "left": left,
                "property": prop.name if prop else "",
                "property_id": item.property_id,
                "trip_id": item.trip_id,
            }
        )
    job_query = visible_jobs()
    if allowed is not None:
        job_query = job_query.filter(Job.property_id.in_(allowed or {0}))
    jobs = job_query.order_by(Job.created_at.desc(), Job.id.desc()).limit(6).all()
    miles = traveled_total(user_id)
    return {
        "today": today,
        "place_count": len(places) + len(pinned),
        "open_count": len(open_items) if allowed is not None else PlanItem.query.filter(PlanItem.deleted_at.is_(None), PlanItem.status.in_(("open", "partial"))).count(),
        "miles": int(miles) if float(miles).is_integer() else miles,
        "places": been or places[:5],
        "plan": plan,
        "jobs": jobs,
    }


def place_groups(city_id: int | None = None, user=None) -> list[dict]:
    from app.models import City
    from app.services.access import access_map, sees_all
    from app.services.geo import city_parts

    props = Property.query.filter(Property.deleted_at.is_(None)).all()
    mine = access_map(user) if user is not None else {}
    if user is not None and not sees_all(user):
        props = [prop for prop in props if prop.id in mine]
    if city_id:
        anchor = db.session.get(City, city_id)
        if anchor:
            want = city_parts(anchor.name, anchor.region)
            props = [
                prop
                for prop in props
                if prop.city and city_parts(prop.city.name, prop.city.region) == want
            ]
        else:
            props = []
    if not props:
        return []
    ids = [prop.id for prop in props]
    jobs = visible_jobs().filter(Job.property_id.in_(ids)).all()
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
        from app.services.geo import city_parts, place_title

        city_name, state = city_parts(city.name, city.region) if city else ("", "")
        label = ", ".join(bit for bit in (city_name, state) if bit) or "No city"
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
            (city_name.lower(), state.lower()),
            {"city": label, "city_name": city_name or label, "state": state, "city_id": prop.city_id, "places": []},
        )
        access = mine.get(prop.id)
        bucket["places"].append(
            {
                "id": prop.id,
                "name": place_title(prop.name, city_name, state),
                "unit_count": unit_counts.get(prop.id, 0),
                "last": last,
                "last_title": title,
                "pinned": bool(access and access.pinned),
                "sort_order": access.sort_order if access else 0,
            }
        )
    pinned = []
    groups = sorted(grouped.values(), key=lambda row: (row["state"].lower(), row["city_name"].lower()))
    for group in groups:
        stay = []
        for place in group["places"]:
            if place["pinned"]:
                pinned.append(place)
            else:
                stay.append(place)
        stay.sort(key=lambda row: (row["last"] is None, -(row["last"].timestamp() if row["last"] else 0), row["name"].lower()))
        group["places"] = stay
        group["pinned"] = False
    groups = [group for group in groups if group["places"]]
    if pinned:
        pinned.sort(key=lambda row: (row["sort_order"], row["name"].lower()))
        groups.insert(0, {"city": "Pinned", "city_name": "Pinned", "state": "", "city_id": None, "places": pinned, "pinned": True})
    return groups


def _building_key(value: str) -> tuple:
    text = (value or "").strip()
    if text.isdigit():
        return (0, int(text), "")
    return (1, 0, text.lower())


def unit_cards(property_id: int, sort: str = "recent", query: str = "", show: str = "", building: str = "") -> dict:
    from app.models import UnitTask
    from app.services.board import building_label, clean_building

    units = Unit.query.filter_by(property_id=property_id).filter(Unit.deleted_at.is_(None)).all()
    building_names = sorted({unit.building for unit in units if unit.building}, key=_building_key)
    wanted = clean_building(building)
    if wanted:
        units = [unit for unit in units if clean_building(unit.building) == wanted]
    jobs = Job.query.filter_by(property_id=property_id).filter(Job.deleted_at.is_(None)).all()
    tasks = (
        UnitTask.query.filter_by(property_id=property_id)
        .filter(UnitTask.deleted_at.is_(None))
        .all()
    )
    needed_by: dict[int, int] = {}
    parts_by: dict[int, int] = {}
    vendors_by: dict[int, int] = {}
    for task in tasks:
        if task.status == "needed":
            needed_by[task.unit_id] = needed_by.get(task.unit_id, 0) + 1
            if task.kind == "part":
                parts_by[task.unit_id] = parts_by.get(task.unit_id, 0) + 1
        if task.kind == "vendor" or task.status == "vendored":
            vendors_by[task.unit_id] = vendors_by.get(task.unit_id, 0) + 1
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
        unit_jobs = sorted(jobs_by.get(unit.id) or [], key=lambda row: row.created_at or _EMPTY, reverse=True)
        unit_tasks = [task for task in tasks if task.unit_id == unit.id]
        unit_gear = gear_by.get(unit.id) or []
        search_blob = " ".join(
            [
                unit.unit_number or "",
                unit.building or "",
                unit.occupancy or "",
                *(f"{job.title} {job.detail}" for job in unit_jobs),
                *(f"{task.title} {task.vendor} {task.notes}" for task in unit_tasks),
                *(f"{item.brand} {item.kind} {item.style} {item.model_number} {item.serial_number} {item.notes}" for item in unit_gear),
            ]
        ).lower()
        if needle and needle not in search_blob:
            continue
        if show == "worked" and not unit_jobs:
            continue
        if show == "make_ready" and (unit.occupancy or "") != "make_ready":
            continue
        if show == "occupied" and (unit.occupancy or "") != "occupied":
            continue
        if show == "needs" and not needed_by.get(unit.id):
            continue
        lines = [gear_blurb(item) for item in unit_gear]
        cards.append(
            {
                "unit": unit,
                "last": unit_jobs[0].created_at if unit_jobs else None,
                "last_title": unit_jobs[0].title if unit_jobs else "",
                "job_count": len(unit_jobs),
                "open_tasks": needed_by.get(unit.id, 0),
                "part_count": parts_by.get(unit.id, 0),
                "vendor_count": vendors_by.get(unit.id, 0),
                "gear_count": len(unit_gear),
                "gear_lines": lines[:2],
                "gear_more": max(len(lines) - 2, 0),
                "search_blob": search_blob,
            }
        )
    def _inside(row):
        if sort == "number":
            return unit_sort_key(row["unit"].unit_number)
        return (row["last"] is None, -(row["last"].timestamp() if row["last"] else 0), unit_sort_key(row["unit"].unit_number))

    cards.sort(key=lambda row: (_building_key(row["unit"].building or ""), _inside(row)))
    rollups: dict[str, dict] = {}
    for card in cards:
        key = card["unit"].building or ""
        bucket = rollups.setdefault(
            key,
            {"units": 0, "equipment": 0, "parts": 0, "labor": 0, "vendors": 0},
        )
        bucket["units"] += 1
        bucket["equipment"] += card["gear_count"]
        bucket["parts"] += card["part_count"]
        bucket["labor"] += card["job_count"]
        bucket["vendors"] += card["vendor_count"]
    has_buildings = any(card["unit"].building for card in cards)
    previous = None
    for card in cards:
        key = card["unit"].building or ""
        bucket = rollups[key]
        bits = [f"{bucket['units']} units"]
        if bucket["equipment"]:
            bits.append(f"{bucket['equipment']} equipment")
        if bucket["parts"]:
            bits.append(f"{bucket['parts']} parts needed")
        if bucket["vendors"]:
            bits.append(f"{bucket['vendors']} vendored")
        if bucket["labor"]:
            bits.append(f"{bucket['labor']} labor")
        card["building_label"] = building_label(key) or "No building"
        card["show_head"] = has_buildings and key != previous
        card["rollup"] = " · ".join(bits)
        previous = key
    loose = sorted(jobs_by.get(None) or [], key=lambda row: row.created_at or _EMPTY, reverse=True)
    return {
        "cards": cards,
        "loose_jobs": loose,
        "total": len(units),
        "building_names": building_names,
        "building": wanted,
    }
