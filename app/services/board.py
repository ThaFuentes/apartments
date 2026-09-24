"""Units in bulk, make-ready lists, occupied units, vendors, and work orders."""
from __future__ import annotations

import re

from app.builddb.builddb import db
from app.models import Job, Property, Unit, UnitTask
from app.services.clock import utcnow
from app.services.people import person_label
from app.services.records import audit, fuzzy_properties, normalize_unit, property_place

PART_WORDS = ("fan", "fans", "blower", "blowers", "window unit", "motor")


def clean_building(value: str) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    text = re.sub(r"^building\s+", "", text, flags=re.I).strip(" .")
    return text[:40]


def building_label(value: str) -> str:
    text = clean_building(value)
    if not text:
        return ""
    return f"Building {text}"


def range_problem(text: str) -> str:
    raw = (text or "").replace("\n", ",").replace(";", ",")
    for chunk in raw.split(","):
        span = re.fullmatch(r"#?\s*(\d+)\s*(?:-|–|—|to)\s*#?\s*(\d+)\s*[a-z]?\s*", chunk.strip(), re.I)
        if not span:
            continue
        start, end = int(span.group(1)), int(span.group(2))
        count = abs(end - start) + 1
        if count > 200:
            return f"{min(start, end)}–{max(start, end)} is {count} units. Add up to 200 at a time."
    return ""


def describe_numbers(numbers: list[str], verb: str) -> str:
    if not numbers:
        return ""
    if len(numbers) >= 3 and all(number.isdigit() for number in numbers):
        ints = [int(number) for number in numbers]
        if ints == list(range(ints[0], ints[-1] + 1)):
            return f"{verb} {ints[0]}–{ints[-1]} ({len(ints)} units)"
    if len(numbers) > 12:
        return f"{verb} {len(numbers)} units, {numbers[0]} through {numbers[-1]}"
    return f"{verb} " + ", ".join(numbers)


def expand_unit_numbers(text: str) -> list[str]:
    """101, 102, 104-110 and one number per line."""
    raw = (text or "").replace("\n", ",").replace(";", ",")
    found: list[str] = []
    for chunk in raw.split(","):
        piece = chunk.strip()
        if not piece:
            continue
        span = re.fullmatch(r"#?\s*(\d+)\s*(?:-|–|—|to)\s*#?\s*(\d+)\s*[a-z]?\s*", piece, re.I)
        if span:
            start, end = int(span.group(1)), int(span.group(2))
            if start > end:
                start, end = end, start
            if end - start <= 200:
                found.extend(str(number) for number in range(start, end + 1))
            continue
        numbers = re.findall(r"\d+[a-z]?", piece, re.I)
        if len(numbers) > 1 and re.fullmatch(r"[\d\s#a-zA-Z\-]+", piece):
            found.extend(normalize_unit(number) for number in numbers if normalize_unit(number))
            continue
        one = normalize_unit(piece)
        if one:
            found.append(one)
    seen = set()
    ordered = []
    for number in found:
        if number and number not in seen:
            seen.add(number)
            ordered.append(number)
    return ordered


def _kind_for(title: str) -> str:
    low = (title or "").lower()
    if any(re.search(rf"\b{re.escape(word)}\b", low) for word in PART_WORDS):
        return "part"
    return "task"


def split_needs(text: str) -> list[str]:
    parts = re.split(r"\s*,\s*|\s+\band\b\s+", text or "", flags=re.I)
    titles = []
    for part in parts:
        title = part.strip(" .")
        changed = True
        while changed:
            nxt = re.sub(r"^(?:and|a|an|the)\s+", "", title, count=1, flags=re.I)
            changed = nxt != title
            title = nxt.strip(" .")
        if title:
            titles.append(title[:200])
    return titles


def _properties() -> list[Property]:
    return Property.query.filter(Property.deleted_at.is_(None)).order_by(Property.name.asc()).all()


