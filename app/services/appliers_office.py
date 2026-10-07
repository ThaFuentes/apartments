"""Office tools. Each one calls the same function the page uses."""
from __future__ import annotations

from app.builddb.builddb import db


def apply_show_property_map(user, payload: dict, source: str) -> dict:
    from app.services.site_map import map_for

    prop, problem = _resolve_named(user, payload.get("property_name") or "")
    if problem:
        return {"ok": False, "reply": problem}
    row = map_for(prop.id)
    link = f"/map?property={prop.id}"
    if row:
        return {"ok": True, "reply": f"The map for {prop.name} is at {link}.", "property_id": prop.id}
    return {"ok": True, "reply": f"No map for {prop.name} yet. Send the picture here and say this is the map for {prop.name}. The page is {link}.", "property_id": prop.id}


def apply_list_regions(user, payload: dict, source: str) -> dict:
    from app.models import Region, RegionCity
    from app.services.access.management import can_manage_regions

    if not can_manage_regions(user):
        return {"ok": False, "reply": "Only an owner or an admin can set up regions."}
    rows = Region.query.order_by(Region.name.asc()).all()
    if not rows:
        return {"ok": True, "reply": "No regions yet. Say create region Permian."}
    lines = []
    for region in rows:
        count = RegionCity.query.filter_by(region_id=region.id).count()
        state = "" if region.active else " (off)"
        lines.append(f"{region.name}{state} — {count} {'city' if count == 1 else 'cities'}")
    return {"ok": True, "reply": "Regions:\n" + "\n".join(lines)}


def apply_remove_property_map(user, payload: dict, source: str) -> dict:
    from app.services.site_map import can_upload_map, remove_map

    prop, problem = _resolve_named(user, payload.get("property_name") or "")
    if problem:
        return {"ok": False, "reply": problem}
    if not can_upload_map(user, prop.id):
        return {"ok": False, "reply": "This login cannot change that property's map."}
    return {"ok": True, "reply": remove_map(user, prop), "property_id": prop.id}


def apply_manage_region(user, payload: dict, source: str) -> dict:
    from app.models import City, Region, RegionAccess, RegionCity, User
    from app.services.access import set_user_capability
    from app.services.access.management import (
        can_manage_regions,
        create_region,
        delete_region,
        set_region_cities,
        set_region_people,
        update_region,
    )
    from app.services.people import find_person

    if not can_manage_regions(user):
        return {"ok": False, "reply": "Only an owner or an admin can set up a region."}
    action = (payload.get("action") or "").strip()
    try:
        if action == "create":
            region = create_region(user, payload.get("name") or "")
            return {"ok": True, "reply": f"{region.name} is ready. Add a city with: put Odessa in region {region.name}."}
        region = _region_named(payload.get("name") or "")
        if region is None:
            return {"ok": False, "reply": f"I can't find a region named {payload.get('name') or 'that'}."}
        if action == "rename":
            message = update_region(user, region, payload.get("new_name") or "", True)
            return {"ok": True, "reply": message}
        if action == "delete":
            message = delete_region(user, region)
            return {"ok": True, "reply": message}
        if action in {"add_city", "remove_city"}:
            city = _city_named(payload.get("city") or "")
            if city is None:
                return {"ok": False, "reply": f"I can't find a saved city named {payload.get('city') or 'that'}. Cities come from properties already on file."}
            have = {row.city_id for row in RegionCity.query.filter_by(region_id=region.id).all()}
            if action == "add_city":
                have.add(city.id)
            else:
                have.discard(city.id)
            message = set_region_cities(user, region, have)
            return {"ok": True, "reply": message}
        if action in {"add_person", "remove_person"}:
            person = find_person(payload.get("person") or "")
            if person is None:
                return {"ok": False, "reply": "Which person? I need one login."}
            if (person.role or "") not in {"regional_manager", "regional_property_manager", "maintenance_regional"}:
                return {"ok": False, "reply": f"{person.display_name or person.username} is not a regional login. Assign a regional manager, a regional property manager, or a regional maintenance manager."}
            have = {row.user_id for row in RegionAccess.query.filter_by(region_id=region.id).all()}
            if action == "add_person":
                have.add(person.id)
            else:
                have.discard(person.id)
            message = set_region_people(user, region, have)
            return {"ok": True, "reply": message}
        if action in {"allow_default", "deny_default"}:
            person = find_person(payload.get("person") or "")
            if person is None or (person.role or "") != "property_manager":
                return {"ok": False, "reply": "That default-property choice is for one property manager."}
            granted = True if action == "allow_default" else False
            set_user_capability(user, person, "open_team_default_property", granted, f"region:{region.id}")
            word = "can" if granted else "cannot"
            return {"ok": True, "reply": f"{person.display_name or person.username} {word} choose a default property in {region.name}."}
    except (PermissionError, ValueError) as exc:
        db.session.rollback()
        return {"ok": False, "reply": str(exc)}
    return {"ok": False, "reply": "Say create region, put a city in a region, or assign someone to a region."}


