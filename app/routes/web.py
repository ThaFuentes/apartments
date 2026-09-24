"""Screens and the chat. Viewers can read. They cannot post."""
from __future__ import annotations

import json
import secrets
from functools import wraps

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user
from werkzeug.security import generate_password_hash

from app.auth import attempt, home_for, login_person, logout_person, needs_setup, safe_next
from app.builddb.builddb import db
from app.models import (
    ApiCredential,
    ChatMessage,
    City,
    Expense,
    Job,
    JobEvent,
    Media,
    PendingAction,
    Property,
    Report,
    Trip,
    TripProperty,
    Unit,
    UnitVisit,
    User,
)
from app.services.clock import money, utcnow
from app.services.crypto import decrypt_text, encrypt_text, last4
from app.services.files import read_blob, save_blob, send_bytes
from app.services.gemini import resolve_model
from app.services.pending import batch_confirm, confirm_id, confirm_property, discard_id, update_pending
from app.services.people import create_user, find_user
from app.services.records import audit, loads, open_shift, site_profile
from app.services.reports import build_snapshot, load_snapshot, render_pdf, signature_ok
from app.services.talk import clear_chat, handle_message, handle_photo

bp = Blueprint("desk", __name__)


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not getattr(current_user, "is_authenticated", False):
            return redirect(url_for("desk.login", next=request.path))
        return fn(*args, **kwargs)

    return wrapped


def owner_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not getattr(current_user, "is_authenticated", False):
            return redirect(url_for("desk.login", next=request.path))
        if current_user.role != "owner":
            abort(403)
        return fn(*args, **kwargs)

    return wrapped


def _key() -> str:
    return (request.form.get("idempotency_key") or request.headers.get("X-Idempotency-Key") or "").strip()[:120]


def _new_key() -> str:
    return secrets.token_hex(16)


def _history_ok() -> bool:
    if current_user.role != "viewer":
        return True
    return bool(current_user.can_see_history)


def _reports_ok() -> bool:
    if current_user.role != "viewer":
        return True
    return bool(current_user.can_see_reports)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if getattr(current_user, "is_authenticated", False):
        return redirect(home_for(current_user))
    setup = needs_setup()
    if request.method == "POST":
        if setup:
            try:
                user, _generated = create_user(
                    username=request.form.get("username") or "",
                    password=request.form.get("password") or "",
                    display_name=request.form.get("display_name") or "",
                    role="owner",
                    email=request.form.get("email") or None,
                )
                db.session.commit()
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "warn")
                return render_template("login.html", setup=True)
            login_person(user)
            flash("You're in. In Settings you can add a Gemini, Groq, OpenAI, Grok, or other key when you want.", "ok")
            return redirect("/")
        user = attempt(request.form.get("username") or "", request.form.get("password") or "")
        if not user:
            flash("That username and password did not match.", "warn")
            return render_template("login.html", setup=False), 401
        login_person(user)
        return redirect(safe_next(home_for(user)))
    return render_template("login.html", setup=setup)


@bp.post("/logout")
def logout():
    logout_person()
    return redirect("/login")


@bp.route("/join/<token>", methods=["GET", "POST"])
def join(token):
    user = User.query.filter_by(invite_token=token, invite_used=False).first()
    if not user or not user.invite_expires or user.invite_expires < utcnow():
        flash("That invite link is used up or expired.", "warn")
        return redirect("/login")
    if request.method == "POST":
        password = request.form.get("password") or ""
        if len(password) < 8:
            flash("Password needs at least 8 characters.", "warn")
            return render_template("join.html", person=user)
        user.password_hash = generate_password_hash(password)
        user.invite_used = True
        user.invite_token = None
        db.session.commit()
        flash("Password set. Sign in with your username. Email is not required.", "ok")
        return redirect("/login")
    return render_template("join.html", person=user)


@bp.route("/")
@login_required
def home():
    if current_user.role == "viewer":
        return redirect("/reports")
    from app.services.browse import home_board

    return render_template("home.html", board=home_board(current_user.id, current_user), msg_key=_new_key())


