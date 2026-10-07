"""Planning, chat, mileage, and trip routes."""
from __future__ import annotations

from flask import abort, flash, jsonify, redirect, render_template, request
from flask_login import current_user

from app.builddb.builddb import db
from app.models import Job, Property, Trip, TripProperty
from app.services.pending import batch_confirm, confirm_id, confirm_property, discard_id, update_pending
from app.services.talk import clear_chat, handle_message, handle_photo
from app.routes.common import bp, login_required, _history_ok, _key, _new_key


def _back_to(default: str = "/") -> str:
    """Only ever return to a same-site relative path, never a posted absolute URL."""
    from app.auth import sanitize_next

    target = request.form.get("next") or ""
    if not (target or "").strip():
        target = request.referrer or ""
    return sanitize_next(target, default)


def _drive_cookie(resp, result):
    """Driving view is a cookie, the same one the page form sets."""
    drive = (result or {}).get("drive")
    if drive not in {"on", "off"}:
        return resp
    secure = bool(request.is_secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https")
    if drive == "on":
        resp.set_cookie("apt_drive", "1", max_age=60 * 60 * 12, samesite="Lax", httponly=True, secure=secure, path="/")
    else:
        resp.set_cookie("apt_drive", "", expires=0, samesite="Lax", httponly=True, secure=secure, path="/")
    return resp


def _theme_cookie(resp, result):
    """A chat theme sticks on this browser the same way the More page does."""
    theme_id = ((result or {}).get("theme") or "").strip()
    if not theme_id:
        return resp
    from app.services.themes import theme_by_id

    found = theme_by_id(theme_id)
    if not found:
        return resp
    secure = bool(request.is_secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https")
    resp.set_cookie(
        "apt_theme",
        found["id"],
        max_age=60 * 60 * 24 * 400,
        samesite="Lax",
        httponly=True,
        secure=secure,
        path="/",
    )
    return resp


def _layout_cookie(resp, result):
    """A chat layout sticks on this browser the same way the Arrange menu does."""
    layout_id = ((result or {}).get("layout") or "").strip()
    if not layout_id:
        return resp
    from app.services.themes import layout_by_id

    found = layout_by_id(layout_id)
    if not found:
        return resp
    secure = bool(request.is_secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https")
    resp.set_cookie(
        "apt_layout",
        found["id"],
        max_age=60 * 60 * 24 * 400,
        samesite="Lax",
        httponly=True,
        secure=secure,
        path="/",
    )
    return resp


def _chat_cookies(resp, result):
    return _layout_cookie(_theme_cookie(_drive_cookie(resp, result), result), result)


@bp.route("/")
@login_required
def home():
    if current_user.is_viewer:
        return redirect("/reports")
    from app.services.browse import home_board
    from app.services.context import property_picker

    properties, default_property_id = property_picker(current_user)
    return render_template(
        "home.html",
        board=home_board(current_user.id, current_user),
        properties=properties,
        default_property_id=default_property_id,
        msg_key=_new_key(),
    )


@bp.post("/sites")
@login_required
def add_site():
    if current_user.is_viewer:
        abort(403)
    from app.services.pending import commit_apply

    name = (request.form.get("name") or "").strip()
    city = (request.form.get("city") or "").strip()
    if not name or not city:
        flash("Need a property name and a city.", "warn")
        return redirect("/")
    result = commit_apply(
        current_user,
        "upsert_property",
        {"property_name": name, "city": city, "region": (request.form.get("region") or "").strip()},
        "human",
        _key() or _new_key(),
    )
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect("/")

@bp.get("/api/places")
@login_required
def place_search():
    if current_user.is_viewer and not current_user.can_see_history:
        abort(403)
    q = (request.args.get("q") or "").strip()[:80]
    if len(q) < 2:
        return jsonify([])
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    from app.services.access import scoped_property_query

    rows = (
        scoped_property_query(current_user, Property.query.filter(Property.deleted_at.is_(None)))
        .filter(Property.name.ilike(f"%{escaped}%", escape="\\"))
        .order_by(Property.name.asc())
        .limit(8)
        .all()
    )
    return jsonify(
        [{"id": row.id, "name": row.name, "city": row.city.name if row.city else ""} for row in rows]
    )

@bp.route("/plan", methods=["GET", "POST"])
@login_required
def plan_day():
    if current_user.is_viewer:
        abort(403)
    from app.services.clock import local_today
    from app.services.pending import commit_apply
    from app.services.records import site_profile as profile_for
    from app.services.access import can_edit_property, require_edit

    profile = profile_for()
    today = local_today(profile.timezone if profile else None).isoformat()
    from app.services.context import property_picker

    properties, default_property_id = property_picker(current_user)
    if request.method == "POST":
        from app.services.plan import work_cards

        stops = []
        ids = request.form.getlist("property_id")
        works = request.form.getlist("work")
        for raw_id in ids:
            try:
                selected_property = db.session.get(Property, int(raw_id))
            except (TypeError, ValueError):
                selected_property = None
            if not selected_property or selected_property.deleted_at:
                flash("Choose an assigned property for the plan.", "warn")
                return redirect("/plan")
            require_edit(current_user, selected_property)
        typed = []
        for unit, title in zip(request.form.getlist("card_unit"), request.form.getlist("card_work")):
            unit = (unit or "").strip()
            title = (title or "").strip()
            if unit or title:
                typed.append({"title": (title or "Work")[:200], "detail": "", "planned_qty": 1, "unit_number": unit[:40]})
        first = True
        for prop_id, work in zip(ids, works):
            try:
                prop = db.session.get(Property, int(prop_id))
            except (TypeError, ValueError):
                continue
            if not prop or prop.deleted_at or not can_edit_property(current_user, prop.id):
                continue
            items = work_cards(work or "")
            if first:
                items = typed + items
                first = False
            if not items:
                continue
            stops.append(
                {
                    "property_name": prop.name,
                    "city": prop.city.name if prop.city else "",
                    "region": prop.city.region if prop.city else "",
                    "items": items,
                }
            )
        new_name = (request.form.get("new_name") or "").strip()
        new_city = (request.form.get("new_city") or "").strip()
        if new_name and new_city:
            items = work_cards(request.form.get("new_work") or "")
            if items:
                stops.append({"property_name": new_name, "city": new_city, "region": "", "items": items})
        if not stops:
            flash("Add a work card for each job. Say the unit on that card.", "warn")
            return render_template("plan.html", today=today, properties=properties, default_property_id=default_property_id, msg_key=_new_key())
        payload = {"starts_on": request.form.get("day") or today, "stops": stops}
        if (request.form.get("odometer_start") or "").strip():
            payload["odometer_start"] = request.form.get("odometer_start")
        if (request.form.get("odometer_end") or "").strip():
            payload["odometer_end"] = request.form.get("odometer_end")
        result = commit_apply(
            current_user,
            "plan_day",
            payload,
            "human",
            _key() or _new_key(),
        )
        flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
        if result.get("trip_id"):
            return redirect(f"/trips/{result['trip_id']}")
        return redirect("/trips")
    return render_template("plan.html", today=today, properties=properties, default_property_id=default_property_id, msg_key=_new_key())

@bp.post("/log")
@login_required
def log_work():
    if current_user.is_viewer:
        abort(403)
    from app.services.pending import commit_apply

    raw_property_id = (request.form.get("property_id") or "").strip()
    try:
        property_id = int(raw_property_id)
    except (TypeError, ValueError):
        flash("Choose a property for this work.", "warn")
        return redirect("/")

    from app.services.access import require_edit
    from app.services.files import save_blob

    prop = db.session.get(Property, property_id)
    require_edit(current_user, prop)

    blob = request.files.get("photo")
    raw = blob.read() if blob and blob.filename else b""
    if blob and blob.filename and (not raw or len(raw) > 12 * 1024 * 1024):
        flash("Choose a photo smaller than 12 MB.", "warn")
        return redirect("/")
    mime = ""
    if raw:
        try:
            from PIL import Image

            image = Image.open(__import__("io").BytesIO(raw))
            image.verify()
            mime = Image.MIME.get(image.format, "")
        except Exception:
            flash("Choose a valid photo image.", "warn")
            return redirect("/")

    payload = {
        "property_id": prop.id,
        "property_name": prop.name,
        "city": prop.city.name if prop.city else "",
        "region": prop.city.region if prop.city else "",
        "unit_number": request.form.get("unit_number") or "",
        "title": request.form.get("title") or "",
        "status": "done",
    }
    result = commit_apply(current_user, "log_work", payload, "human", _key() or _new_key())
    if raw and result.get("ok"):
        from app.models import Media
        from app.services.clock import utcnow

        result_job_id = result.get("job_id")
        result_unit_id = result.get("unit_id")
        if not result_job_id and not result_unit_id:
            flash("Work was logged, but the photo could not be attached because no work record was returned.", "warn")
        else:
            media = Media(
                user_id=current_user.id,
                kind="photo",
                storage_name=save_blob(raw),
                mime=mime,
                caption=(request.form.get("title") or "")[:300],
                property_id=prop.id,
                job_id=result_job_id,
                unit_id=result_unit_id,
                created_at=utcnow(),
            )
            db.session.add(media)
            db.session.commit()
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect("/")

@bp.post("/chat")
@login_required
def chat():
    if current_user.is_viewer:
        abort(403)
    if request.headers.get("X-Apt-Offline-Queue") == "1":
        return jsonify({"ok": False, "error": "Send this when you are online so it is not filed twice."}), 409
    if request.is_json:
        text = ((request.get_json(silent=True) or {}).get("message") or "").strip()
    else:
        text = (request.form.get("message") or "").strip()
    blob = request.files.get("photo") if request.files else None
    raw = b""
    if blob and blob.filename:
        raw = blob.read()
    key = _key() or _new_key()
    if raw:
        result = handle_photo(
            current_user,
            text,
            raw,
            blob.mimetype or "image/jpeg",
            idempotency_key=key,
            source="ai",
        )
    else:
        result = handle_message(current_user, text, idempotency_key=key, source="ai")
    # If the help system wants to open the help page, redirect there
    if result.get("help_page_url"):
        if request.is_json or request.headers.get("Accept") == "application/json":
            return _chat_cookies(jsonify(result), result)
        flash(result.get("reply") or "", "ok")
        return _chat_cookies(redirect(result["help_page_url"]), result)
    if request.is_json or request.headers.get("Accept") == "application/json":
        return _chat_cookies(jsonify(result), result)
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return _chat_cookies(redirect("/"), result)

@bp.post("/chat/new")
@login_required
def chat_new():
    if current_user.is_viewer:
        abort(403)
    result = clear_chat(current_user)
    if request.headers.get("Accept") == "application/json":
        return jsonify(result)
    return redirect("/")

@bp.post("/review")
@login_required
def review():
    if current_user.is_viewer:
        abort(403)
    ids = request.form.getlist("id")
    accept_all = request.form.get("accept_all") == "1"
    result = batch_confirm(current_user, ids, accept_all=accept_all, source="human")
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(_back_to("/"))

def _pending_response(result, *, ok_flash=True):
    if request.is_json or request.headers.get("Accept") == "application/json":
        return jsonify(result)
    flash(result.get("reply") or "", "ok" if (result.get("ok") if ok_flash else True) else "warn")
    return redirect(_back_to("/"))


@bp.post("/pending/<int:pending_id>/confirm")
@login_required
def confirm(pending_id):
    if current_user.is_viewer:
        abort(403)
    return _pending_response(confirm_id(current_user, pending_id, "human"))

@bp.post("/pending/<int:pending_id>/discard")
@login_required
def discard(pending_id):
    if current_user.is_viewer:
        abort(403)
    return _pending_response(discard_id(current_user, pending_id), ok_flash=False)

@bp.post("/pending/<int:pending_id>/edit")
@login_required
def edit_pending(pending_id):
    if current_user.is_viewer:
        abort(403)
    changes = {
        "property_name": request.form.get("property_name") or None,
        "city": request.form.get("city") or None,
        "starts_on": request.form.get("starts_on") or None,
        "purpose": request.form.get("purpose") or None,
        "unit_number": request.form.get("unit_number") or None,
        "title": request.form.get("title") or None,
        "kind": request.form.get("kind") or None,
        "merchant": request.form.get("merchant") or None,
        "note": request.form.get("note") or None,
    }
    if request.form.get("amount"):
        try:
            changes["amount_cents"] = int(round(float(request.form.get("amount")) * 100))
        except ValueError:
            flash("Amount should look like 42.18", "warn")
            return redirect("/")
    if request.form.get("odometer"):
        changes["odometer"] = int(request.form.get("odometer"))
    if request.form.get("miles"):
        changes["miles"] = float(request.form.get("miles"))
    result = update_pending(current_user, pending_id, changes)
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(_back_to("/"))

@bp.post("/property/default")
@login_required
def property_default():
    if current_user.is_viewer:
        abort(403)
    from app.services.context import apply_set_default

    result = apply_set_default(
        current_user,
        {"property_id": request.form.get("property_id")},
        "human",
    )
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(_back_to("/"))


@bp.post("/property/confirm")
@login_required
def property_confirm():
    if current_user.is_viewer:
        abort(403)
    result = confirm_property(current_user, "human")
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(_back_to("/"))

@bp.get("/miles")
@login_required
def miles_page():
    if current_user.is_viewer:
        abort(403)
    from app.models import MilesEntry, OdometerReading
    from app.services.miles import traveled_rows, traveled_total

    readings = (
        OdometerReading.query.filter_by(user_id=current_user.id)
        .order_by(OdometerReading.recorded_at.desc())
        .limit(40)
        .all()
    )
    _all, counted = traveled_rows(current_user.id)
    recent = (
        MilesEntry.query.filter_by(user_id=current_user.id)
        .order_by(MilesEntry.recorded_at.desc())
        .limit(40)
        .all()
    )
    return render_template(
        "miles.html",
        total=traveled_total(current_user.id),
        readings=readings,
        entries=recent,
        counted_ids={row.id for row in counted},
        msg_key=_new_key(),
    )

@bp.post("/miles")
@login_required
def miles_save():
    if current_user.is_viewer:
        abort(403)
    from app.services.pending import commit_apply

    if request.form.get("reading"):
        result = commit_apply(
            current_user,
            "log_odometer",
            {"reading": int(request.form.get("reading"))},
            "human",
            _key() or _new_key(),
        )
    else:
        result = commit_apply(
            current_user,
            "log_miles",
            {"miles": float(request.form.get("miles") or 0), "note": request.form.get("note") or "Miles she logged"},
            "human",
            _key() or _new_key(),
        )
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect("/miles")

@bp.get("/more")
@login_required
def more():
    return render_template("more.html")


@bp.get("/help")
@login_required
def help_page():
    """Role-tiered help page: each role sees their own help plus everything below them."""
    from app.services.caps.help_content import role_help_topics, role_intro, role_summary, role_help_text
    from app.services.access import role_of

    role = role_of(current_user)
    topics = role_help_topics(role, user=current_user)
    return render_template(
        "help.html",
        role=role,
        role_display=role.replace("_", " ").title() if role else "Unknown",
        intro=role_intro(role),
        summary=role_summary(role, user=current_user),
        topics=topics,
        help_text=role_help_text(role),
        msg_key=_new_key(),
    )

@bp.get("/trips")
@login_required
def trips():
    if not _history_ok():
        abort(403)
    from app.models import PlanItem

    rows = Trip.query.filter(Trip.deleted_at.is_(None)).order_by(Trip.starts_on.desc(), Trip.id.desc()).all()
    followups = Job.query.filter(Job.deleted_at.is_(None), Job.status.in_(("followup", "blocked"))).order_by(Job.id.desc()).all()
    open_plan = (
        PlanItem.query.filter(PlanItem.deleted_at.is_(None), PlanItem.status.in_(("open", "partial")))
        .order_by(PlanItem.id.asc())
        .all()
    )
    return render_template("trips.html", trips=rows, followups=followups, open_plan=open_plan)

@bp.get("/trips/<int:trip_id>")
@login_required
def trip_detail(trip_id):
    if not _history_ok():
        abort(403)
    trip = db.session.get(Trip, trip_id)
    if not trip or trip.deleted_at:
        abort(404)
    from app.models import PlanItem
    from app.services.plan import status_line

    links = TripProperty.query.filter_by(trip_id=trip.id).order_by(TripProperty.sort_order.asc()).all()
    items = (
        PlanItem.query.filter_by(trip_id=trip.id)
        .filter(PlanItem.deleted_at.is_(None))
        .order_by(PlanItem.sort_order.asc(), PlanItem.id.asc())
        .all()
    )
    return render_template("trip.html", trip=trip, links=links, items=items, status_line=status_line)

@bp.post("/plan-items/<int:item_id>")
@login_required
def plan_mark(item_id):
    if current_user.is_viewer:
        abort(403)
    from app.models import PlanItem
    from app.services.pending import commit_apply

    item = db.session.get(PlanItem, item_id)
    if not item or item.deleted_at:
        abort(404)
    status = request.form.get("status") or "done"
    payload = {
        "item_id": item.id,
        "status": status,
        "note": request.form.get("note") or "",
        "closed_by": request.form.get("closed_by") or "",
    }
    if request.form.get("done_qty"):
        payload["done_qty"] = int(request.form.get("done_qty"))
        payload["status"] = "partial"
    result = commit_apply(
        current_user,
        "plan_outcome",
        payload,
        "human",
        _key() or f"plan-{item_id}-{status}-{_new_key()}",
    )
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(f"/trips/{item.trip_id}")

@bp.post("/trips/<int:trip_id>/miles")
@login_required
def trip_miles(trip_id):
    if current_user.is_viewer:
        abort(403)
    from app.services.pending import commit_apply

    payload = {"trip_id": trip_id}
    if request.form.get("miles_estimate"):
        payload["miles_estimate"] = float(request.form.get("miles_estimate"))
    if request.form.get("miles_actual"):
        payload["miles_actual"] = float(request.form.get("miles_actual"))
    if (request.form.get("odometer_start") or "").strip():
        payload["odometer_start"] = request.form.get("odometer_start")
    if (request.form.get("odometer_end") or "").strip():
        payload["odometer_end"] = request.form.get("odometer_end")
    result = commit_apply(current_user, "update_trip", payload, "human", _key() or f"miles-{trip_id}-{_new_key()}")
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(f"/trips/{trip_id}")

@bp.post("/trips/<int:trip_id>/cards")
@login_required
def trip_card(trip_id):
    if current_user.is_viewer:
        abort(403)
    from app.services.pending import commit_apply

    result = commit_apply(
        current_user,
        "add_plan_card",
        {
            "trip_id": trip_id,
            "property_id": request.form.get("property_id") or "",
            "unit_number": request.form.get("unit_number") or "",
            "title": request.form.get("title") or "",
        },
        "human",
        _key() or f"card-{trip_id}-{_new_key()}",
    )
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(f"/trips/{trip_id}")