def apply_send_back(user, payload: dict, source: str) -> dict:
    from app.models import UnitTask
    from app.services.appliers_field import _unit_for
    from app.services.ready import send_back_task

    unit = _unit_for(user, payload)
    if unit is None:
        return {"ok": False, "reply": "Which unit, and at which property?"}
    title = (payload.get("job") or payload.get("title") or "").strip().lower()
    note = (payload.get("note") or "").strip()
    rows = UnitTask.query.filter_by(unit_id=unit.id).filter(UnitTask.deleted_at.is_(None)).all()
    hits = [row for row in rows if title and (title in (row.title or "").lower() or (row.title or "").lower() in title)]
    if len(hits) != 1:
        open_names = ", ".join(row.title for row in rows[:8]) or "nothing"
        if not hits:
            return {"ok": False, "reply": f"I can't find {payload.get('job') or 'that item'} on unit {unit.unit_number}. On that unit: {open_names}."}
        return {"ok": False, "reply": f"Which one on unit {unit.unit_number}? {', '.join(row.title for row in hits[:8])}"}
    result = send_back_task(user, hits[0], note)
    result["source"] = source
    return result


def apply_mark_rentable(user, payload: dict, source: str) -> dict:
    from app.services.appliers_field import _unit_for
    from app.services.ready import set_rentable

    unit = _unit_for(user, payload)
    if unit is None:
        return {"ok": False, "reply": "Which unit, and at which property?"}
    result = set_rentable(user, unit, bool(payload.get("rentable")))
    result["source"] = source
    return result


def apply_set_move_out(user, payload: dict, source: str) -> dict:
    from app.services.appliers_field import _unit_for
    from app.services.ready import set_move_out_date

    unit = _unit_for(user, payload)
    if unit is None:
        return {"ok": False, "reply": "Which unit, and at which property?"}
    day = _iso_day(payload.get("move_out_date") or "")
    if not day and (payload.get("move_out_date") or "").strip():
        return {"ok": False, "reply": "Use a date like 2026-10-15, or say today or tomorrow."}
    result = set_move_out_date(user, unit, day)
    result["source"] = source
    return result


def apply_restore_inventory(user, payload: dict, source: str) -> dict:
    from app.services.people import find_person
    from app.services.reversals import restore_removed_inventory

    person = find_person(payload.get("person") or "")
    if person is None:
        return {"ok": False, "reply": "Which person? I need one login."}
    result = restore_removed_inventory(user, person.id, int(payload.get("days") or 30))
    result["source"] = source
    return result


def apply_reverse_audit(user, payload: dict, source: str) -> dict:
    from app.services.reversals import reverse_audit

    try:
        audit_id = int(payload.get("audit_id"))
    except (TypeError, ValueError):
        return {"ok": False, "reply": "Which audit number? Reverse audit 12."}
    result = reverse_audit(user, audit_id)
    result["source"] = source
    return result


def apply_unlock_login(user, payload: dict, source: str) -> dict:
    from app.services.access.management import can_manage_user
    from app.services.people import find_person, unlock_login
    from app.services.records import audit

    person = find_person(payload.get("person") or payload.get("username") or "")
    if person is None:
        return {"ok": False, "reply": "I can't find that login."}
    if not can_manage_user(user, person):
        return {"ok": False, "reply": "This login cannot unlock that person."}
    before = {"active": bool(person.active), "failed_login_attempts": person.failed_login_attempts or 0}
    unlock_login(person)
    audit(
        user.id,
        source if source in {"ai", "human"} else "ai",
        "update",
        "user",
        person.id,
        before,
        {"active": True, "failed_login_attempts": 0, "locked_until": ""},
    )
    name = person.display_name or person.username
    return {"ok": True, "reply": f"{name} can sign in again."}


def apply_remove_contractor(user, payload: dict, source: str) -> dict:
    from app.services.clock import utcnow
    from app.services.contractors import match_contractor
    from app.services.records import audit

    row = match_contractor(payload.get("name") or "")
    if row is None:
        return {"ok": False, "reply": "I can't find that contractor. Say the name the way it is saved."}
    row.deleted_at = utcnow()
    audit(user.id, source if source in {"ai", "human"} else "ai", "delete", "contractor", row.id, {"name": row.name}, {})
    return {"ok": True, "reply": f"{row.name} is off the list."}


