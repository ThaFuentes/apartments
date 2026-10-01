"""Report, mail, people, and record-removal appliers. Split from appliers.py.

Callers go through appliers.apply_tool.
"""
from __future__ import annotations

import re
import secrets
from datetime import timedelta

from app.builddb.builddb import db
from app.models import (
    Equipment,
    Expense,
    Job,
    Notice,
    Property,
    Report,
    ReportShare,
    Trip,
    TripProperty,
    Unit,
    User,
)
from app.services.clock import utcnow
from app.services.people import create_user
from app.services.records import audit, dumps, find_properties, property_place, site_profile

from app.services.appliers_common import (
    _entity,
    _property_match,
    _record_snapshot,
    _src,
    re_words,
)


def apply_query_record(user, payload, source) -> dict:
    from app.services.access import authorize_tool, scoped_property_query, visible_property_ids

    allowed = authorize_tool(user, "query_record", payload or {})
    if not allowed.get("ok"):
        return allowed
    question = (payload.get("question") or "").strip()
    if not question:
        return {"ok": False, "reply": "Ask about a property, a unit, or an expense."}
    low = question.lower()
    props = scoped_property_query(user, Property.query.filter(Property.deleted_at.is_(None)), Property.id).all()
    visible_ids = None if getattr(user, "role", "") in ("owner", "admin", "office") else visible_property_ids(user)
    hit = None
    for prop in props:
        if prop.name.lower() in low:
            hit = prop
            break
    jobs = Job.query.filter(Job.deleted_at.is_(None))
    if visible_ids is not None:
        jobs = jobs.filter(Job.property_id.in_(visible_ids or {-1}))
    if hit:
        jobs = jobs.filter_by(property_id=hit.id)
    rows = jobs.order_by(Job.id.desc()).limit(80).all()
    stop = {"what", "which", "when", "where", "did", "the", "and", "for", "this", "that", "with", "from", "have", "were", "was", "you", "our", "install", "installed"}
    words = [w for w in re_words(low) if w not in stop and len(w) > 2 and (not hit or w != hit.name.lower())]
    matched = []
    for job in rows:
        blob = f"{job.title} {job.detail}".lower()
        if not words or any(w in blob for w in words):
            matched.append(job)
        if len(matched) >= 8:
            break
    if not matched:
        place = f" at {hit.name}" if hit else ""
        return {"ok": True, "reply": f"I don't have a matching job{place}.", "matches": []}
    lines = []
    matches = []
    for job in matched:
        unit = db.session.get(Unit, job.unit_id) if job.unit_id else None
        prop = db.session.get(Property, job.property_id)
        prefix = f"Unit {unit.unit_number}: " if unit else ""
        place = property_place(prop)
        lines.append(f"{prefix}{job.title} ({job.status}) at {place}.")
        matches.append({"job_id": job.id, "href": f"/properties/{job.property_id}"})
    return {"ok": True, "reply": " ".join(lines), "matches": matches}