@bp.post("/sites")
@login_required
def add_site():
    if current_user.role == "viewer":
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
    if current_user.role == "viewer" and not current_user.can_see_history:
        abort(403)
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    like = f"%{q}%"
    rows = (
        Property.query.filter(Property.deleted_at.is_(None), Property.name.ilike(like))
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
    if current_user.role == "viewer":
        abort(403)
    from app.services.clock import local_today
    from app.services.pending import commit_apply
    from app.services.records import site_profile as profile_for

    profile = profile_for()
    today = local_today(profile.timezone if profile else None).isoformat()
    from app.models import Property

    properties = Property.query.filter(Property.deleted_at.is_(None)).order_by(Property.name.asc()).all()
    if request.method == "POST":
        from app.services.plan import work_cards

        stops = []
        ids = request.form.getlist("property_id")
        works = request.form.getlist("work")
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
            if not prop or prop.deleted_at:
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
            return render_template("plan.html", today=today, properties=properties, msg_key=_new_key())
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
    return render_template("plan.html", today=today, properties=properties, msg_key=_new_key())


@bp.post("/log")
@login_required
def log_work():
    if current_user.role == "viewer":
        abort(403)
    from app.services.pending import commit_apply

    payload = {
        "property_id": request.form.get("property_id") or "",
        "property_name": request.form.get("new_name") or "",
        "city": request.form.get("new_city") or "",
        "unit_number": request.form.get("unit_number") or "",
        "title": request.form.get("title") or "",
        "status": "done",
    }
    result = commit_apply(current_user, "log_work", payload, "human", _key() or _new_key())
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect("/")


@bp.post("/chat")
@login_required
def chat():
    if current_user.role == "viewer":
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
    if request.is_json or request.headers.get("Accept") == "application/json":
        return jsonify(result)
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect("/")


@bp.post("/chat/new")
@login_required
def chat_new():
    if current_user.role == "viewer":
        abort(403)
    result = clear_chat(current_user)
    if request.headers.get("Accept") == "application/json":
        return jsonify(result)
    return redirect("/")


@bp.post("/review")
@login_required
def review():
    if current_user.role == "viewer":
        abort(403)
    ids = request.form.getlist("id")
    accept_all = request.form.get("accept_all") == "1"
    result = batch_confirm(current_user, ids, accept_all=accept_all, source="human")
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(request.form.get("next") or "/")


@bp.post("/pending/<int:pending_id>/confirm")
@login_required
def confirm(pending_id):
    if current_user.role == "viewer":
        abort(403)
    result = confirm_id(current_user, pending_id, "human")
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect("/")


@bp.post("/pending/<int:pending_id>/discard")
@login_required
def discard(pending_id):
    if current_user.role == "viewer":
        abort(403)
    result = discard_id(current_user, pending_id)
    flash(result.get("reply") or "", "ok")
    return redirect("/")


@bp.post("/pending/<int:pending_id>/edit")
@login_required
def edit_pending(pending_id):
    if current_user.role == "viewer":
        abort(403)
    changes = {
        "property_name": request.form.get("property_name"),
        "city": request.form.get("city"),
        "starts_on": request.form.get("starts_on"),
        "purpose": request.form.get("purpose"),
        "unit_number": request.form.get("unit_number"),
        "title": request.form.get("title"),
        "kind": request.form.get("kind"),
        "merchant": request.form.get("merchant"),
        "note": request.form.get("note"),
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
    return redirect("/")


@bp.post("/property/confirm")
@login_required
def property_confirm():
    if current_user.role == "viewer":
        abort(403)
    result = confirm_property(current_user, "human")
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(request.form.get("next") or "/")


@bp.get("/miles")
@login_required
def miles_page():
    if current_user.role == "viewer":
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
    if current_user.role == "viewer":
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
    if current_user.role == "viewer":
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
    if current_user.role == "viewer":
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
    if current_user.role == "viewer":
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


@bp.get("/places")
@login_required
def places():
    if not _history_ok():
        abort(403)
    from app.services.browse import place_groups

    return render_template("places.html", groups=place_groups(user=current_user), city=None)


@bp.get("/places/<int:city_id>")
@login_required
def city_detail(city_id):
    if not _history_ok():
        abort(403)
    city = db.session.get(City, city_id)
    if not city:
        abort(404)
    from app.services.browse import place_groups

    return render_template("places.html", groups=place_groups(city.id, user=current_user), city=city)


@bp.get("/properties/<int:property_id>")
@login_required
def property_detail(property_id):
    if not _history_ok():
        abort(403)
    from app.services.access import can_edit_property, require_see

    prop = require_see(current_user, db.session.get(Property, property_id))
    from app.services.browse import unit_cards

    sort = request.args.get("sort") or "recent"
    if sort not in ("recent", "number"):
        sort = "recent"
    show = request.args.get("show") or ""
    if show not in ("", "worked", "make_ready", "occupied", "needs"):
        show = ""
    building = (request.args.get("building") or "").strip()
    from app.services.board import recent_changes
    from app.services.geo import city_parts, place_title
    from app.services.people import person_label

    city_name, state = city_parts(prop.city.name, prop.city.region) if prop.city else ("", "")
    packed = unit_cards(prop.id, sort=sort, query=request.args.get("q") or "", show=show, building=building)
    return render_template(
        "property.html",
        prop=prop,
        place_name=place_title(prop.name, city_name, state),
        city_name=city_name,
        state_name=state,
        cards=packed["cards"],
        loose_jobs=packed["loose_jobs"],
        unit_total=packed["total"],
        sort=sort,
        show=show,
        building=packed["building"],
        building_names=packed["building_names"],
        changes=recent_changes(prop.id),
        who=person_label,
        editable=can_edit_property(current_user, prop.id),
        msg_key=_new_key(),
    )


@bp.post("/properties/<int:property_id>")
@login_required
def property_save(property_id):
    if current_user.role == "viewer":
        abort(403)
    from app.services.pending import commit_apply

    prop = db.session.get(Property, property_id)
    if not prop:
        abort(404)
    payload = {
        "property_id": prop.id,
        "property_name": (request.form.get("name") or prop.name).strip(),
        "city": (request.form.get("city") or (prop.city.name if prop.city else "")).strip(),
        "region": (request.form.get("region") or (prop.city.region if prop.city else "")).strip(),
        "address": (request.form.get("address") or "").strip(),
    }
    result = commit_apply(current_user, "update_property", payload, "human", _key() or f"prop-{property_id}-{_new_key()}")
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(f"/properties/{property_id}")


@bp.post("/properties/<int:property_id>/delete")
@login_required
def property_delete(property_id):
    if current_user.role == "viewer":
        abort(403)
    from app.services.pending import commit_apply

    result = commit_apply(
        current_user,
        "delete_property",
        {"property_id": property_id},
        "human",
        _key() or f"del-prop-{property_id}-{_new_key()}",
    )
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect("/places")


@bp.post("/properties/<int:property_id>/units")
@login_required
def property_add_unit(property_id):
    from app.services.access import require_edit
    from app.services.board import add_units

    prop = require_edit(current_user, db.session.get(Property, property_id))
    blob = (request.form.get("units") or request.form.get("unit_number") or "").strip()
    result = add_units(current_user, prop, blob, "human", building=request.form.get("building") or "")
    db.session.commit()
    flash(result.get("reply") or "Type the unit numbers.", "ok" if result.get("ok") else "warn")
    return redirect(f"/properties/{property_id}")


@bp.post("/units/<int:unit_id>")
@login_required
def unit_rename(unit_id):
    from app.services.records import normalize_unit

    unit = _editable_unit(unit_id)
    number = normalize_unit(request.form.get("unit_number") or "")
    if not number:
        flash("Type a unit number.", "warn")
        return redirect(f"/properties/{unit.property_id}")
    taken = (
        Unit.query.filter_by(property_id=unit.property_id, unit_number=number)
        .filter(Unit.deleted_at.is_(None), Unit.id != unit.id)
        .first()
    )
    if taken:
        flash(f"Unit {number} is already on this property.", "warn")
        return redirect(f"/properties/{unit.property_id}")
    unit.unit_number = number
    db.session.commit()
    flash(f"Unit number is {number}.", "ok")
    return redirect(f"/properties/{unit.property_id}")


@bp.post("/units/<int:unit_id>/delete")
@login_required
def unit_delete(unit_id):
    unit = _editable_unit(unit_id)
    now = utcnow()
    unit.deleted_at = now
    for job in Job.query.filter_by(unit_id=unit.id).filter(Job.deleted_at.is_(None)).all():
        job.deleted_at = now
    from app.models import UnitTask

    for task in UnitTask.query.filter_by(unit_id=unit.id).filter(UnitTask.deleted_at.is_(None)).all():
        task.deleted_at = now
    db.session.commit()
    flash(f"Removed unit {unit.unit_number}.", "ok")
    return redirect(f"/properties/{unit.property_id}")


@bp.get("/units/<int:unit_id>")
@login_required
def unit_detail(unit_id):
    if not _history_ok():
        abort(403)
    from app.services.access import can_edit_property, require_see

    unit = db.session.get(Unit, unit_id)
    if not unit or unit.deleted_at:
        abort(404)
    require_see(current_user, unit.property)
    from app.models import Equipment

    visits = UnitVisit.query.filter_by(unit_id=unit.id).order_by(UnitVisit.id.desc()).all()
    gear = (
        Equipment.query.filter_by(unit_id=unit.id)
        .filter(Equipment.deleted_at.is_(None))
        .order_by(Equipment.id.desc())
        .all()
    )
    jobs = Job.query.filter_by(unit_id=unit.id).filter(Job.deleted_at.is_(None)).order_by(Job.id.desc()).all()
    events = {}
    for job in jobs:
        events[job.id] = JobEvent.query.filter_by(job_id=job.id).order_by(JobEvent.id.asc()).all()
    from app.services.board import task_groups
    from app.services.equipment import kind_choices, kind_label
    from app.services.people import person_label

    last = jobs[0].created_at if jobs else (visits[0].started_at if visits else None)
    return render_template(
        "unit.html",
        unit=unit,
        visits=visits,
        jobs=jobs,
        events=events,
        gear=gear,
        gear_kinds=kind_choices(),
        kind_label=kind_label,
        tasks=task_groups(unit.id),
        who=person_label,
        editable=can_edit_property(current_user, unit.property_id),
        last=last,
    )


def _editable_unit(unit_id: int):
    from app.services.access import require_edit

    unit = db.session.get(Unit, unit_id)
    if not unit or unit.deleted_at:
        abort(404)
    require_edit(current_user, unit.property)
    return unit


@bp.post("/units/<int:unit_id>/equipment")
@login_required
def unit_equipment(unit_id):
    unit = _editable_unit(unit_id)
    piece = _equipment_form()
    if not any(piece.values()):
        flash("Say what the equipment is, or its brand, model, serial, or a note.", "warn")
        return redirect(f"/units/{unit.id}")
    from app.services.appliers import file_piece
    from app.services.equipment import kind_label

    row, _ambiguous = file_piece(current_user, piece, unit, None, unit.property_id, "human", force_new=True)
    db.session.commit()
    label = kind_label(row.kind) if row else "equipment"
    flash(f"Saved the {label or 'equipment'} in unit {unit.unit_number}.", "ok")
    return redirect(f"/units/{unit.id}")


@bp.post("/units/<int:unit_id>/occupancy")
@login_required
def unit_occupancy(unit_id):
    from app.services.board import set_occupancy

    unit = _editable_unit(unit_id)
    occupancy = (request.form.get("occupancy") or "").strip()
    if occupancy not in ("occupied", "make_ready", ""):
        occupancy = ""
    set_occupancy(current_user, unit, occupancy, "human")
    db.session.commit()
    word = {"occupied": "occupied", "make_ready": "a make ready"}.get(occupancy, "cleared")
    flash(f"Unit {unit.unit_number} is {word}.", "ok")
    return redirect(f"/units/{unit.id}")


@bp.post("/units/<int:unit_id>/tasks")
@login_required
def unit_task_add(unit_id):
    from app.services.board import add_needed

    unit = _editable_unit(unit_id)
    title = (request.form.get("title") or "").strip()
    if not title:
        flash("Say what this unit needs.", "warn")
        return redirect(f"/units/{unit.id}")
    kind = (request.form.get("kind") or "task").strip()
    if kind not in ("task", "part", "work_order", "vendor"):
        kind = "task"
    add_needed(
        current_user,
        unit,
        [title],
        "human",
        kind=kind,
        vendor=request.form.get("vendor") or "",
        notes=request.form.get("notes") or "",
    )
    db.session.commit()
    flash(f"Saved on unit {unit.unit_number}.", "ok")
    return redirect(f"/units/{unit.id}")


@bp.post("/tasks/<int:task_id>/done")
@login_required
def task_done(task_id):
    from app.models import UnitTask

    row = db.session.get(UnitTask, task_id)
    if not row or row.deleted_at:
        abort(404)
    _editable_unit(row.unit_id)
    row.status = "done"
    row.done_by_id = current_user.id
    row.done_at = utcnow()
    db.session.commit()
    flash(f"Done: {row.title}.", "ok")
    return redirect(f"/units/{row.unit_id}")


@bp.post("/tasks/<int:task_id>/delete")
@login_required
def task_delete(task_id):
    from app.models import UnitTask

    row = db.session.get(UnitTask, task_id)
    if not row or row.deleted_at:
        abort(404)
    _editable_unit(row.unit_id)
    row.deleted_at = utcnow()
    unit_id = row.unit_id
    db.session.commit()
    flash("Removed that item.", "ok")
    return redirect(f"/units/{unit_id}")


def _equipment_form() -> dict:
    return {
        "kind": (request.form.get("kind") or "").strip(),
        "brand": (request.form.get("brand") or "").strip(),
        "style": (request.form.get("style") or "").strip(),
        "model": (request.form.get("model") or "").strip(),
        "serial": (request.form.get("serial") or "").strip(),
        "size": (request.form.get("size") or "").strip(),
        "color": (request.form.get("color") or "").strip(),
        "notes": (request.form.get("notes") or "").strip(),
    }


@bp.post("/equipment/<int:gear_id>")
@login_required
def equipment_update(gear_id):
    from app.models import Equipment
    from app.services.equipment import kind_label
    from app.services.records import audit

    row = db.session.get(Equipment, gear_id)
    if not row or row.deleted_at or not row.unit_id:
        abort(404)
    _editable_unit(row.unit_id)
    before = {
        "kind": row.kind,
        "brand": row.brand,
        "style": row.style,
        "model": row.model_number,
        "serial": row.serial_number,
        "notes": row.notes,
    }
    piece = _equipment_form()
    row.kind = piece["kind"][:80]
    row.brand = piece["brand"][:80]
    row.style = piece["style"][:80]
    row.model_number = piece["model"][:80]
    row.serial_number = piece["serial"][:80].upper()
    row.size_label = piece["size"][:40]
    row.color = piece["color"][:40]
    row.notes = piece["notes"][:2000]
    audit(current_user.id, "human", "update", "equipment", row.id, before, {"kind": row.kind, "serial": row.serial_number, "notes": row.notes, "unit_id": row.unit_id})
    db.session.commit()
    flash(f"Updated this {kind_label(row.kind) or 'item'}.", "ok")
    return redirect(f"/units/{row.unit_id}")


@bp.post("/equipment/<int:gear_id>/delete")
@login_required
def equipment_delete(gear_id):
    from app.models import Equipment

    row = db.session.get(Equipment, gear_id)
    if not row or row.deleted_at or not row.unit_id:
        abort(404)
    _editable_unit(row.unit_id)
    unit_id = row.unit_id
    row.deleted_at = utcnow()
    db.session.commit()
    flash("Removed that piece of equipment.", "ok")
    return redirect(f"/units/{unit_id}" if unit_id else "/places")


@bp.get("/expenses")
@login_required
def expenses():
    if current_user.role == "viewer":
        abort(403)
    rows = Expense.query.filter(Expense.deleted_at.is_(None)).order_by(Expense.id.desc()).limit(80).all()
    return render_template("expenses.html", expenses=rows, money=money, msg_key=_new_key())


@bp.post("/expenses")
@login_required
def expense_save():
    if current_user.role == "viewer":
        abort(403)
    if request.headers.get("X-Apt-Offline-Queue") == "1":
        return jsonify({"ok": False, "error": "Confirm the expense when you are online."}), 409
    from app.services.pending import commit_apply

    try:
        cents = int(round(float(request.form.get("amount") or "0") * 100))
    except ValueError:
        cents = 0
    payload = {
        "kind": request.form.get("kind") or "other",
        "amount_cents": cents,
        "merchant": request.form.get("merchant") or "",
        "note": request.form.get("note") or "",
        "fields_confirmed": True,
        "confidence": 1,
    }
    if request.form.get("odometer"):
        payload["odometer"] = int(request.form.get("odometer"))
        payload["gas_stop"] = payload["kind"] == "gas"
    result = commit_apply(current_user, "log_expense", payload, "human", _key() or _new_key())
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect("/expenses")


@bp.get("/reports")
@login_required
def reports():
    if not _reports_ok():
        abort(403)
    saved = Report.query.filter(Report.deleted_at.is_(None), Report.status.in_(("ready", "sent"))).order_by(Report.id.desc()).all()
    if current_user.role == "viewer":
        preview = None
    else:
        preview = build_snapshot(kind="weekly", author=current_user.label())
    return render_template("reports.html", saved=saved, preview=preview, money=money, msg_key=_new_key())


@bp.post("/reports/build")
@login_required
def reports_build():
    if current_user.role == "viewer":
        abort(403)
    from app.services.pending import commit_apply

    kind = request.form.get("kind") or "weekly"
    result = commit_apply(
        current_user,
        "draft_report",
        {"kind": kind, "force_new": request.form.get("force_new") == "1"},
        "human",
        _key() or f"report-{kind}-{_new_key()}",
    )
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    if result.get("report_id"):
        return redirect(f"/reports/{result['report_id']}")
    return redirect("/reports")


@bp.get("/reports/<int:report_id>")
@login_required
def report_detail(report_id):
    if not _reports_ok():
        abort(403)
    report = db.session.get(Report, report_id)
    if not report or report.deleted_at:
        abort(404)
    viewers = User.query.filter_by(role="viewer", active=True, can_see_reports=True).order_by(User.username.asc()).all()
    return render_template(
        "report.html",
        report=report,
        snapshot=load_snapshot(report),
        money=money,
        viewers=viewers,
        msg_key=_new_key(),
    )


@bp.post("/reports/<int:report_id>")
@login_required
def report_edit(report_id):
    if current_user.role == "viewer":
        abort(403)
    report = db.session.get(Report, report_id)
    if not report or report.status == "sent":
        flash("A sent report stays as the copy your bosses already have.", "warn")
        return redirect(f"/reports/{report_id}")
    body = request.form.get("body_md") or ""
    before = {"body": report.body_md[:80]}
    report.body_md = body
    snap = load_snapshot(report)
    snap["edited"] = True
    report.snapshot_json = json.dumps(snap)
    audit(current_user.id, "human", "update", "report", report.id, before, {"body": body[:80]})
    db.session.commit()
    flash("Report updated.", "ok")
    return redirect(f"/reports/{report_id}")


@bp.get("/reports/<int:report_id>/pdf")
def report_pdf(report_id):
    report = db.session.get(Report, report_id)
    if not report or report.deleted_at:
        abort(404)
    signed = signature_ok(report_id, request.args.get("exp"), request.args.get("sig"))
    if not signed:
        if not getattr(current_user, "is_authenticated", False) or not _reports_ok():
            abort(403)
    data = render_pdf(load_snapshot(report))
    name = f"apt-report-{report.id}.pdf"
    return send_bytes(data, "application/pdf", name, as_attachment=request.args.get("dl") == "1")


@bp.post("/reports/<int:report_id>/send")
@login_required
def report_send(report_id):
    if current_user.role != "owner":
        abort(403)
    from app.services.pending import commit_apply

    result = commit_apply(
        current_user,
        "send_report",
        {"report_id": report_id, "also": request.form.get("also") or ""},
        "human",
        _key() or f"send-{report_id}-{_new_key()}",
    )
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(f"/reports/{report_id}")


@bp.get("/map")
@login_required
def map_page():
    if current_user.role == "viewer" and not current_user.can_see_live_map and not current_user.can_see_history:
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
    if current_user.role == "viewer":
        abort(403)
    from app.services.share import start_share, stop_share

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
    if current_user.role == "viewer":
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
    if current_user.role == "viewer":
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
    card = propose(
        user,
        "record_unit_visit",
        payload,
        (quota + " " if quota else "") + summary + " Not saved yet.",
        "material",
        f"photo-{media.id}",
        f"photo-{media.id}",
        "ai",
    )
    return card


@bp.get("/media/<int:media_id>")
@login_required
def media_file(media_id):
    if current_user.role == "viewer":
        abort(403)
    media = db.session.get(Media, media_id)
    if not media:
        abort(404)
    data = read_blob(media.storage_name)
    if not data:
        abort(404)
    return send_bytes(data, media.mime or "image/jpeg")


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if current_user.role == "viewer":
        abort(403)
    profile = site_profile()
    keys = []
    providers = []
    if current_user.role == "owner":
        from app.services.providers import PROVIDERS

        keys = ApiCredential.query.filter_by(user_id=current_user.id).all()
        keys.sort(key=lambda row: (row.use_order or 99, 0 if row.preferred else 1, row.id))
        for row in keys:
            row.provider_label = (PROVIDERS.get(row.provider) or {}).get("label") or row.provider
        providers = [
            {"id": key, "label": spec["label"], "hint": spec["hint"], "models": list(spec["models"])}
            for key, spec in PROVIDERS.items()
        ]
    if request.method == "POST":
        if current_user.role != "owner":
            abort(403)
        from app.services.pending import commit_apply

        payload = {
            "assistant_name": request.form.get("assistant_name"),
            "tone": request.form.get("tone"),
            "always_ask": request.form.get("always_ask"),
            "default_city": request.form.get("default_city"),
            "default_region": request.form.get("default_region"),
            "report_voice": request.form.get("report_voice"),
            "company_name": request.form.get("company_name"),
            "home_label": request.form.get("home_label"),
            "timezone": request.form.get("timezone"),
            "reporter_name": request.form.get("reporter_name"),
            "smtp_host": request.form.get("smtp_host"),
            "smtp_port": request.form.get("smtp_port"),
            "smtp_user": request.form.get("smtp_user"),
            "smtp_from": request.form.get("smtp_from"),
            "smtp_password": request.form.get("smtp_password"),
        }
        if request.form.get("home_lat") and request.form.get("home_lng"):
            payload["home_lat"] = request.form.get("home_lat")
            payload["home_lng"] = request.form.get("home_lng")
        result = commit_apply(current_user, "update_settings", payload, "human", _key() or _new_key())
        flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
        return redirect("/settings")
    return render_template("settings.html", profile=profile, keys=keys, providers=providers, msg_key=_new_key())


@bp.post("/settings/key")
@owner_required
def settings_key():
    from app.services.providers import PROVIDERS, check_key

    provider = (request.form.get("provider") or "").strip().lower()
    raw = (request.form.get("api_key") or "").strip()
    model = (request.form.get("model_custom") or request.form.get("model") or "").strip()
    base_url = (request.form.get("base_url") or "").strip()
    if provider not in PROVIDERS:
        flash("Pick a provider.", "warn")
        return redirect("/settings")
    if len(raw) < 8:
        flash("Paste the API key. It is stored encrypted and only the last 4 are shown.", "warn")
        return redirect("/settings")
    checked = check_key(provider, raw, model, base_url)
    if not checked.get("ok"):
        flash(checked.get("error") or "That key did not answer. I did not keep retrying.", "warn")
        return redirect("/settings")
    others = ApiCredential.query.filter_by(user_id=current_user.id).count()
    row = ApiCredential(
        user_id=current_user.id,
        provider=provider,
        secret_ciphertext=encrypt_text(raw),
        last4=last4(raw),
        model_id=checked.get("model") or model,
        base_url=base_url or None,
        active=True,
        preferred=others == 0,
        use_order=others + 1,
        model_checked_at=utcnow(),
        created_at=utcnow(),
    )
    db.session.add(row)
    from app.services.records import audit

    audit(current_user.id, "human", "update", "api_credential", None, {}, {"provider": provider, "last4": row.last4, "model": row.model_id})
    db.session.commit()
    flash(f"Saved the {PROVIDERS[provider]['label']} key ····{row.last4}. Model {row.model_id}.", "ok")
    return redirect("/settings")


@bp.post("/settings/key/<int:key_id>")
@owner_required
def settings_key_update(key_id):
    row = ApiCredential.query.filter_by(id=key_id, user_id=current_user.id).first()
    if not row:
        abort(404)
    model = (request.form.get("model") or "").strip()
    if model:
        row.model_id = model[:120]
    base_url = (request.form.get("base_url") or "").strip()
    row.base_url = base_url[:300] or None
    row.active = request.form.get("active") == "1"
    order = (request.form.get("use_order") or "").strip()
    if order.isdigit():
        place = max(1, min(int(order), 9))
        for other in ApiCredential.query.filter_by(user_id=current_user.id).all():
            if other.id != row.id and (other.use_order or 0) == place:
                other.use_order = row.use_order or 0
            other.preferred = False
        row.use_order = place
        row.preferred = place == 1
    db.session.commit()
    state = "on" if row.active else "off"
    flash(f"Saved ····{row.last4}. Model {row.model_id or 'unset'}. {state}.", "ok")
    return redirect("/settings")


@bp.post("/settings/key/<int:key_id>/prefer")
@owner_required
def settings_key_prefer(key_id):
    row = ApiCredential.query.filter_by(id=key_id, user_id=current_user.id).first()
    if not row:
        abort(404)
    others = ApiCredential.query.filter_by(user_id=current_user.id).all()
    for other in others:
        if other.id != row.id and (other.use_order or 0) == 1:
            other.use_order = row.use_order or 2
        other.preferred = other.id == row.id
    row.use_order = 1
    row.active = True
    db.session.commit()
    flash("That key is 1st.", "ok")
    return redirect("/settings")


@bp.post("/settings/key/<int:key_id>/delete")
@owner_required
def settings_key_delete(key_id):
    row = ApiCredential.query.filter_by(id=key_id, user_id=current_user.id).first()
    if row:
        db.session.delete(row)
        db.session.commit()
    flash("Key removed.", "ok")
    return redirect("/settings")


@bp.route("/users", methods=["GET", "POST"])
@owner_required
def users():
    if request.method == "POST":
        from app.services.pending import commit_apply

        payload = {
            "username": request.form.get("username") or "",
            "display_name": request.form.get("display_name") or "",
            "role": request.form.get("role") or "viewer",
            "email": request.form.get("email") or "",
            "password": request.form.get("password") or "",
            "can_see_reports": request.form.get("can_see_reports") == "1",
            "can_see_history": request.form.get("can_see_history") == "1",
            "can_see_live_map": request.form.get("can_see_live_map") == "1",
        }
        result = commit_apply(current_user, "invite_viewer", payload, "human", _key() or _new_key())
        flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
        return redirect("/users")
    from app.models import Property, PropertyAccess

    people = User.query.order_by(User.id.asc()).all()
    properties = Property.query.filter(Property.deleted_at.is_(None)).order_by(Property.name.asc()).all()
    access = {(row.user_id, row.property_id): row for row in PropertyAccess.query.all()}
    return render_template("users.html", people=people, properties=properties, access=access, msg_key=_new_key())


@bp.post("/users/<int:user_id>/access")
@owner_required
def user_access(user_id):
    from app.models import Property
    from app.services.access import set_access

    person = db.session.get(User, user_id)
    if not person or person.role == "owner":
        abort(404)
    props = Property.query.filter(Property.deleted_at.is_(None)).all()
    for prop in props:
        see = request.form.get(f"see-{prop.id}") == "1"
        edit = request.form.get(f"edit-{prop.id}") == "1"
        notify = request.form.get(f"notify-{prop.id}") == "1"
        set_access(current_user, person, prop, see=see, edit=edit and see, notify=notify and see)
    db.session.commit()
    flash("Property access saved.", "ok")
    return redirect("/users")


@bp.post("/properties/<int:property_id>/pin")
@login_required
def property_pin(property_id):
    from app.services.access import pin_property, require_see

    prop = require_see(current_user, db.session.get(Property, property_id))
    reply = pin_property(current_user, prop, request.form.get("pinned") == "1")
    db.session.commit()
    flash(reply, "ok")
    return redirect("/places")


@bp.post("/users/<int:user_id>")
@owner_required
def user_update(user_id):
    from app.services.pending import commit_apply

    person = db.session.get(User, user_id)
    if not person:
        abort(404)
    payload = {
        "username": person.username,
        "role": request.form.get("role") or person.role,
        "can_see_reports": request.form.get("can_see_reports") == "1",
        "can_see_history": request.form.get("can_see_history") == "1",
        "can_see_live_map": request.form.get("can_see_live_map") == "1",
        "active": request.form.get("active") == "1",
        "clear_email": request.form.get("clear_email") == "1",
    }
    if request.form.get("email"):
        payload["email"] = request.form.get("email")
    result = commit_apply(current_user, "update_viewer", payload, "human", _key() or f"user-{user_id}-{_new_key()}")
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect("/users")


@bp.post("/drive")
@login_required
def drive():
    resp = make_response(redirect(request.form.get("next") or "/"))
    if request.form.get("on") == "1":
        resp.set_cookie("apt_drive", "1", max_age=60 * 60 * 12, samesite="Lax", httponly=False)
    else:
        resp.set_cookie("apt_drive", "", expires=0)
    return resp


@bp.post("/jobs/<int:job_id>/delete")
@login_required
def job_delete(job_id):
    if current_user.role == "viewer":
        abort(403)
    from app.services.pending import commit_apply

    result = commit_apply(current_user, "soft_delete", {"entity": "job", "entity_id": job_id}, "human", _key() or f"del-job-{job_id}-{_new_key()}")
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(request.form.get("next") or "/trips")


@bp.post("/jobs/<int:job_id>/restore")
@login_required
def job_restore(job_id):
    if current_user.role == "viewer":
        abort(403)
    from app.services.pending import commit_apply

    result = commit_apply(current_user, "restore", {"entity": "job", "entity_id": job_id}, "human", _key() or f"restore-job-{job_id}-{_new_key()}")
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(request.form.get("next") or "/trips")