def apply_save_how_to(user, payload: dict, source: str) -> dict:
    from app.models import Equipment
    from app.services.appliers_field import _unit_for
    from app.services.equipment import appliance_kinds
    from app.services.upkeep import save_how_to

    gear_id = payload.get("equipment_id")
    gear = None
    if gear_id not in (None, ""):
        try:
            gear = db.session.get(Equipment, int(gear_id))
        except (TypeError, ValueError):
            gear = None
    if gear is None or getattr(gear, "deleted_at", None):
        query = Equipment.query.filter(Equipment.deleted_at.is_(None))
        unit = _unit_for(user, payload) if payload.get("unit_number") else None
        if unit is not None:
            query = query.filter(Equipment.unit_id == unit.id)
        else:
            prop_id = payload.get("property_id")
            named = (payload.get("property_name") or "").strip()
            if not prop_id and named:
                prop, problem = _resolve_named(user, named)
                if prop is None:
                    return {"ok": False, "reply": problem or "Which property?"}
                prop_id = prop.id
            if prop_id:
                query = query.filter(Equipment.property_id == int(prop_id), Equipment.unit_id.is_(None))
        kinds = appliance_kinds(payload.get("gear") or "")
        if kinds:
            query = query.filter(Equipment.kind.in_(kinds))
        rows = query.order_by(Equipment.id.desc()).limit(2).all()
        if len(rows) != 1:
            return {"ok": False, "reply": "Which piece? Name the unit or the property so I can find one."}
        gear = rows[0]
    result = save_how_to(user, gear, payload.get("how_to") or "", source if source in {"ai", "human"} else "human")
    return result


_SPOKEN_ROLES = (
    ("regional property manager", "regional_property_manager"),
    ("regional maintenance manager", "maintenance_regional"),
    ("regional manager", "regional_manager"),
    ("maintenance supervisor", "maintenance_supervisor"),
    ("maintenance manager", "maintenance_manager"),
    ("maintenance person", "maintenance_person"),
    ("assistant manager", "assistant_manager"),
    ("property manager", "property_manager"),
    ("office staff", "office"),
    ("office", "office"),
)


def _spoken_role(text: str) -> str:
    key = " ".join((text or "").split()).lower()
    for label, slug in _SPOKEN_ROLES:
        if key == label:
            return slug
    return ""


def _person_or_reply(payload: dict):
    from app.services.people import find_person

    person = find_person(payload.get("person") or payload.get("username") or "")
    if person is None:
        return None, "I can't find that login."
    return person, ""


def apply_ban_ip(user, payload: dict, source: str) -> dict:
    from app.services.security_ops import temp_ban_ip

    return temp_ban_ip(user, payload.get("ip") or "", payload.get("hours"), payload.get("reason") or "")


def apply_unban_ip(user, payload: dict, source: str) -> dict:
    from app.services.security_ops import lift_ip_ban

    return lift_ip_ban(user, payload.get("ip") or "")


def apply_ban_device(user, payload: dict, source: str) -> dict:
    from app.services.security_ops import temp_ban_device

    return temp_ban_device(user, payload.get("device_fp") or "", payload.get("hours"), payload.get("reason") or "")


def apply_unban_device(user, payload: dict, source: str) -> dict:
    from app.services.security_ops import lift_device_ban

    return lift_device_ban(user, payload.get("device_fp") or "")


def apply_send_password_reset(user, payload: dict, source: str) -> dict:
    from app.services.access import role_of
    from app.services.access.management import can_manage_user
    from app.services.passwords import issue_reset, send_reset
    from app.services.records import audit

    person, problem = _person_or_reply(payload)
    if problem:
        return {"ok": False, "reply": problem}
    if person.role == "owner" and getattr(user, "id", None) != person.id:
        return {"ok": False, "reply": "Another owner is not reset from here."}
    if getattr(user, "id", None) != person.id and role_of(user) != "owner" and not can_manage_user(user, person):
        return {"ok": False, "reply": "This login cannot reset that person."}
    token = issue_reset(person)
    if not token:
        return {"ok": False, "reply": "That login has no reset inbox yet."}
    ok, msg = send_reset(person, token)
    if token and token in (msg or ""):
        msg = "The reset email was sent."
    audit(
        getattr(user, "id", None),
        source if source in {"ai", "human"} else "ai",
        "password_reset",
        "user",
        person.id,
        {},
        {"sent": bool(ok)},
    )
    if not ok:
        return {"ok": False, "reply": msg or "The reset email did not send."}
    return {"ok": True, "reply": msg or f"A reset email is on the way to {person.label()}."}


