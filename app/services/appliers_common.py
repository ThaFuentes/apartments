"""Helpers shared by the applier modules.

The appliers live in appliers_trips.py, appliers_units.py, and
appliers_reports.py; appliers.py is the registry. Everything the appliers
share — source stamping, trip lookup, pinning, appliance pieces, property
matching — lives here so no module imports another applier module.
"""
from __future__ import annotations

import json
import re
from datetime import date
from urllib.parse import urlsplit

from app.builddb.builddb import db
from app.models import Equipment, EquipmentTemplate, Expense, Job, Property, Trip
from app.services.clock import utcnow
from app.services.geo import geocode, haversine_miles, lookup_place
from app.services.records import audit, open_shift


def _src(source: str) -> str:
    return source if source in ("ai", "human") else "human"


def _trip_for(payload, user) -> Trip | None:
    if payload.get("trip_id"):
        trip = db.session.get(Trip, int(payload["trip_id"]))
        if trip and trip.deleted_at is None:
            return trip
    shift = open_shift(user)
    if shift and shift.trip_id:
        trip = db.session.get(Trip, shift.trip_id)
        if trip and trip.deleted_at is None:
            return trip
    return (
        Trip.query.filter(Trip.deleted_at.is_(None), Trip.created_by_id == user.id)
        .order_by(Trip.id.desc())
        .first()
    )


def _locate_property(prop: Property, name: str, city: str, region: str, given_address: str = "") -> str:
    """Look the place up and keep the street only when it is in that city."""
    given = (given_address or "").strip()
    if given:
        if given != (prop.address or ""):
            prop.address = given[:300]
        _pin_property(prop, given)
        return prop.address or ""
    if (prop.address or "").strip() and prop.lat is not None:
        return prop.address
    from app.services.geo import lookup_place

    found = lookup_place(name, city, region)
    if not found:
        # No web hit: still give the property a city-level pin so mileage and
        # maps work with the address she can type later.
        _pin_property(prop, "")
        if prop.lat is None and prop.city is not None:
            point = geocode("\n".join(bit for bit in (prop.city.name, prop.city.region) if bit) or "")
            if point:
                prop.lat, prop.lng = point
        return prop.address or ""
    prop.address = found["address"][:300]
    if found.get("lat") is not None and found.get("lng") is not None:
        prop.lat = found["lat"]
        prop.lng = found["lng"]
    return prop.address


def _pin_property(prop: Property, address: str = "") -> None:
    if prop.lat is not None and prop.lng is not None and not address:
        return
    city = prop.city.name if prop.city else ""
    region = prop.city.region if prop.city else ""
    query = ", ".join(bit for bit in (address or prop.address, prop.name, city, region) if bit)
    point = geocode(query)
    if point:
        prop.lat, prop.lng = point


def _miles_between(origin_lat, origin_lng, prop: Property) -> float | None:
    return haversine_miles(origin_lat, origin_lng, prop.lat, prop.lng)


def _worked_stamp(value, tz_name: str | None):
    raw = (str(value or "")).strip()[:10]
    if not re.match(r"\d{4}-\d{2}-\d{2}$", raw):
        return utcnow()
    from datetime import datetime, time, timezone

    from app.services.clock import zone

    day = datetime.fromisoformat(raw).date()
    local = datetime.combine(day, time(12, 0), tzinfo=zone(tz_name))
    return local.astimezone(timezone.utc).replace(tzinfo=None)


_PIECE_KEYS = (
    "kind", "brand", "model", "serial", "size", "style", "color", "notes", "note",
    "phone", "vendor", "purchase_date", "purchase_price", "warranty_expires",
    "repair_notes", "parts_link", "template_id",
)