def resolve_property(hint: str = "", unit_number: str = "") -> tuple[Property | None, str]:
    hint = (hint or "").strip()
    if hint:
        matches = fuzzy_properties(hint)
        if len(matches) == 1:
            return matches[0], ""
        if len(matches) > 1:
            lines = "\n".join(property_place(prop) for prop in matches[:8])
            return None, f"Which one?\n{lines}"
        return None, f"Nothing on your list matches {hint}."
    number = normalize_unit(unit_number)
    if number:
        rows = (
            Unit.query.filter(Unit.deleted_at.is_(None), db.func.lower(Unit.unit_number) == number.lower())
            .all()
        )
        props = []
        seen = set()
        for row in rows:
            if row.property_id not in seen:
                prop = db.session.get(Property, row.property_id)
                if prop and not prop.deleted_at:
                    seen.add(prop.id)
                    props.append(prop)
        if len(props) == 1:
            return props[0], ""
        if len(props) > 1:
            lines = "\n".join(property_place(prop) for prop in props[:8])
            return None, f"Unit {number} is on more than one property.\n{lines}"
    props = _properties()
    if len(props) == 1:
        return props[0], ""
    if props:
        lines = "\n".join(property_place(prop) for prop in props[:8])
        return None, f"Which property?\n{lines}"
    return None, "Add the property first, then the units."


def ensure_unit(prop: Property, number: str, user, source: str, building: str = "") -> tuple[Unit, str]:
    number = normalize_unit(number)
    building = clean_building(building)
    row = (
        Unit.query.filter_by(property_id=prop.id, unit_number=number)
        .filter(Unit.deleted_at.is_(None))
        .first()
    )
    if row:
        if building and not row.building:
            row.building = building
            return row, "assigned"
        if building and row.building != building:
            return row, "other"
        return row, "kept"
    row = Unit(
        property_id=prop.id,
        unit_number=number,
        building=building,
        created_by_id=user.id,
        created_at=utcnow(),
    )
    db.session.add(row)
    db.session.flush()
    audit(
        user.id,
        source,
        "create",
        "unit",
        row.id,
        {},
        {"unit_number": number, "building": building, "property_id": prop.id},
    )
    return row, "created"


def add_units(user, prop: Property, text: str, source: str, building: str = "") -> dict:
    problem = range_problem(text)
    if problem:
        return {"ok": False, "reply": problem}
    numbers = expand_unit_numbers(text)
    if not numbers:
        return {"ok": False, "reply": "Which unit numbers? You can say 1000-1020."}
    building = clean_building(building)
    made, assigned, already, other = [], [], [], []
    for number in numbers:
        row, status = ensure_unit(prop, number, user, source, building=building)
        if status == "created":
            made.append(number)
        elif status == "assigned":
            assigned.append(number)
        elif status == "other":
            other.append(f"{number} is in {building_label(row.building)}")
        else:
            already.append(number)
    bits = []
    if made:
        bits.append(describe_numbers(made, "Added"))
    if assigned:
        bits.append(describe_numbers(assigned, "Moved"))
    if already:
        bits.append("Already there: " + ", ".join(already[:12]))
    if other:
        bits.append("Left where they were: " + "; ".join(other[:8]))
    where = building_label(building)
    place = f"{where} at {prop.name}" if where else prop.name
    who = person_label(user.id)
    reply = f"{' '.join(bits)} in {place}." if where else f"{' '.join(bits)} at {prop.name}."
    if who:
        reply += f" Saved by {who}."
    if made or assigned:
        from app.services.access import announce

        detail = describe_numbers(made or assigned, "added")
        announce(prop.id, user.id, f"{who or 'Someone'} {detail} in {place}.", f"/properties/{prop.id}")
    return {"ok": True, "reply": reply, "property_id": prop.id, "building": building, "created": len(made)}


def set_occupancy(user, unit: Unit, occupancy: str, source: str) -> None:
    before = unit.occupancy or ""
    unit.occupancy = occupancy
    from app.services.access import announce

    words = {"occupied": "occupied", "make_ready": "a make ready", "": "cleared"}
    announce(
        unit.property_id,
        user.id,
        f"{person_label(user.id) or 'Someone'} marked unit {unit.unit_number} {words.get(occupancy, occupancy or 'updated')}.",
        f"/units/{unit.id}",
    )
    audit(
        user.id,
        source,
        "update",
        "unit",
        unit.id,
        {"occupancy": before},
        {"occupancy": occupancy, "unit_number": unit.unit_number, "property_id": unit.property_id},
    )