def apply_set_security_watch(user, payload: dict, source: str) -> dict:
    from app.services.access import role_of
    from app.services.records import audit
    from app.services.security_ops import set_security_watch

    if role_of(user) != "owner":
        return {"ok": False, "reply": "Only an owner can choose who watches security."}
    person, problem = _person_or_reply(payload)
    if problem:
        return {"ok": False, "reply": problem}
    if not person.is_bot or person.role == "owner":
        return {"ok": False, "reply": "Security watch is for a bot login."}
    on = bool(payload.get("on"))
    before = bool((person.extra_data or {}).get("security_watch")) if isinstance(person.extra_data, dict) else False
    set_security_watch(person, on)
    audit(
        getattr(user, "id", None),
        source if source in {"ai", "human"} else "ai",
        "update",
        "user",
        person.id,
        {"security_watch": before},
        {"security_watch": on},
    )
    word = "can watch the site" if on else "no longer watches the site"
    return {"ok": True, "reply": f"{person.label()} {word}."}


def apply_mark_bot(user, payload: dict, source: str) -> dict:
    from app.services.access import role_of
    from app.services.access.management import can_manage_user
    from app.services.records import audit
    from app.services.security_ops import set_security_watch
    from app.services.twofa import turn_off

    person, problem = _person_or_reply(payload)
    if problem:
        return {"ok": False, "reply": problem}
    if person.role == "owner":
        return {"ok": False, "reply": "An owner login is not a bot."}
    make = bool(payload.get("on"))
    if role_of(user) != "owner" and not can_manage_user(user, person):
        return {"ok": False, "reply": "This login cannot change that person."}
    if not make and role_of(user) != "owner":
        return {"ok": False, "reply": "Only an owner can unmark a bot."}
    before = bool(person.is_bot)
    person.is_bot = make
    if not make:
        turn_off(person)
        set_security_watch(person, False)
    audit(
        getattr(user, "id", None),
        source if source in {"ai", "human"} else "ai",
        "update",
        "user",
        person.id,
        {"is_bot": before},
        {"is_bot": make},
    )
    word = "a bot login" if make else "a regular login"
    return {"ok": True, "reply": f"{person.label()} is {word} now."}


def apply_create_job_title(user, payload: dict, source: str) -> dict:
    from app.services.access.management import create_custom_role

    based_on = _spoken_role(payload.get("based_on") or "")
    if not based_on:
        return {"ok": False, "reply": "Start that title from an on-the-ground role, like office or maintenance person."}
    try:
        row = create_custom_role(user, payload.get("label") or "", based_on)
    except (PermissionError, ValueError) as exc:
        db.session.rollback()
        return {"ok": False, "reply": str(exc)}
    return {"ok": True, "reply": f"{row.label} is ready. You can assign it like any other job title."}


def apply_set_hat(user, payload: dict, source: str) -> dict:
    from app.services.hats import set_hat

    person, problem = _person_or_reply(payload)
    if problem:
        return {"ok": False, "reply": problem}
    role = _spoken_role(payload.get("role") or "")
    if not role:
        return {"ok": False, "reply": "That extra role has to be an on-the-ground title, like maintenance supervisor."}
    property_id, region_id, problem = _hat_place(user, payload)
    if problem:
        return {"ok": False, "reply": problem}
    try:
        reply = set_hat(user, person, role, property_id=property_id, region_id=region_id)
    except (PermissionError, ValueError) as exc:
        db.session.rollback()
        return {"ok": False, "reply": str(exc)}
    return {"ok": True, "reply": reply}


def apply_clear_hat(user, payload: dict, source: str) -> dict:
    from app.models import UserHat
    from app.services.hats import clear_hat

    person, problem = _person_or_reply(payload)
    if problem:
        return {"ok": False, "reply": problem}
    role = _spoken_role(payload.get("role") or "")
    if not role:
        return {"ok": False, "reply": "Which extra role should I remove?"}
    property_id, region_id, problem = _hat_place(user, payload)
    if problem:
        return {"ok": False, "reply": problem}
    query = UserHat.query.filter_by(user_id=person.id, role=role)
    if property_id:
        query = query.filter_by(property_id=property_id, region_id=None)
    else:
        query = query.filter_by(region_id=region_id, property_id=None)
    rows = query.limit(2).all()
    if len(rows) != 1:
        return {"ok": False, "reply": "That extra role is not on this login."}
    try:
        reply = clear_hat(user, person, rows[0].id)
    except (PermissionError, ValueError) as exc:
        db.session.rollback()
        return {"ok": False, "reply": str(exc)}
    return {"ok": True, "reply": reply}