def apply_draft_report(user, payload, source) -> dict:
    from app.services.reports import build_snapshot, chat_excerpt, render_markdown

    source = _src(source)
    kind = (payload.get("kind") or "weekly").strip().lower()
    if kind in ("boss", "bosses", "company packet"):
        kind = "company"
    if kind not in ("weekly", "company", "property", "adhoc"):
        kind = "weekly"
    profile = site_profile()
    starts = None
    if payload.get("starts_on"):
        from datetime import date

        try:
            starts = date.fromisoformat(str(payload["starts_on"])[:10])
        except ValueError:
            starts = None
    prop_id = payload.get("property_id")
    if payload.get("property_name") and not prop_id:
        found = find_properties(payload["property_name"], payload.get("city") or "")
        if found:
            prop_id = found[0].id
    if not prop_id and getattr(user, "role", "") not in ("owner", "admin"):
        return {"ok": False, "reply": "Scoped report packs are not enabled for this role yet."}
    snapshot = build_snapshot(
        kind=kind,
        starts_on=starts,
        property_id=prop_id,
        author=user.label(),
    )
    from datetime import date

    start = date.fromisoformat(snapshot["period"]["start"])
    end = date.fromisoformat(snapshot["period"]["end"])
    existing = (
        Report.query.filter(
            Report.kind == kind,
            Report.period_start == start,
            Report.period_end == end,
            Report.deleted_at.is_(None),
            Report.status.in_(("ready", "sent")),
        )
        .order_by(Report.id.desc())
        .first()
    )
    if existing and existing.status == "sent" and not payload.get("force"):
        return {
            "ok": True,
            "reply": f"That {kind} report was already sent. It stays as the company copy.",
            "report_id": existing.id,
            "duplicate": True,
        }
    body = render_markdown(snapshot)
    if existing and existing.status == "ready" and not payload.get("force_new"):
        before = {"title": existing.title}
        existing.title = snapshot["title"][:200]
        existing.body_md = body
        existing.snapshot_json = dumps(snapshot)
        existing.property_id = prop_id
        audit(user.id, source, "update", "report", existing.id, before, {"title": existing.title})
        return {
            "ok": True,
            "reply": f"{existing.title}\n\n{chat_excerpt(body)}\n\nBosses with a login can open it. Email is only used when they have one.",
            "report_id": existing.id,
        }
    report = Report(
        kind=kind,
        title=snapshot["title"][:200],
        period_start=start,
        period_end=end,
        property_id=prop_id,
        body_md=body,
        snapshot_json=dumps(snapshot),
        status="ready",
        created_by_id=user.id,
        ready_at=utcnow(),
        created_at=utcnow(),
    )
    db.session.add(report)
    db.session.flush()
    audit(user.id, source, "create", "report", report.id, {}, {"kind": kind, "title": report.title})
    bosses = User.query.filter_by(role="viewer", active=True, can_see_reports=True).count()
    who = f"{bosses} boss login(s) can read it now." if bosses else "Add a boss whenever you want — they do not need an email."
    return {"ok": True, "reply": f"{report.title}\n\n{chat_excerpt(body)}\n\n{who}", "report_id": report.id}


def apply_send_report(user, payload, source) -> dict:
    from app.services.reports import sign_report

    source = _src(source)
    report = db.session.get(Report, int(payload.get("report_id") or 0))
    if not report or report.deleted_at is not None:
        report = (
            Report.query.filter(Report.deleted_at.is_(None), Report.status.in_(("ready", "sent")))
            .order_by(Report.id.desc())
            .first()
        )
    if not report:
        return {"ok": False, "reply": "Save a weekly or company report first."}
    before = {"status": report.status, "sent_at": report.sent_at.isoformat() if report.sent_at else None}
    report.status = "sent"
    report.sent_at = utcnow()
    viewers = User.query.filter_by(role="viewer", active=True, can_see_reports=True).all()
    delivered = []
    for viewer in viewers:
        db.session.add(
            Notice(
                user_id=viewer.id,
                kind="report",
                body=report.title,
                href=f"/reports/{report.id}",
                created_at=utcnow(),
            )
        )
        link = sign_report(report.id, ttl=900)
        mailed = bool(viewer.email) and _email_report(viewer.email, report, link)
        db.session.add(
            ReportShare(
                report_id=report.id,
                user_id=viewer.id,
                label=viewer.label(),
                expires_at=utcnow() + timedelta(minutes=15),
                created_by_id=user.id,
                created_at=utcnow(),
            )
        )
        delivered.append(
            {
                "username": viewer.username,
                "email": viewer.email,
                "mailed": mailed,
                "link": link,
            }
        )
    owner_link = sign_report(report.id, ttl=900)
    extras = []
    for bit in re.split(r"[,;\s]+", str(payload.get("also") or "")):
        bit = bit.strip()
        if "@" in bit and bit not in extras:
            extras.append(bit)
    for address in extras:
        delivered.append(
            {
                "username": address,
                "email": address,
                "mailed": _email_report(address, report, owner_link),
                "link": owner_link,
            }
        )
    audit(user.id, source, "send", "report", report.id, before, {"status": "sent", "viewers": [d["username"] for d in delivered]})
    mail_note = "" if _mail_config()[0] else " Mail is not saved in Settings yet, so nothing was emailed."
    if not delivered:
        return {
            "ok": True,
            "reply": f"Marked {report.title} sent. No bosses are on the account yet. Download the PDF from the report page.{mail_note} This short link works for 15 minutes: {owner_link}",
            "report_id": report.id,
            "link": owner_link,
            "delivered": [],
        }
    bits = []
    for row in delivered:
        if row["email"] and row["mailed"]:
            bits.append(f"{row['username']} emailed")
        elif row["email"]:
            bits.append(f"{row['username']} has email but send failed — they can still open it when they log in")
        else:
            bits.append(f"{row['username']} has no email — it is on their login")
    return {
        "ok": True,
        "reply": f"Sent {report.title}. " + "; ".join(bits) + f".{mail_note} Short link: {owner_link}",
        "report_id": report.id,
        "link": owner_link,
        "delivered": delivered,
    }