def add_needed(user, unit: Unit, titles: list[str], source: str, kind: str = "", vendor: str = "", notes: str = "") -> list[UnitTask]:
    rows = []
    for title in titles:
        task_kind = kind or _kind_for(title)
        row = UnitTask(
            property_id=unit.property_id,
            unit_id=unit.id,
            kind=task_kind,
            title=title[:200],
            status="vendored" if task_kind == "vendor" else "needed",
            vendor=(vendor or "")[:160],
            notes=(notes or "")[:2000],
            created_by_id=user.id,
            created_at=utcnow(),
        )
        db.session.add(row)
        db.session.flush()
        audit(
            user.id,
            source,
            "create",
            "unit_task",
            row.id,
            {},
            {"title": row.title, "kind": row.kind, "unit_id": unit.id, "property_id": unit.property_id},
        )
        rows.append(row)
    if rows:
        from app.services.access import announce

        names = ", ".join(row.title for row in rows)
        announce(
            unit.property_id,
            user.id,
            f"{person_label(user.id) or 'Someone'} updated unit {unit.unit_number}: {names}.",
            f"/units/{unit.id}",
        )
    return rows


def complete_task(user, unit: Unit, hint: str, source: str) -> tuple[UnitTask | None, list[UnitTask]]:
    open_rows = (
        UnitTask.query.filter_by(unit_id=unit.id)
        .filter(UnitTask.deleted_at.is_(None), UnitTask.status.in_(("needed", "vendored")))
        .order_by(UnitTask.id.asc())
        .all()
    )
    want = {word for word in re.findall(r"[a-z0-9]+", (hint or "").lower()) if len(word) > 2}
    best = None
    best_score = 0
    for row in open_rows:
        have = {word for word in re.findall(r"[a-z0-9]+", row.title.lower()) if len(word) > 2}
        score = len(want & have)
        if hint and hint.lower() in row.title.lower():
            score += 3
        if score > best_score:
            best = row
            best_score = score
    if best is None or best_score < 1:
        return None, open_rows
    best.status = "done"
    best.done_by_id = user.id
    best.done_at = utcnow()
    from app.services.access import announce

    announce(
        unit.property_id,
        user.id,
        f"{person_label(user.id) or 'Someone'} finished {best.title} on unit {unit.unit_number}.",
        f"/units/{unit.id}",
    )
    audit(
        user.id,
        source,
        "update",
        "unit_task",
        best.id,
        {"status": "needed"},
        {"status": "done", "title": best.title, "unit_id": unit.id, "property_id": unit.property_id},
    )
    return best, open_rows


def apply_unit_board(user, payload, source) -> dict:
    source = source if source in ("ai", "human") else "human"
    action = (payload.get("action") or "").strip()
    prop, problem = resolve_property(payload.get("property_hint") or "", payload.get("unit_number") or "")
    if problem:
        return {"ok": True, "reply": problem}
    if prop is None:
        return {"ok": False, "reply": "Which property?"}
    who = person_label(user.id)
    if action == "add_units":
        return add_units(user, prop, payload.get("units") or "", source, building=payload.get("building") or "")
    number = normalize_unit(payload.get("unit_number") or "")
    if not number:
        return {"ok": False, "reply": "Which unit number?"}
    unit, _status = ensure_unit(prop, number, user, source)
    if action == "occupancy":
        occupancy = (payload.get("occupancy") or "").strip()
        words = {"occupied": "occupied", "make_ready": "a make ready", "vacant": "vacant"}
        set_occupancy(user, unit, occupancy, source)
        label = words.get(occupancy, occupancy or "cleared")
        reply = f"Unit {unit.unit_number} at {prop.name} is {label}."
        work = (payload.get("work_title") or "").strip()
        if work:
            from app.services.appliers import apply_log_work

            logged = apply_log_work(
                user,
                {
                    "property_id": prop.id,
                    "unit_number": unit.unit_number,
                    "title": work[:300],
                    "status": "done",
                },
                source,
            )
            if logged.get("reply"):
                reply += " " + logged["reply"]
        if who:
            reply += f" Saved by {who}."
        return {"ok": True, "reply": reply, "unit_id": unit.id, "property_id": prop.id}
    if action == "needs":
        titles = payload.get("titles") or []
        if isinstance(titles, str):
            titles = split_needs(titles)
        rows = add_needed(user, unit, titles, source, kind=payload.get("kind") or "", vendor=payload.get("vendor") or "")
        if not rows:
            return {"ok": False, "reply": "What does that unit need?"}
        names = ", ".join(row.title for row in rows)
        reply = f"Unit {unit.unit_number} at {prop.name} needs {names}."
        if who:
            reply += f" Saved by {who}."
        return {"ok": True, "reply": reply, "unit_id": unit.id}
    if action == "done":
        row, open_rows = complete_task(user, unit, payload.get("title") or "", source)
        if row is None:
            if not open_rows:
                return {"ok": True, "reply": f"Nothing is still open on unit {unit.unit_number}."}
            left = ", ".join(item.title for item in open_rows[:8])
            return {"ok": True, "reply": f"Which item on unit {unit.unit_number}? Still open: {left}"}
        reply = f"Done on unit {unit.unit_number} at {prop.name}: {row.title}."
        if who:
            reply += f" Marked by {who}."
        return {"ok": True, "reply": reply, "unit_id": unit.id}
    if action in ("vendor", "work_order", "part"):
        title = (payload.get("title") or "").strip()
        if not title:
            return {"ok": False, "reply": "What is the item?"}
        kind = {"vendor": "vendor", "work_order": "work_order", "part": "part"}[action]
        row = add_needed(user, unit, [title], source, kind=kind, vendor=payload.get("vendor") or "")[0]
        if action == "vendor":
            reply = f"Vendored out {row.title} on unit {unit.unit_number} at {prop.name}"
            if row.vendor:
                reply += f" to {row.vendor}"
            reply += "."
        elif action == "work_order":
            reply = f"Work order on unit {unit.unit_number} at {prop.name}: {row.title}."
        else:
            reply = f"Unit {unit.unit_number} at {prop.name} needs {row.title}."
        if who:
            reply += f" Saved by {who}."
        return {"ok": True, "reply": reply, "unit_id": unit.id}
    return {"ok": False, "reply": "Tell me the unit and what changed."}