def _clip(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _equipment_date(value):
    raw = _clip(value, 10)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _equipment_price(value):
    if value in (None, ""):
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return amount if 0 <= amount <= 1_000_000 else None


def _equipment_template_id(value, property_id):
    try:
        row = db.session.get(EquipmentTemplate, int(value)) if value else None
    except (TypeError, ValueError):
        return None
    return row.id if row and row.property_id == property_id else None


def _piece_filled(eq: dict) -> bool:
    return any(_clip(eq.get(key), 2000) for key in _PIECE_KEYS)


def _gear_pieces(equipment: dict, extra: list) -> list[dict]:
    pieces = []
    if isinstance(equipment, dict) and _piece_filled(equipment):
        pieces.append(equipment)
    for item in extra:
        if isinstance(item, dict) and _piece_filled(item):
            pieces.append(item)
    return pieces


def _piece_view(row) -> dict:
    return {
        "kind": row.kind,
        "brand": row.brand,
        "style": row.style,
        "color": row.color,
        "size": row.size_label,
        "model": row.model_number,
        "serial": row.serial_number,
        "phone": row.phone,
        "vendor": row.vendor,
        "parts_link": row.parts_link,
        "repair_notes": row.repair_notes,
    }


def _same_kind(rows, kind: str) -> list:
    wanted = kind.lower()
    return [row for row in rows if (row.kind or "").lower() == wanted]


def file_piece(user, piece, unit, job, property_id, source, *, force_new: bool = False):
    """Save one appliance on this unit. A note never spreads to other units or other cards."""
    eq = piece or {}
    if not isinstance(eq, dict) or not _piece_filled(eq):
        return None, []
    kind = _clip(eq.get("kind"), 80)
    brand = _clip(eq.get("brand"), 80)
    model = _clip(eq.get("model") or eq.get("model_number"), 80)
    serial = _clip(eq.get("serial") or eq.get("serial_number"), 80).upper()
    size = _clip(eq.get("size") or eq.get("size_label"), 40)
    style = _clip(eq.get("style"), 80)
    color = _clip(eq.get("color"), 40)
    note = _clip(eq.get("notes") if eq.get("notes") is not None else eq.get("note"), 2000)
    phone = _clip(eq.get("phone"), 40)
    vendor = _clip(eq.get("vendor"), 120)
    repair_notes = _clip(eq.get("repair_notes"), 4000)
    parts_link = _clip(eq.get("parts_link"), 500)
    parsed_link = urlsplit(parts_link) if parts_link else None
    if parsed_link and parsed_link.scheme.lower() not in {"http", "https"}:
        parts_link = ""
    purchase_date = _equipment_date(eq.get("purchase_date"))
    warranty_expires = _equipment_date(eq.get("warranty_expires"))
    purchase_price = _equipment_price(eq.get("purchase_price"))
    template_id = _equipment_template_id(eq.get("template_id"), property_id)
    if serial:
        existing_serial = Equipment.query.filter(
            Equipment.deleted_at.is_(None), Equipment.property_id == property_id,
            db.func.upper(Equipment.serial_number) == serial,
        ).first()
        if existing_serial and (unit is None or existing_serial.unit_id != unit.id):
            return None, [existing_serial]
    if not template_id and kind and model:
        template = EquipmentTemplate.query.filter_by(
            property_id=property_id, kind=kind, brand=brand, model_number=model
        ).first()
        if template is None:
            template = EquipmentTemplate(
                property_id=property_id,
                kind=kind,
                brand=brand,
                model_number=model,
                size_label=size,
                style=style,
                color=color,
                notes=note,
            phone=phone,
            vendor=vendor,
            parts_link=parts_link,
            created_by_id=user.id,
            )
            db.session.add(template)
            db.session.flush()
        template_id = template.id
        size = size or template.size_label
        style = style or template.style
        color = color or template.color
        note = note or template.notes
        phone = phone or template.phone
        vendor = vendor or template.vendor
        parts_link = parts_link or template.parts_link
    elif template_id:
        template = db.session.get(EquipmentTemplate, template_id)
        if template:
            kind = kind or template.kind
            brand = brand or template.brand
            model = model or template.model_number
            size = size or template.size_label
            style = style or template.style
            color = color or template.color
            note = note or template.notes
            phone = phone or template.phone
            vendor = vendor or template.vendor
            parts_link = parts_link or template.parts_link
    query = Equipment.query.filter(Equipment.deleted_at.is_(None), Equipment.property_id == property_id)
    if unit is not None:
        query = query.filter(Equipment.unit_id == unit.id)
    else:
        query = query.filter(Equipment.unit_id.is_(None))
    rows = query.all()
    target = None
    ambiguous = []
    if serial:
        target = next((row for row in rows if (row.serial_number or "").upper() == serial), None)
        if target is None and kind and not force_new:
            blanks = [row for row in _same_kind(rows, kind) if not (row.serial_number or "").strip()]
            if len(blanks) == 1:
                target = blanks[0]
    elif kind and not force_new:
        same = _same_kind(rows, kind)
        if model:
            hits = [row for row in same if (row.model_number or "").upper() == model.upper()]
            if len(hits) == 1:
                target = hits[0]
            elif len(hits) > 1:
                ambiguous = hits
        elif len(same) == 1:
            only = same[0]
            different_brand = brand and only.brand and only.brand.lower() != brand.lower()
            if not different_brand:
                target = only
        elif len(same) > 1:
            ambiguous = same
    if ambiguous:
        return None, ambiguous
    created = target is None
    before = {} if created else {
        "kind": target.kind, "brand": target.brand, "style": target.style,
        "model": target.model_number, "serial": target.serial_number,
        "size": target.size_label, "color": target.color, "notes": target.notes,
        "phone": target.phone, "vendor": target.vendor, "purchase_date": target.purchase_date,
        "purchase_price": target.purchase_price, "warranty_expires": target.warranty_expires,
        "repair_notes": target.repair_notes, "parts_link": target.parts_link,
        "unit_id": target.unit_id, "property_id": target.property_id,
    }
    if created:
        row = Equipment(
            property_id=property_id,
            unit_id=unit.id if unit else None,
            job_id=job.id if job else None,
            template_id=template_id,
            kind=kind,
            brand=brand,
            model_number=model,
            serial_number=serial,
            size_label=size,
            style=style,
            color=color,
            notes=note,
            phone=phone,
            vendor=vendor,
            purchase_date=purchase_date,
            purchase_price=purchase_price,
            warranty_expires=warranty_expires,
            repair_notes=repair_notes,
            parts_link=parts_link,
            confidence=float(eq["confidence"]) if eq.get("confidence") not in (None, "") else None,
            source=source,
            created_by_id=user.id,
            created_at=utcnow(),
        )
        db.session.add(row)
    else:
        row = target
        if kind:
            row.kind = kind
        if brand:
            row.brand = brand
        if model:
            row.model_number = model
        if serial:
            row.serial_number = serial
        if size:
            row.size_label = size
        if style:
            row.style = style
        if color:
            row.color = color
        if note:
            row.notes = note
        if phone:
            row.phone = phone
        if vendor:
            row.vendor = vendor
        if parts_link:
            row.parts_link = parts_link
        if purchase_date:
            row.purchase_date = purchase_date
        if purchase_price is not None:
            row.purchase_price = purchase_price
        if warranty_expires:
            row.warranty_expires = warranty_expires
        if repair_notes:
            row.repair_notes = repair_notes
        if template_id and not row.template_id:
            row.template_id = template_id
        if job and not row.job_id:
            row.job_id = job.id
    if job:
        from app.services.equipment import describe

        line = describe(_piece_view(row) if not created else {
            "kind": kind,
            "brand": brand,
            "style": style,
            "color": color,
            "size": size,
            "model": model,
            "serial": serial,
        })
        if note and note not in line:
            line = f"{line}. Note: {note}".strip(". ")
        if line and line not in (job.detail or ""):
            job.detail = (job.detail + "\n" + line).strip()
    db.session.flush()
    audit(
        user.id,
        source,
        "create" if created else "update",
        "equipment",
        row.id,
        before,
        {
            "kind": row.kind,
            "brand": row.brand,
            "model": row.model_number,
            "serial": row.serial_number,
            "style": row.style,
            "size": row.size_label,
            "color": row.color,
            "notes": row.notes,
            "phone": row.phone,
            "vendor": row.vendor,
            "purchase_date": row.purchase_date.isoformat() if row.purchase_date else None,
            "purchase_price": row.purchase_price,
            "warranty_expires": row.warranty_expires.isoformat() if row.warranty_expires else None,
            "repair_notes": row.repair_notes,
            "parts_link": row.parts_link,
            "unit_id": row.unit_id,
            "property_id": row.property_id,
            "template_id": row.template_id,
        },
    )
    row._apt_created = created
    if created and unit is not None:
        from app.models import EquipmentMove

        snapshot = {
            "kind": row.kind, "brand": row.brand, "model": row.model_number,
            "serial": row.serial_number, "size": row.size_label, "style": row.style,
            "color": row.color, "vendor": row.vendor, "phone": row.phone,
        }
        install_move = EquipmentMove(
            equipment_id=row.id,
            from_unit_id=None,
            to_unit_id=unit.id,
            from_unit_number="",
            to_unit_number=unit.unit_number,
            event_type="install",
            source_inventory_missing=False,
            equipment_snapshot=json.dumps(snapshot),
            from_property_id=property_id,
            to_property_id=property_id,
            moved_by_id=user.id,
            reason="Equipment record added to unit inventory",
            note=note,
            created_at=utcnow(),
        )
        db.session.add(install_move)
        db.session.flush()
        audit(user.id, source, "install", "equipment", row.id, {}, {
            **snapshot, "property_id": property_id, "unit_id": unit.id,
            "title": f"Installed {row.kind or 'equipment'} in unit {unit.unit_number}",
            "related_unit_ids": [unit.id], "move_id": install_move.id,
        })
    return row, []


def _file_pieces(user, pieces, unit, job, property_id, source, stamp):
    saved = []
    warnings = []
    if not unit:
        return saved, warnings
    for piece in pieces:
        row, ambiguous = file_piece(user, piece, unit, job, property_id, source)
        if ambiguous:
            kind = _clip(piece.get("kind"), 80) or "appliances"
            warnings.append(
                f"Unit {unit.unit_number} already has {len(ambiguous)} {kind} cards. Say the serial so the note stays on one."
            )
            continue
        if row is None:
            continue
        if getattr(row, "_apt_created", False):
            row.created_at = stamp
        saved.append(row)
    return saved, warnings


def _save_equipment(user, payload, unit, job, property_id, source) -> list:
    """File every appliance she named on this unit, title or no title."""
    extra = [item for item in (payload.get("equipment_items") or []) if isinstance(item, dict)]
    pieces = _gear_pieces(payload.get("equipment") or {}, extra)
    media_id = int(payload["media_id"]) if payload.get("media_id") else None
    rows = []
    for piece in pieces:
        row, _ambiguous = file_piece(user, piece, unit, job, property_id, source)
        if row is None:
            continue
        if media_id and not row.media_id and not rows:
            row.media_id = media_id
        rows.append(row)
    return rows


def _property_match(payload) -> tuple[Property | None, str]:
    if payload.get("property_id"):
        prop = db.session.get(Property, int(payload["property_id"]))
        if not prop or prop.deleted_at:
            return None, "That property is already gone."
        return prop, ""
    name = (payload.get("match_name") or payload.get("property_name") or "").strip()
    city = (payload.get("city") or "").strip()
    if not name:
        return None, "Which property?"
    catalog = Property.query.filter(Property.deleted_at.is_(None)).all()
    skip = {"the", "and", "apartment", "apartments", "property", "properties", "please"}
    words = [word for word in re.findall(r"[a-z0-9]+", name.lower()) if word not in skip and len(word) > 2]
    city_words = [word for word in words if any(row.city and word == row.city.name.lower() for row in catalog)]
    if city:
        city_words.append(city.lower())
    name_words = [word for word in words if word not in city_words]
    rows = []
    for row in catalog:
        label = row.name.lower()
        town = row.city.name.lower() if row.city else ""
        if city_words and not any(word == town or word in town for word in city_words):
            continue
        if name_words and not any(word in label for word in name_words):
            continue
        if not name_words and not city_words:
            continue
        rows.append(row)
    exact = [row for row in rows if row.name.lower() == name.lower()]
    rows = exact or rows
    if not rows:
        return None, f"No property named {name}."
    if len(rows) > 1:
        from app.services.geo import state_name

        bits = []
        for row in rows[:6]:
            city = row.city.name if row.city else ""
            state = state_name(row.city.region) if row.city else ""
            where = ", ".join(bit for bit in (city, state) if bit)
            bits.append(f"{row.name} in {where}" if where else row.name)
        return None, "More than one match: " + "; ".join(bits) + "."
    return rows[0], ""


def _entity(name: str, entity_id: int):
    from app.models import Unit, UnitTask

    model = {"job": Job, "unit": Unit, "expense": Expense, "unit_task": UnitTask, "equipment": Equipment}.get(name)
    if not model:
        return None
    return db.session.get(model, entity_id)


def _record_snapshot(name: str, row) -> dict:
    common = {"property_id": row.property_id, "deleted_at": row.deleted_at.isoformat() if row.deleted_at else None}
    if name == "unit":
        return {**common, "unit_id": row.id, "unit_number": row.unit_number, "building": row.building, "occupancy": row.occupancy}
    if name == "job":
        return {**common, "unit_id": row.unit_id, "title": row.title, "detail": row.detail, "status": row.status}
    if name == "unit_task":
        return {**common, "unit_id": row.unit_id, "title": row.title, "kind": row.kind, "status": row.status, "vendor": row.vendor, "notes": row.notes}
    if name == "equipment":
        return {**common, "unit_id": row.unit_id, "kind": row.kind, "brand": row.brand, "style": row.style, "model": row.model_number, "serial": row.serial_number, "size": row.size_label, "color": row.color, "notes": row.notes}
    return {**common}


def re_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())