def apply_pin_property(user, payload: dict, source: str) -> dict:
    from app.services.access import can_see_property, pin_property

    prop, problem = _resolve_named(user, payload.get("property_name") or "")
    if problem or prop is None:
        return {"ok": False, "reply": problem or "Which property?"}
    if not can_see_property(user, prop.id):
        return {"ok": False, "reply": "That property is not on your login."}
    return {"ok": True, "reply": pin_property(user, prop, bool(payload.get("pinned")))}


def apply_send_test_email(user, payload: dict, source: str) -> dict:
    from app.services.access import role_of
    from app.services.mail import send_test
    from app.services.people import clean_email

    if role_of(user) != "owner":
        return {"ok": False, "reply": "Only an owner can send a test email."}
    try:
        inbox = clean_email(payload.get("to") or "")
    except ValueError as exc:
        return {"ok": False, "reply": str(exc)}
    if not inbox:
        return {"ok": False, "reply": "Type the inbox that should receive the test."}
    ok, msg = send_test(inbox)
    return {"ok": bool(ok), "reply": msg or ("Sent." if ok else "The test email did not send.")}


def apply_set_reset_email(user, payload: dict, source: str) -> dict:
    from app.services.people import clean_email
    from app.services.records import audit

    try:
        email = clean_email(payload.get("email") or "")
    except ValueError as exc:
        return {"ok": False, "reply": str(exc)}
    previous = user.reset_email
    user.reset_email = email
    audit(
        getattr(user, "id", None),
        source if source in {"ai", "human"} else "ai",
        "update",
        "user",
        getattr(user, "id", None),
        {"reset_email": previous},
        {"reset_email": email},
    )
    if email:
        return {"ok": True, "reply": "Password-reset email saved."}
    return {"ok": True, "reply": "Password resets will use your login email."}


def apply_add_place_gear(user, payload: dict, source: str) -> dict:
    from app.services.equipment import appliance_kinds
    from app.services.upkeep import add_place_gear

    prop, problem = _resolve_named(user, payload.get("property_name") or "")
    if problem or prop is None:
        return {"ok": False, "reply": problem or "Which property?"}
    spoken = (payload.get("kind") or "").strip()
    kinds = appliance_kinds(spoken)
    kind = kinds[0] if len(kinds) == 1 else spoken
    try:
        every = int(payload.get("every_days") or 0)
    except (TypeError, ValueError):
        every = 0
    task = " ".join((payload.get("task") or "").split())
    if every and not task:
        task = f"Check the {kind}"
    if every and (every < 1 or every > 3650):
        return {"ok": False, "reply": "How often, in days? Like every 90 days."}
    return add_place_gear(
        user,
        prop,
        kind,
        payload.get("brand") or "",
        payload.get("how_to") or "",
        task,
        every or 30,
        source if source in {"ai", "human"} else "human",
    )


def _hat_place(user, payload: dict):
    if (payload.get("property_name") or "").strip():
        prop, problem = _resolve_named(user, payload.get("property_name") or "")
        if problem or prop is None:
            return None, None, problem or "Which property?"
        return prop.id, None, ""
    region = _region_named(payload.get("region") or "")
    if region is None:
        return None, None, "Which property or region is that extra role for?"
    return None, region.id, ""


def _region_named(name: str):
    from app.models import Region

    text = " ".join((name or "").split()).lower()
    if not text:
        return None
    rows = [row for row in Region.query.all() if (row.name or "").lower() == text]
    return rows[0] if len(rows) == 1 else None


def _city_named(name: str):
    from app.models import City

    text = " ".join((name or "").split()).lower()
    if not text:
        return None
    rows = [row for row in City.query.all() if (row.name or "").lower() == text]
    return rows[0] if len(rows) == 1 else None


def _iso_day(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    from datetime import date

    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            date.fromisoformat(text)
            return text
        except ValueError:
            return ""
    key = text.lower()
    named = {"today", "tomorrow", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
    if key not in named:
        try:
            from app.services.talk.textutil import _spoken_date

            when, rest = _spoken_date(text)
        except Exception:
            return ""
        if when is None or rest.strip():
            return ""
        return when.isoformat()
    try:
        from app.services.talk.textutil import parse_day

        return parse_day(key).isoformat()
    except Exception:
        return ""


def _resolve_named(user, name: str):
    """Imported lazily by the map tools. Defined here so office.py stays free of the database."""
    from app.services.parse import resolve_property

    verdict = resolve_property(name, "", "", user=user)
    if verdict.get("state") != "resolved":
        return None, verdict.get("message") or "Which property?"
    return verdict["property"], ""
