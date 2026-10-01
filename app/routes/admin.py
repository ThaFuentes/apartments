"""Apt owner settings and user administration routes."""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request
from flask_login import current_user

from app.builddb.builddb import db
from app.models import ApiCredential, User
from app.services.clock import utcnow
from app.services.crypto import encrypt_text, last4
from app.routes.common import bp, login_required, owner_required, people_admin, _key, _new_key
from app.services.roles import operational_choices, role_choices as catalog_role_choices


def _assignable_roles(actor):
    from app.services.access import can_create_user
    from app.services.access.core import role_of

    include_owner = role_of(actor) == "owner"
    return [(value, label) for value, label in catalog_role_choices(include_owner=include_owner) if can_create_user(actor, value)]


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if current_user.is_viewer:
        abort(403)
    from app.services.records import site_profile

    profile = site_profile()
    keys = []
    providers = []
    if current_user.role == "owner":
        from app.services import budget
        from app.services.providers import PROVIDERS

        keys = ApiCredential.query.filter_by(user_id=current_user.id).all()
        keys.sort(key=lambda row: (row.use_order or 99, 0 if row.preferred else 1, row.id))
        for row in keys:
            row.provider_label = (PROVIDERS.get(row.provider) or {}).get("label") or row.provider
            row.used = budget.spent(row)
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


def _bounded_int(name: str, fallback: int, low: int, high: int) -> int:
    raw = (request.form.get(name) or "").strip()
    if not raw.isdigit():
        return fallback
    return max(low, min(int(raw), high))


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
    row.max_reply_tokens = _bounded_int("max_reply_tokens", row.max_reply_tokens or 0, 0, 100000)
    row.burst_tokens = _bounded_int("burst_tokens", row.burst_tokens or 5000, 0, 1000000)
    minutes = _bounded_int("burst_minutes", (row.burst_seconds or 180) // 60, 1, 1440)
    row.burst_seconds = minutes * 60
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
    cap = f"{row.max_reply_tokens}" if row.max_reply_tokens else "provider default"
    burst = f"{row.burst_tokens} per {minutes} min" if row.burst_tokens else "no burst limit"
    flash(f"Saved ····{row.last4}. Model {row.model_id or 'unset'}. {state}. Reply cap {cap}. {burst}.", "ok")
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
@people_admin
def users():
    if request.method == "POST":
        from app.services.pending import commit_apply
        from app.services.people import suggest_username

        display = (request.form.get("display_name") or "").strip()
        username = (request.form.get("username") or "").strip()
        if not display and not username:
            flash("Need their name.", "warn")
            return redirect("/users")
        if not username:
            username = suggest_username(display)
        grants = []
        for raw in request.form.getlist("property_id"):
            try:
                grants.append({"property_id": int(raw), "see": True, "edit": True})
            except (TypeError, ValueError):
                continue
        payload = {
            "username": username,
            "display_name": display or username,
            "role": request.form.get("role") or "office",
            "email": request.form.get("email") or "",
            "phone": request.form.get("phone") or "",
            "password": request.form.get("password") or "",
            "is_bot": request.form.get("is_bot") == "1",
            "security_email": request.form.get("security_email") or "",
            "reset_email": request.form.get("reset_email") or "",
            "can_see_reports": True,
            "can_see_history": True,
            "can_see_live_map": request.form.get("can_see_live_map") == "1",
            "_grants": grants,
        }
        result = commit_apply(current_user, "invite_viewer", payload, "human", _key() or _new_key())
        flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
        return redirect("/users")
    from app.models import Property, PropertyAccess

    people = User.query.order_by(User.id.asc()).all()
    from app.services.access.management import can_manage_company_users, can_manage_user, can_create_custom_role

    if not can_manage_company_users(current_user):
        people = [person for person in people if person.id == current_user.id or can_manage_user(current_user, person)]
    properties = Property.query.filter(Property.deleted_at.is_(None)).order_by(Property.name.asc()).all()
    from app.services.access import can_manage_property_people, sees_all

    if current_user.role != "owner" and not sees_all(current_user):
        properties = [prop for prop in properties if can_manage_property_people(current_user, prop.id)]
    access = {(row.user_id, row.property_id): row for row in PropertyAccess.query.all()}
    from app.models import Region, RegionAccess, UserCapability
    from app.services.access import CAPABILITY_LABELS, ROLE_CAPABILITIES, normalize_role
    from app.services.hats import describe_hat, hats_for
    from app.services.roles import custom_roles

    overrides = {(row.user_id, row.capability): row.granted for row in UserCapability.query.filter_by(scope_key="global").all()}
    regional_policies = {(row.user_id, int(row.scope_key[7:])): row.granted for row in UserCapability.query.filter_by(capability="open_team_default_property").all() if row.scope_key.startswith("region:") and row.scope_key[7:].isdigit()}
    regions_by_manager: dict[int, list[int]] = {}
    for link in RegionAccess.query.all():
        regions_by_manager.setdefault(link.user_id, []).append(link.region_id)
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
        role_choices=_assignable_roles(current_user),
        all_role_choices=catalog_role_choices(include_owner=True),
        hat_choices=operational_choices(),
        hats={person.id: hats_for(person) for person in people},
        hat_lines={person.id: person.role_line() for person in people},
        hat_text={hat.id: describe_hat(hat) for person in people for hat in hats_for(person)},
        regions=Region.query.filter_by(active=True).order_by(Region.name.asc()).all(),
        can_add_titles=can_create_custom_role(current_user),
        extra_titles=custom_roles(),
        seats={
            person.id: [prop.name for prop in properties if access.get((person.id, prop.id))]
            for person in people
        },
        regions_by_manager=regions_by_manager,
        regional_policies=regional_policies,
        msg_key=_new_key(),
    )


