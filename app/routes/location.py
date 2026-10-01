"""Map, sharing, offline property pack, and photo routes."""
from __future__ import annotations

from flask import abort, flash, jsonify, redirect, render_template, request
from flask_login import current_user

from app.builddb.builddb import db
from app.models import Job, Media, PendingAction, Property, Unit
from app.services.clock import utcnow
from app.services.files import read_blob, save_blob, send_bytes
from app.services.records import loads, site_profile
from app.services.talk import handle_message
from app.routes.common import bp, login_required, _history_ok, _key, _new_key


@bp.get("/map")
@login_required
def map_page():
    if current_user.is_viewer and not current_user.can_see_live_map and not current_user.can_see_history:
        abort(403)
    props = Property.query.filter(Property.deleted_at.is_(None), Property.lat.isnot(None)).all()
    pins = [
        {
            "name": p.name,
            "city": p.city.name if p.city else "",
            "lat": p.lat,
            "lng": p.lng,
            "href": f"/properties/{p.id}",
        }
        for p in props
    ]
    profile = site_profile()
    home = None
    if profile and profile.home_lat is not None:
        home = {"lat": profile.home_lat, "lng": profile.home_lng, "label": profile.home_label or "Home"}
    from app.services.share import share_state

    return render_template("map.html", pins=pins, home=home, state=share_state(current_user))


@bp.post("/share")
@login_required
def share_toggle():
    if current_user.is_viewer:
        abort(403)
    from app.services.share import start_share, stop_share
    from app.services.records import open_shift

    shift = open_shift(current_user)
    if request.form.get("on") == "1":
        result = start_share(current_user)
    else:
        if shift and shift.sharing_on:
            stop_share(shift, "manual")
            db.session.commit()
        result = {"ok": True, "reply": "Sharing is off."}
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect("/map")


@bp.post("/api/ping")
@login_required
def ping():
    if current_user.is_viewer:
        abort(403)
    from app.services.share import add_ping

    data = request.get_json(silent=True) or request.form
    try:
        lat = float(data.get("lat"))
        lng = float(data.get("lng"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Need a latitude and longitude."}), 400
    result = add_ping(
        current_user,
        lat,
        lng,
        offline_queue=request.headers.get("X-Apt-Offline-Queue") == "1",
    )
    code = 200 if result.get("ok") else 409
    return jsonify(result), code


@bp.get("/api/pack/<int:property_id>")
@login_required
def pack(property_id):
    if not _history_ok():
        abort(403)
    prop = db.session.get(Property, property_id)
    if not prop or prop.deleted_at:
        abort(404)
    units = Unit.query.filter_by(property_id=prop.id).filter(Unit.deleted_at.is_(None)).all()
    jobs = Job.query.filter_by(property_id=prop.id).filter(Job.deleted_at.is_(None)).all()
    return jsonify(
        {
            "property": {"id": prop.id, "name": prop.name, "city": prop.city.name if prop.city else ""},
            "units": [{"id": u.id, "number": u.unit_number} for u in units],
            "jobs": [
                {
                    "id": j.id,
                    "unit_id": j.unit_id,
                    "title": j.title,
                    "detail": j.detail,
                    "status": j.status,
                }
                for j in jobs
            ],
        }
    )


@bp.post("/media")
@login_required
def upload():
    if current_user.is_viewer:
        abort(403)
    blob = request.files.get("photo")
    if not blob:
        flash("Choose a photo.", "warn")
        return redirect("/")
    raw = blob.read()
    if not raw:
        flash("That photo was empty.", "warn")
        return redirect("/")
    name = save_blob(raw)
    media = Media(
        user_id=current_user.id,
        kind=request.form.get("kind") or "photo",
        storage_name=name,
        mime=blob.mimetype or "image/jpeg",
        caption=(request.form.get("message") or "")[:300],
        created_at=utcnow(),
    )
    db.session.add(media)
    db.session.commit()
    note = (request.form.get("message") or "").strip()
    from app.services.equipment import describe, merge_equipment, parse_equipment, read_photo
    from app.services.records import dumps

    seen = read_photo(current_user, raw, media.mime)
    merged = merge_equipment(parse_equipment(note), seen)
    media.parse_json = dumps(merged)
    media.confidence = merged.get("confidence")
    if merged.get("serial") or merged.get("model"):
        media.kind = "nameplate"
    db.session.commit()
    quota = seen.get("reply") if seen.get("quota") else ""
    if note:
        key = _key() or _new_key()
        result = handle_message(current_user, note, idempotency_key=key, source="ai")
        pending = PendingAction.query.filter_by(user_id=current_user.id, idempotency_key=key[:120]).first()
        if pending and pending.status in ("pending", "needs_answer"):
            payload = loads(pending.payload_json)
            payload["media_id"] = media.id
            if pending.tool == "record_unit_visit":
                payload["equipment"] = merge_equipment(payload.get("equipment") or {}, merged)
                label = describe(payload["equipment"])
                if label:
                    pending.summary = f"Log unit {payload.get('unit_number') or '?'} — {label}. Not saved yet."
            pending.payload_json = dumps(payload)
            db.session.commit()
        flash(" ".join(bit for bit in (quota, result.get("reply")) if bit) or "Photo kept.", "ok")
    else:
        result = _photo_proposal(current_user, media, merged, quota)
        flash(result.get("reply") or "Photo kept.", "ok" if result.get("ok", True) else "warn")
    return redirect("/")


def _photo_proposal(user, media, merged, quota: str) -> dict:
    from app.services.equipment import describe, plate_ready
    from app.services.pending import propose
    from app.services.records import open_shift

    label = describe(merged)
    shift = open_shift(user)
    number = ""
    if not label and not merged.get("kind"):
        extra = "Add a Gemini key in Settings and I'll read the nameplate, or type the brand, model, and serial."
        if quota:
            extra = quota
        return {"ok": True, "reply": f"Photo kept. {extra}"}
    summary = f"Photo reads {label or merged.get('kind') or 'a label'}."
    needs = False
    if not plate_ready(merged, from_photo=True):
        needs = True
        missing = ", ".join(merged.get("missing") or ["the serial"])
        if merged.get("conflict"):
            summary = merged["conflict"]
        else:
            summary = f"{summary} I still need {missing} before this is filed."
    summary += " Which unit number?" if not number else ""
    if not number:
        needs = True
    payload = {
        "unit_number": number,
        "title": label or "Nameplate",
        "status": "done",
        "note": label,
        "equipment": merged,
        "media_id": media.id,
        "needs_answer": needs,
    }
    if shift and shift.confirmed and not needs:
        pass
    return propose(
        user,
        "record_unit_visit",
        payload,
        (quota + " " if quota else "") + summary + " Not saved yet.",
        "material",
        f"photo-{media.id}",
        f"photo-{media.id}",
        "ai",
    )


@bp.get("/media/<int:media_id>")
@login_required
def media_file(media_id):
    if current_user.is_viewer:
        abort(403)
    media = db.session.get(Media, media_id)
    if not media:
        abort(404)
    data = read_blob(media.storage_name)
    if not data:
        abort(404)
    return send_bytes(data, media.mime or "image/jpeg")


from app.routes import auth as _auth_routes
from app.routes import property_units as _property_unit_routes
from app.routes import admin as _admin_routes
