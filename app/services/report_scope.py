"""Who a report is for.

Managers, regionals, and admins get the properties they cover, with each
person listed. Everyone else gets their own week. A saved copy stays in
that lane: another login does not see it unless they cover every property
on it, or it is their own week.
"""
from __future__ import annotations

import json

from app.builddb.builddb import db
from app.models import Job, PropertyAccess, User
from app.services.access import has_capability, region_ids, role_of, sees_all, visible_property_ids

LEADER_ROLES = {
    "owner",
    "admin",
    "regional_manager",
    "regional_property_manager",
    "maintenance_regional",
    "property_manager",
    "assistant_manager",
    "maintenance_manager",
}
REGION_ROLES = {"regional_manager", "regional_property_manager", "maintenance_regional"}
AUDIENCE_LINE = "Prepared for property managers, regional managers, and admins."


def manages_team_report(user) -> bool:
    return bool(user and role_of(user) in LEADER_ROLES and has_capability(user, "manage_reports"))


def name_for(user_id, cache: dict) -> str:
    if not user_id:
        return ""
    user_id = int(user_id)
    if user_id not in cache:
        person = db.session.get(User, user_id)
        cache[user_id] = ((person.display_name or person.username) if person else "") or ""
    return cache[user_id]


def people_totals(jobs, expenses, mile_lines) -> list[dict]:
    """One row per person: jobs, completed, spend, miles."""
    stats: dict[int, dict] = {}

    def bucket(user_id):
        if not user_id:
            return None
        user_id = int(user_id)
        return stats.setdefault(
            user_id,
            {"user_id": user_id, "jobs": 0, "done": 0, "spend_cents": 0, "miles": 0.0},
        )

    for job in jobs:
        row = bucket(job.created_by_id)
        if not row:
            continue
        row["jobs"] += 1
        if job.status == "done":
            row["done"] += 1
    for exp in expenses:
        row = bucket(exp.user_id or exp.created_by_id)
        if row:
            row["spend_cents"] += int(exp.amount_cents or 0)
    for line in mile_lines or []:
        row = bucket(line.get("user_id"))
        if row:
            row["miles"] = round(row["miles"] + float(line.get("miles") or 0), 1)
    cache: dict[int, str] = {}
    out = []
    for row in stats.values():
        row["name"] = name_for(row["user_id"], cache) or "Someone"
        out.append(row)
    out.sort(key=lambda item: (-item["jobs"], item["name"].lower()))
    return out


def people_choices(user) -> list:
    """People a manager can pull a one-person week for."""
    if not manages_team_report(user):
        return []
    if sees_all(user):
        rows = User.query.filter_by(active=True).order_by(User.display_name.asc(), User.username.asc()).limit(80).all()
        return [row for row in rows if row.role != "viewer"]
    ids = visible_property_ids(user)
    chosen = {row.user_id for row in PropertyAccess.query.filter(PropertyAccess.property_id.in_(ids or {-1})).all()}
    chosen |= {
        row[0]
        for row in db.session.query(Job.created_by_id)
        .filter(Job.property_id.in_(ids or {-1}), Job.created_by_id.isnot(None), Job.deleted_at.is_(None))
        .distinct()
        .all()
    }
    chosen.add(user.id)
    return (
        User.query.filter(User.id.in_(chosen or {-1}), User.active.is_(True))
        .order_by(User.display_name.asc(), User.username.asc())
        .limit(80)
        .all()
    )