@bp.post("/users/<int:user_id>/access")
@people_admin
def user_access(user_id):
    from app.models import Property
    from app.services.access import set_access

    person = db.session.get(User, user_id)
    if not person or person.role == "owner":
        abort(404)
    props = Property.query.filter(Property.deleted_at.is_(None)).all()
    for prop in props:
        if current_user.role == "regional_manager":
            from app.services.access.management import can_manage_property_people

            if not can_manage_property_people(current_user, prop.id):
                continue
        see = request.form.get(f"see-{prop.id}") == "1"
        edit = request.form.get(f"edit-{prop.id}") == "1"
        notify = request.form.get(f"notify-{prop.id}") == "1"
        set_access(current_user, person, prop, see=see, edit=edit and see, notify=notify and see)
    db.session.commit()
    flash("Property access saved.", "ok")
    return redirect("/users")


@bp.post("/users/<int:user_id>/capabilities")
@people_admin
def user_capabilities(user_id):
    from app.services.access import CAPABILITIES, set_user_capability

    person = db.session.get(User, user_id)
    if not person or person.role == "owner":
        abort(404)
    if current_user.role == "regional_manager":
        from app.services.access.management import can_manage_pm_default_policy

        if person.role != "property_manager" or not any(can_manage_pm_default_policy(current_user, person, rid) for rid in __import__("app.models", fromlist=["RegionAccess"]).RegionAccess.query.filter_by(user_id=current_user.id).with_entities(__import__("app.models", fromlist=["RegionAccess"]).RegionAccess.region_id).all()):
            abort(403)
    try:
        for capability in CAPABILITIES - {"open_team_default_property", "manage_security"}:
            value = request.form.get(f"cap-{capability}", "default")
            granted = True if value == "allow" else False if value == "deny" else None
            set_user_capability(current_user, person, capability, granted)
        company_policy = request.form.get("team-default-global")
        if company_policy is not None:
            granted = True if company_policy == "allow" else False if company_policy == "deny" else None
            set_user_capability(current_user, person, "open_team_default_property", granted)
        db.session.commit()
        flash(f"Permissions saved for {person.display_name or person.username}.", "ok")
    except (PermissionError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "warn")
    return redirect("/users")