def _mail_config() -> tuple[str, int, str, str, str]:
    import os

    profile = site_profile()
    host = ((getattr(profile, "smtp_host", None) or "") or os.getenv("SMTP_HOST") or "").strip()
    try:
        port = int(getattr(profile, "smtp_port", None) or os.getenv("SMTP_PORT") or 587)
    except (TypeError, ValueError):
        port = 587
    user_name = ((getattr(profile, "smtp_user", None) or "") or os.getenv("SMTP_USER") or "").strip()
    sender = ((getattr(profile, "smtp_from", None) or "") or os.getenv("SMTP_FROM") or user_name or "apt@poweredby.top").strip()
    password = ""
    cipher = getattr(profile, "smtp_password_ciphertext", None) if profile else None
    if cipher:
        from app.services.crypto import decrypt_text

        try:
            password = decrypt_text(cipher)
        except Exception:
            password = ""
    if not password:
        password = os.getenv("SMTP_PASSWORD") or ""
    return host, port, user_name, password, sender


def _email_report(address: str, report: Report, link: str) -> bool:
    import smtplib
    from email.message import EmailMessage

    host, port, user_name, password, sender = _mail_config()
    if not host or not address:
        return False
    from app.services.reports import load_snapshot, render_markdown, render_pdf

    snapshot = load_snapshot(report)
    msg = EmailMessage()
    msg["Subject"] = report.title
    msg["From"] = sender
    msg["To"] = address
    msg.set_content(render_markdown(snapshot) + f"\nShort link (15 minutes): {link}\n")
    try:
        pdf = render_pdf(snapshot)
        msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=f"apt-report-{report.id}.pdf")
    except Exception:
        pass
    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.starttls()
            if user_name:
                smtp.login(user_name, password)
            smtp.send_message(msg)
        return True
    except Exception:
        return False


def apply_invite_viewer(user, payload, source) -> dict:
    source = _src(source)
    from app.services.access import can_create_user
    from app.services.changes import normalize_role

    role = normalize_role(payload.get("role") or "") or (payload.get("role") or "viewer").strip().lower()
    if not can_create_user(user, role):
        return {"ok": False, "reply": "This login cannot add that role or scope."}
    username = (payload.get("username") or "").strip()
    if not username:
        from app.services.people import suggest_username

        username = suggest_username(payload.get("display_name") or "staff")
    try:
        created, generated = create_user(
            username=username,
            password=payload.get("password") or "",
            display_name=payload.get("display_name") or username,
            role=role,
            email=payload.get("email") or None,
            phone=payload.get("phone") or "",
            created_by=user,
            can_see_reports=payload.get("can_see_reports", True),
            can_see_history=payload.get("can_see_history", role != "viewer" or payload.get("can_see_history", True)),
            can_see_live_map=bool(payload.get("can_see_live_map", False)),
            is_bot=bool(payload.get("is_bot")),
            security_email=payload.get("security_email") or None,
            reset_email=payload.get("reset_email") or None,
        )
    except ValueError as exc:
        return {"ok": False, "reply": str(exc)}
    token = secrets.token_urlsafe(24)
    created.invite_token = token
    created.invite_expires = utcnow() + timedelta(days=7)
    created.invite_used = False
    audit(
        user.id,
        source,
        "create",
        "user",
        created.id,
        {},
        {"username": created.username, "role": created.role, "email": created.email, "phone": created.phone, "display_name": created.display_name},
    )
    from app.services.providers import ROLE_LABELS as ROLE_WORD

    reach = ", ".join(bit for bit in (created.phone, created.email or "") if bit) or "no phone or email"
    secret = f" Temporary password: {generated}." if generated else ""
    bot_note = ""
    if created.is_bot:
        bot_note = " Marked as a bot: first sign-in must turn on 2FA, and the reset inbox should be a different email from login and 2FA."
    granted = []
    for grant in payload.get("_grants") or []:
        if not isinstance(grant, dict):
            continue
        result = apply_grant_access(user, {**grant, "username": created.username}, source)
        if result.get("reply"):
            granted.append(result["reply"])
    return {
        "ok": True,
        "reply": " ".join(
            bit
            for bit in (
                (
                    f"Added {created.display_name or created.username} as {ROLE_WORD.get(created.role, created.role)} ({reach}). "
                    f"They sign in with that username.{secret}{bot_note} "
                    f"One-time link, 7 days: /join/{token}"
                ),
                *granted,
            )
            if bit
        ),
        "user_id": created.id,
        "username": created.username,
        "generated_password": generated,
    }