def task_groups(unit_id: int) -> dict:
    rows = (
        UnitTask.query.filter_by(unit_id=unit_id)
        .filter(UnitTask.deleted_at.is_(None))
        .order_by(UnitTask.id.asc())
        .all()
    )
    groups = {"task": [], "part": [], "work_order": [], "vendor": []}
    for row in rows:
        groups.setdefault(row.kind or "task", []).append(row)
    return groups


def open_task_count(unit_id: int) -> int:
    return (
        UnitTask.query.filter_by(unit_id=unit_id)
        .filter(UnitTask.deleted_at.is_(None), UnitTask.status == "needed")
        .count()
    )


def recent_changes(property_id: int, limit: int = 12) -> list[dict]:
    from app.models import Equipment

    rows = []
    for job in (
        Job.query.filter_by(property_id=property_id)
        .filter(Job.deleted_at.is_(None))
        .order_by(Job.id.desc())
        .limit(limit)
        .all()
    ):
        unit = job.unit.unit_number if job.unit else ""
        rows.append(
            {
                "when": job.created_at,
                "who": person_label(job.created_by_id),
                "what": job.title,
                "where": f"unit {unit}" if unit else "",
                "kind": "work",
            }
        )
    for task in (
        UnitTask.query.filter_by(property_id=property_id)
        .filter(UnitTask.deleted_at.is_(None))
        .order_by(UnitTask.id.desc())
        .limit(limit)
        .all()
    ):
        unit = task.unit.unit_number if task.unit else ""
        who_id = task.done_by_id if task.status == "done" and task.done_by_id else task.created_by_id
        rows.append(
            {
                "when": task.done_at or task.created_at,
                "who": person_label(who_id),
                "what": task.title,
                "where": f"unit {unit}" if unit else "",
                "kind": task.status,
            }
        )
    for item in (
        Equipment.query.filter_by(property_id=property_id)
        .filter(Equipment.deleted_at.is_(None))
        .order_by(Equipment.id.desc())
        .limit(limit)
        .all()
    ):
        unit = item.unit.unit_number if item.unit else ""
        rows.append(
            {
                "when": item.created_at,
                "who": person_label(item.created_by_id),
                "what": " ".join(bit for bit in (item.brand, item.kind) if bit) or "equipment",
                "where": f"unit {unit}" if unit else "",
                "kind": "equipment",
            }
        )
    rows.sort(key=lambda row: row["when"] or utcnow(), reverse=True)
    return rows[:limit]