def _person_id(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _region_subject(user) -> str:
    from app.models import Region

    ids = region_ids(user)
    if not ids:
        return "Region"
    names = [
        row.name
        for row in Region.query.filter(Region.id.in_(ids), Region.active.is_(True)).order_by(Region.name.asc()).all()
    ]
    return ", ".join(names[:3]) or "Region"


def scope_for(user, person_id=None, property_id=None) -> dict:
    """Property set and, when this is one person, that person's id."""
    asked = _person_id(person_id)
    if person_id not in (None, "") and asked is None:
        return {"ok": False, "reply": "Pick a person from the list."}
    single = _person_id(property_id)
    if property_id not in (None, "") and single is None:
        return {"ok": False, "reply": "That property is not on your login."}
    leader = manages_team_report(user)
    if asked and asked != getattr(user, "id", None) and not leader:
        return {"ok": False, "reply": "You can save your own week. Managers, regionals, and admins cover the properties."}
    if asked:
        person = db.session.get(User, asked)
        if not person or not person.active:
            return {"ok": False, "reply": "That person does not have a login."}
        if asked != getattr(user, "id", None) and leader and not sees_all(user):
            if asked not in {row.id for row in people_choices(user)}:
                return {"ok": False, "reply": "That person is not on your properties."}
    visible = None if sees_all(user) else set(visible_property_ids(user))
    if single is not None:
        if visible is not None and single not in visible:
            return {"ok": False, "reply": "That property is not on your login."}
        visible = {single}
    if asked or not leader:
        target = asked or user.id
        person = db.session.get(User, target)
        subject = ((person.display_name or person.username) if person else "") or "This person"
        return {
            "ok": True,
            "property_ids": visible,
            "person_id": target,
            "audience": "person",
            "subject": subject,
            "audience_line": f"This copy is one person's week: {subject}.",
        }
    role = role_of(user)
    if role in ("owner", "admin") and single is None:
        return {
            "ok": True,
            "property_ids": None,
            "person_id": None,
            "audience": "company",
            "subject": "",
            "audience_line": AUDIENCE_LINE,
        }
    subject = _region_subject(user) if role in REGION_ROLES else ""
    return {
        "ok": True,
        "property_ids": visible if visible is not None else set(),
        "person_id": None,
        "audience": "region" if role in REGION_ROLES else "property",
        "subject": subject,
        "audience_line": AUDIENCE_LINE,
    }


def report_lane(report) -> dict:
    """Audience, person, and property fence stored on a saved copy.

    Older copies have no fence. Those were the company packet.
    """
    snap = {}
    raw = getattr(report, "snapshot_json", None) or ""
    if raw:
        try:
            loaded = json.loads(raw)
        except (TypeError, ValueError):
            loaded = {}
        if isinstance(loaded, dict):
            snap = loaded
    audience = (snap.get("audience") or "").strip()
    person_id = _person_id(snap.get("person_id")) if "person_id" in snap else None
    if "property_ids" in snap:
        raw_ids = snap.get("property_ids")
        try:
            property_ids = None if raw_ids is None else tuple(sorted(int(i) for i in raw_ids))
        except (TypeError, ValueError):
            property_ids = tuple()
    else:
        property_ids = None
        if not audience and getattr(report, "kind", "") != "person":
            audience = "company"
    if not audience and person_id:
        audience = "person"
    return {"audience": audience, "person_id": person_id, "property_ids": property_ids}


def _covers(user, property_ids) -> bool:
    if not property_ids:
        return False
    return set(property_ids) <= set(visible_property_ids(user))


def can_open_report(user, report) -> bool:
    """True when this login is allowed to read this saved copy."""
    if not report or report.deleted_at or report.status not in ("ready", "sent"):
        return False
    if not user:
        return False
    role = role_of(user)
    uid = getattr(user, "id", None)
    lane = report_lane(report)
    if role in ("owner", "admin"):
        return True
    if uid and report.created_by_id == uid:
        return True
    if lane["person_id"] and uid and lane["person_id"] == uid:
        return True
    if lane["audience"] == "company":
        return role == "viewer" and bool(getattr(user, "can_see_reports", False))
    if not manages_team_report(user):
        return False
    return _covers(user, lane["property_ids"])


def reports_for(user) -> list:
    """Saved reports this login is allowed to open, newest first."""
    from app.models import Report

    rows = (
        Report.query.filter(Report.deleted_at.is_(None), Report.status.in_(("ready", "sent")))
        .order_by(Report.id.desc())
        .all()
    )
    return [row for row in rows if can_open_report(user, row)]