def apply_grant_access(user, payload, source) -> dict:
    """Change what one person can do at one property."""
    source = _src(source)
    from app.models import PropertyAccess
    from app.services.access import can_manage_property_people, set_access
    from app.services.people import find_person
    from app.services.parse import resolve_property

    person = find_person(payload.get("username") or "")
    if not person:
        return {"ok": False, "reply": "I can't find that login."}
    prop = None
    raw_id = payload.get("property_id")
    if raw_id:
        from app.models import Property

        try:
            prop = db.session.get(Property, int(raw_id))
        except (TypeError, ValueError):
            prop = None
        if prop and prop.deleted_at:
            prop = None
    if prop is None:
        verdict = resolve_property(payload.get("property_name") or "", payload.get("city") or "", user=user)
        if verdict.get("state") != "resolved":
            return {"ok": False, "reply": verdict.get("message") or "Which property is this for?"}
        prop = verdict["property"]
    if not can_manage_property_people(user, prop.id):
        return {"ok": False, "reply": "You do not manage people at that property."}
    row = PropertyAccess.query.filter_by(user_id=person.id, property_id=prop.id).first()
    before = {
        "see": bool(row),
        "edit": bool(row.can_edit) if row else False,
        "notify": bool(row.notify) if row else False,
        "manage_people": bool(row.can_manage_people) if row else False,
    }
    see = True if payload.get("see") is None else bool(payload.get("see"))
    edit = None if payload.get("edit") is None else bool(payload.get("edit"))
    notify = None if payload.get("notify") is None else bool(payload.get("notify"))
    manage = None if payload.get("manage_people") is None else bool(payload.get("manage_people"))
    reply = set_access(user, person, prop, see=see, edit=edit, notify=notify, manage_people=manage)
    if not see or reply.startswith("You do not"):
        return {"ok": False, "reply": reply}
    after = {
        "see": True,
        "edit": bool(edit) if edit is not None else before["edit"],
        "notify": bool(notify) if notify is not None else before["notify"],
        "manage_people": bool(manage) if manage is not None else before["manage_people"],
    }
    audit(user.id, source, "update", "user", person.id, before, after)
    return {
        "ok": True,
        "reply": reply + " They'll see it when they log in again.",
        "user_id": person.id,
        "property_id": prop.id,
    }


