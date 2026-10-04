"""Reusable contractor names so calling someone to another unit is not a new name."""
from __future__ import annotations

import re

from app.builddb.builddb import db
from app.models import Contractor
from app.services.clock import utcnow
from app.services.people import clean_phone, person_label
from app.services.records import audit


def clean_name(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip(" .,"))[:160]


def list_contractors() -> list[Contractor]:
    return (
        Contractor.query.filter(Contractor.deleted_at.is_(None))
        .order_by(Contractor.last_used_at.desc(), Contractor.name.asc())
        .all()
    )


def match_contractor(hint: str) -> Contractor | None:
    want = clean_name(hint).lower()
    if not want:
        return None
    rows = list_contractors()
    exact = [row for row in rows if (row.name or "").lower() == want]
    if len(exact) == 1:
        return exact[0]
    hits = [row for row in rows if want in (row.name or "").lower() or (row.name or "").lower() in want]
    if len(hits) == 1:
        return hits[0]
    return exact[0] if exact else None


def remember_contractor(user, name: str, *, phone: str = "", trade: str = "", notes: str = "", company: str = "") -> Contractor | None:
    label = clean_name(name)
    if not label:
        return None
    digits = clean_phone(phone) if phone else ""
    trade_label = re.sub(r"\s+", " ", (trade or "").strip())[:80]
    company_label = re.sub(r"\s+", " ", (company or "").strip())[:160]
    note = (notes or "").strip()[:2000]
    row = match_contractor(label)
    if row is None:
        row = Contractor(
            name=label,
            company=company_label,
            phone=digits,
            trade=trade_label,
            notes=note,
            last_used_at=utcnow(),
            created_by_id=getattr(user, "id", None),
            created_at=utcnow(),
        )
        db.session.add(row)
        db.session.flush()
        audit(
            getattr(user, "id", None),
            "human",
            "create",
            "contractor",
            row.id,
            {},
            {"name": row.name, "company": row.company, "phone": row.phone, "trade": row.trade},
        )
        return row
    before = {"name": row.name, "company": row.company, "phone": row.phone, "trade": row.trade, "notes": row.notes}
    if digits and not row.phone:
        row.phone = digits
    if trade_label and not row.trade:
        row.trade = trade_label
    if company_label and not row.company:
        row.company = company_label
    if note and not row.notes:
        row.notes = note
    if label and label.lower() != (row.name or "").lower():
        row.name = label
    row.last_used_at = utcnow()
    after = {"name": row.name, "company": row.company, "phone": row.phone, "trade": row.trade, "notes": row.notes}
    if before != after:
        audit(getattr(user, "id", None), "human", "update", "contractor", row.id, before, after)
    return row


def parse_contractor_blob(text: str) -> dict:
    """Split 'Jane with Ace Paint 432-555-0100 trashout' into name, company, phone, trade."""
    raw = re.sub(r"\s+", " ", (text or "").strip(" .,"))
    phone = ""
    found = re.search(r"(\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})", raw)
    if found:
        phone = found.group(1)
        raw = (raw[: found.start()] + " " + raw[found.end() :]).strip(" ,")
    trade = ""
    from app.services.ready import job_label, match_job

    leftover = raw
    matched = match_job(leftover)
    if matched:
        trade = job_label(matched)
        leftover = re.sub(rf"\b{re.escape(matched)}\b", " ", leftover, flags=re.I)
        leftover = re.sub(r"\s+", " ", leftover).strip(" ,")
    if not leftover:
        leftover = raw
        trade = ""
    company = ""
    who = re.match(r"^(?P<who>[A-Za-z][A-Za-z.'-]{1,30})\s+(?:with|at|from)\s+(?P<co>[A-Za-z0-9][A-Za-z0-9 .'&/-]{1,60})$", leftover)
    if who:
        name = who.group("who")
        company = who.group("co").strip()
    else:
        name = leftover
    return {"name": clean_name(name), "company": clean_name(company), "phone": phone, "trade": trade}


def describe_contractor(row: Contractor) -> str:
    bits = [row.name]
    if getattr(row, "company", ""):
        bits.append(row.company)
    if row.phone:
        bits.append(row.phone)
    if row.trade:
        bits.append(row.trade)
    return " · ".join(bits)


def call_to_unit(user, contractor: Contractor, unit, title: str = "", source: str = "human"):
    from app.services.board import add_needed, set_occupancy
    from app.services.ready import job_label, match_job

    if (unit.occupancy or "") == "occupied":
        return {"ok": False, "reply": f"Mark unit {unit.unit_number} vacant before sending make-ready work."}
    job = (title or "").strip()
    slug = match_job(job) if job else ""
    if slug:
        job = job_label(slug)
    if not job:
        job = contractor.trade or contractor.name
    remember_contractor(user, contractor.name, phone=contractor.phone, trade=contractor.trade)
    if (unit.occupancy or "") != "occupied" and (unit.occupancy or "") != "make_ready":
        set_occupancy(user, unit, "make_ready", source)
    unit.rentable = False
    rows = add_needed(user, unit, [job[:200]], source, kind="vendor", vendor=contractor.name)
    who = person_label(getattr(user, "id", None))
    reply = f"Called {contractor.name} to unit {unit.unit_number}"
    prop = getattr(unit, "property", None)
    if prop:
        reply += f" at {prop.name}"
    reply += f" for {job}."
    if contractor.phone:
        reply += f" Phone {contractor.phone}."
    if who:
        reply += f" Saved by {who}."
    return {"ok": True, "reply": reply, "unit_id": unit.id, "contractor_id": contractor.id, "task_id": rows[0].id if rows else None}


def roster_lines() -> list[str]:
    rows = list_contractors()
    if not rows:
        return []
    return [describe_contractor(row) for row in rows]