@bp.post("/users/<int:user_id>/default-policy/<int:region_id>")
@people_admin
def user_default_policy(user_id, region_id):
    from app.services.access import set_user_capability

    person = db.session.get(User, user_id)
    if not person or person.role != "property_manager":
        abort(404)
    value = request.form.get("policy", "default")
    granted = True if value == "allow" else False if value == "deny" else None
    try:
        set_user_capability(current_user, person, "open_team_default_property", granted, f"region:{region_id}")
        db.session.commit()
        flash(f"Default-property policy saved for {person.display_name or person.username}.", "ok")
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
@people_admin
def user_update(user_id):
    from app.services.pending import commit_apply

    person = db.session.get(User, user_id)
    if not person:
        abort(404)
    payload = {
        "username": person.username,
        "role": request.form.get("role") or person.role,
        "clear_email": request.form.get("clear_email") == "1",
    }
    if request.form.get("has_flags"):
        payload["can_see_reports"] = request.form.get("can_see_reports") == "1"
        payload["can_see_history"] = request.form.get("can_see_history") == "1"
        payload["can_see_live_map"] = request.form.get("can_see_live_map") == "1"
        payload["active"] = request.form.get("active") == "1"
    if request.form.get("email"):
        payload["email"] = request.form.get("email")
    if request.form.get("security_email"):
        payload["security_email"] = request.form.get("security_email")
    if request.form.get("reset_email"):
        payload["reset_email"] = request.form.get("reset_email")
    if request.form.get("phone"):
        payload["phone"] = request.form.get("phone")
    if request.form.get("clear_phone") == "1":
        payload["phone"] = ""
    result = commit_apply(current_user, "update_viewer", payload, "human", _key() or f"user-{user_id}-{_new_key()}")
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect("/users")


@bp.post("/users/<int:user_id>/bot")
@people_admin
def user_bot(user_id):
    from app.services.access import can_manage_user
    from app.services.twofa import turn_off

    person = db.session.get(User, user_id)
    if not person:
        abort(404)
    make = (request.form.get("is_bot") or "") in ("1", "true", "on", "yes")
    if person.role == "owner":
        abort(403)
    if current_user.role != "owner" and not can_manage_user(current_user, person):
        abort(403)
    if not make and current_user.role != "owner":
        flash("Only an owner can unmark a bot (that clears 2FA).", "warn")
        return redirect("/users")
    person.is_bot = make
    if not make:
        turn_off(person)
    db.session.commit()
    flash(f"{person.label()} is {'a bot login' if make else 'a regular login'} now.", "ok")
    return redirect("/users")


@bp.post("/users/<int:user_id>/hat")
@people_admin
def user_hat(user_id):
    from app.services.hats import set_hat

    person = db.session.get(User, user_id)
    if not person:
        abort(404)
    try:
        reply = set_hat(
            current_user,
            person,
            request.form.get("role") or "",
            property_id=int(request.form.get("property_id") or 0) or None,
            region_id=int(request.form.get("region_id") or 0) or None,
            label=request.form.get("label") or "",
        )
        db.session.commit()
        flash(reply, "ok")
    except (PermissionError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "warn")
    return redirect("/users")


@bp.post("/users/<int:user_id>/hat/<int:hat_id>/delete")
@people_admin
def user_hat_delete(user_id, hat_id):
    from app.services.hats import clear_hat

    person = db.session.get(User, user_id)
    if not person:
        abort(404)
    try:
        reply = clear_hat(current_user, person, hat_id)
        db.session.commit()
        flash(reply, "ok")
    except (PermissionError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "warn")
    return redirect("/users")


@bp.post("/users/<int:user_id>/unlock")
@people_admin
def user_unlock(user_id):
    from app.services.access import can_manage_user
    from app.services.people import unlock_login

    person = db.session.get(User, user_id)
    if not person:
        abort(404)
    if current_user.role != "owner" and not can_manage_user(current_user, person):
        abort(403)
    unlock_login(person)
    db.session.commit()
    flash(f"{person.label()} can sign in again.", "ok")
    return redirect("/users")


@bp.post("/users/<int:user_id>/reset")
@people_admin
def user_send_reset(user_id):
    from app.services.access import can_manage_user
    from app.services.passwords import issue_reset, send_reset

    person = db.session.get(User, user_id)
    if not person:
        abort(404)
    if person.role == "owner" and current_user.id != person.id:
        abort(403)
    if current_user.id != person.id and not can_manage_user(current_user, person) and current_user.role != "owner":
        abort(403)
    token = issue_reset(person)
    if not token:
        flash("That login has no reset inbox yet.", "warn")
        return redirect("/users")
    ok, msg = send_reset(person, token)
    flash(msg if ok else (msg or "The reset email did not send."), "ok" if ok else "warn")
    return redirect("/users")


@bp.post("/roles")
@people_admin
def custom_role_create():
    from app.services.access.management import create_custom_role

    try:
        row = create_custom_role(current_user, request.form.get("label") or "", request.form.get("based_on") or "office")
        db.session.commit()
        flash(f"{row.label} is ready. You can assign it like any other job title.", "ok")
    except (PermissionError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "warn")
    return redirect("/users")