def apply_update_viewer(user, payload, source) -> dict:
    source = _src(source)
    from app.services.changes import normalize_role
    from app.services.people import find_person

    target = find_person(payload.get("username") or "")
    if not target:
        return {"ok": False, "reply": "I can't find that login."}
    from app.services.access import can_create_user, can_manage_user, role_of

    if not can_manage_user(user, target) and not (role_of(user) == "owner" and target.id == user.id):
        return {"ok": False, "reply": "This login cannot manage that person."}
    wanted_role = normalize_role(payload.get("role") or "") if payload.get("role") else ""
    if payload.get("role") and not wanted_role:
        return {"ok": False, "reply": "I don't know that role. Say owner, admin, regional manager, regional property manager, property manager, assistant manager, office, maintenance supervisor, or maintenance person."}
    if wanted_role and target.role == "owner":
        wanted_role = ""
    if wanted_role and not can_create_user(user, wanted_role):
        return {"ok": False, "reply": "This login cannot assign that role."}
    before = {
        "role": target.role,
        "email": target.email,
        "phone": target.phone,
        "display_name": target.display_name,
        "can_see_reports": target.can_see_reports,
        "can_see_history": target.can_see_history,
        "can_see_live_map": target.can_see_live_map,
        "active": target.active,
    }
    if wanted_role:
        target.role = wanted_role
    if "is_bot" in payload and payload["is_bot"] is not None:
        make = bool(payload.get("is_bot"))
        if make and target.role == "owner":
            return {"ok": False, "reply": "The owner login cannot be a bot."}
        if not make and target.is_bot and role_of(user) != "owner":
            return {"ok": False, "reply": "Only an owner can unmark a bot. That clears 2FA."}
        if not make and target.is_bot:
            from app.services.twofa import turn_off

            turn_off(target)
        target.is_bot = make
    if "security_email" in payload:
        from app.services.people import clean_email

        try:
            target.security_email = clean_email(payload.get("security_email"))
        except ValueError as exc:
            return {"ok": False, "reply": str(exc)}
    if "reset_email" in payload:
        from app.services.people import clean_email

        try:
            target.reset_email = clean_email(payload.get("reset_email"))
        except ValueError as exc:
            return {"ok": False, "reply": str(exc)}
    if payload.get("clear_email"):
        first = User.query.filter_by(role="owner").order_by(User.id.asc()).first()
        if first and first.id == target.id:
            return {"ok": False, "reply": "The first login has to keep an email."}
        target.email = None
    elif "email" in payload:
        from app.services.people import clean_email

        try:
            target.email = clean_email(payload.get("email"))
        except ValueError as exc:
            return {"ok": False, "reply": str(exc)}
    for flag in ("can_see_reports", "can_see_history", "can_see_live_map", "active"):
        if flag in payload and payload[flag] is not None:
            setattr(target, flag, bool(payload[flag]))
    if payload.get("display_name"):
        target.display_name = str(payload["display_name"])[:150]
    if payload.get("phone") is not None:
        from app.services.people import clean_phone

        try:
            target.phone = clean_phone(payload.get("phone"))
        except ValueError as exc:
            return {"ok": False, "reply": str(exc)}
    audit(user.id, source, "update", "user", target.id, before, {"role": target.role, "email": target.email, "phone": target.phone, "display_name": target.display_name, "active": target.active})
    reach = ", ".join(bit for bit in (target.phone, target.email or "") if bit) or "no phone or email"
    return {
        "ok": True,
        "reply": f"{target.label()} is {target.role}, {reach}. They'll see it when they log in again.",
        "user_id": target.id,
    }


def apply_soft_delete(user, payload, source) -> dict:
    source = _src(source)
    name = (payload.get("entity") or "").strip().lower()
    row = _entity(name, int(payload.get("entity_id") or 0))
    if not row or getattr(row, "deleted_at", None):
        return {"ok": False, "reply": "Nothing to remove."}
    removed_at = utcnow()
    if name == "unit":
        from app.models import Equipment, UnitTask

        for model in (Job, UnitTask, Equipment):
            for child in model.query.filter_by(unit_id=row.id).filter(model.deleted_at.is_(None)).all():
                child_name = {Job: "job", UnitTask: "unit_task", Equipment: "equipment"}[model]
                before_child = _record_snapshot(child_name, child)
                child.deleted_at = removed_at
                audit(user.id, source, "delete", child_name, child.id, before_child, _record_snapshot(child_name, child))
    before = _record_snapshot(name, row)
    row.deleted_at = removed_at
    after = _record_snapshot(name, row)
    audit(user.id, source, "delete", name, row.id, before, after)
    return {"ok": True, "reply": f"Removed {name.replace('_', ' ')} {row.id}. Say ‘restore {name.replace('_', ' ')} {row.id}’ if that was a mistake.", "entity": name, "entity_id": row.id, "unit_id": getattr(row, "unit_id", None)}


def apply_restore(user, payload, source) -> dict:
    source = _src(source)
    name = (payload.get("entity") or "").strip().lower()
    row = _entity(name, int(payload.get("entity_id") or 0))
    if row is None or not getattr(row, "deleted_at", None):
        return {"ok": False, "reply": "That record is not in the restore bin."}
    restore_group = row.deleted_at
    before = _record_snapshot(name, row)
    row.deleted_at = None
    after = _record_snapshot(name, row)
    audit(user.id, source, "restore", name, row.id, before, after)
    if name == "unit":
        from app.models import Equipment, UnitTask

        for model in (Job, UnitTask, Equipment):
            for child in model.query.filter_by(unit_id=row.id, deleted_at=restore_group).all():
                child_name = {Job: "job", UnitTask: "unit_task", Equipment: "equipment"}[model]
                before_child = _record_snapshot(child_name, child)
                child.deleted_at = None
                audit(user.id, source, "restore", child_name, child.id, before_child, _record_snapshot(child_name, child))
    return {"ok": True, "reply": f"Restored {name.replace('_', ' ')} {row.id} and its removed unit records.", "entity": name, "entity_id": row.id, "unit_id": getattr(row, "unit_id", None)}
