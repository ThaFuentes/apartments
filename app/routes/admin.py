"""Apt owner settings and user administration routes."""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request
from flask_login import current_user

from app.builddb.builddb import db
from app.models import ApiCredential, User
from app.services.clock import utcnow
from app.services.crypto import encrypt_text, last4
from app.routes.common import bp, login_required, owner_required, _key, _new_key


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if current_user.role == "viewer":
        abort(403)
    from app.services.records import site_profile

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
    from app.models import UserCapability
    from app.services.access import CAPABILITY_LABELS, ROLE_CAPABILITIES, normalize_role

    overrides = {(row.user_id, row.capability): row.granted for row in UserCapability.query.all()}
    role_defaults = ROLE_CAPABILITIES
    default_capabilities = {
        (person.id, capability): (
            "*" in role_defaults.get(normalize_role(person.role), set())
            or capability in role_defaults.get(normalize_role(person.role), set())
        )
        for person in people
        for capability in CAPABILITY_LABELS
    }
    return render_template(
        "users.html",
        people=people,
        properties=properties,
        access=access,
        capabilities=CAPABILITY_LABELS,
        role_defaults=role_defaults,
        overrides=overrides,
        default_capabilities=default_capabilities,
        msg_key=_new_key(),
    )


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


@bp.post("/users/<int:user_id>/capabilities")
@owner_required
def user_capabilities(user_id):
    from app.services.access import CAPABILITIES, set_user_capability

    person = db.session.get(User, user_id)
    if not person or person.role == "owner":
        abort(404)
    try:
        for capability in CAPABILITIES:
            value = request.form.get(f"cap-{capability}", "default")
            granted = True if value == "allow" else False if value == "deny" else None
            set_user_capability(current_user, person, capability, granted)
        db.session.commit()
        flash(f"Permissions saved for {person.display_name or person.username}.", "ok")
    except (PermissionError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "warn")
    return redirect("/users")


@bp.post("/properties/<int:property_id>/pin")
@login_required
def property_pin(property_id):
    from app.services.access import pin_property, require_see
    from app.models import Property

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
