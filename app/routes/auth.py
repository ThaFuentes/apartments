"""Login, invite, bot 2FA, and password-reset routes."""
from __future__ import annotations

from flask import flash, redirect, render_template, request
from flask_login import current_user
from werkzeug.security import generate_password_hash

from app.auth import home_for, login_person, logout_person, needs_setup, safe_next
from app.builddb.builddb import db
from app.models import User
from app.routes.common import bp, login_required
from app.services.clock import utcnow
from app.services.people import create_user


@bp.route("/login", methods=["GET", "POST"])
def login():
    if getattr(current_user, "is_authenticated", False):
        return redirect(safe_next(home_for(current_user)))
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
        from app.services.people import try_login

        user, reason = try_login(request.form.get("username") or "", request.form.get("password") or "")
        if not user:
            flash(reason, "warn")
            return render_template("login.html", setup=False), 401
        from app.services import twofa as twofa_util

        if twofa_util.twofa_enabled(user):
            method = twofa_util.twofa_method(user)
            twofa_util.stash_pending_login(user.id, method)
            if method == "email":
                ok, msg = twofa_util.send_email_code(user)
                if not ok:
                    twofa_util.pop_pending_login()
                    flash(msg, "warn")
                    return render_template("login.html", setup=False)
                flash(msg, "ok")
            nxt = safe_next("")
            if nxt:
                from urllib.parse import urlencode

                return redirect("/2fa?" + urlencode({"next": nxt}))
            return redirect("/2fa")
        login_person(user)
        return redirect(safe_next(home_for(user)))
    return render_template("login.html", setup=setup)


@bp.post("/logout")
def logout():
    logout_person()
    return redirect("/login")


@bp.post("/account/reset-email")
@login_required
def account_reset_email():
    from app.services.people import clean_email
    from app.services.records import audit

    try:
        reset_email = clean_email(request.form.get("reset_email"))
    except ValueError as exc:
        flash(str(exc), "warn")
        return redirect("/more")
    previous = current_user.reset_email
    current_user.reset_email = reset_email
    audit(
        current_user.id,
        "human",
        "update",
        "user",
        current_user.id,
        {"reset_email": previous},
        {"reset_email": reset_email},
    )
    db.session.commit()
    flash("Password-reset email saved." if reset_email else "Password resets will use your login email.", "ok")
    return redirect("/more")


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


@bp.route("/2fa", methods=["GET", "POST"])
def twofa_verify():
    from app.services import twofa as twofa_util

    pending = twofa_util.pending_login()
    if pending is None:
        return redirect("/login")
    user = db.session.get(User, int(pending["user_id"]))
    if user is None or not user.active or not twofa_util.twofa_enabled(user):
        twofa_util.pop_pending_login()
        return redirect("/login")
    method = pending.get("method") or twofa_util.twofa_method(user)
    if request.method == "POST":
        do = (request.form.get("do") or "").strip()
        if do == "resend":
            ok, msg = twofa_util.send_email_code(user)
            flash(msg, "ok" if ok else "warn")
            return render_template("twofa.html", method=method, inbox=twofa_util.twofa_inbox_for(user))
        if do == "cancel":
            twofa_util.pop_pending_login()
            return redirect("/login")
        code = (request.form.get("code") or "").strip()
        ok = False
        if method == "email":
            ok = twofa_util.email_code_ok(pending, code)
        else:
            if twofa_util.totp_replay_recent(user, code):
                flash("That code was already used. Wait for the next one.", "warn")
                return render_template("twofa.html", method=method, inbox=twofa_util.twofa_inbox_for(user))
            ok = twofa_util.totp_ok((twofa_util.twofa_settings(user).get("secret") or ""), code)
        if not ok:
            fails = twofa_util.register_pending_fail(pending)
            if fails >= twofa_util.LOCK_AFTER_FAILS:
                flash("Too many wrong codes. Start again at sign in.", "warn")
                return redirect("/login")
            flash(f"That code is not right. {twofa_util.LOCK_AFTER_FAILS - fails} tries left.", "warn")
            return render_template("twofa.html", method=method, inbox=twofa_util.twofa_inbox_for(user))
        if method == "email":
            twofa_util.pop_pending_login()
        else:
            twofa_util.mark_totp_used(user, code)
            db.session.commit()
            twofa_util.pop_pending_login()
        login_person(user)
        flash("Two-factor check passed.", "ok")
        return redirect(safe_next(home_for(user)))
    return render_template("twofa.html", method=method, inbox=twofa_util.twofa_inbox_for(user))


@bp.route("/bot-setup", methods=["GET", "POST"])
@login_required
def bot_setup():
    from app.services import twofa as twofa_util
    from app.services.people import clean_email

    if not bool(getattr(current_user, "is_bot", False)):
        return redirect(home_for(current_user))
    if request.method == "POST":
        do = (request.form.get("do") or "").strip()
        try:
            if do == "emails":
                security = clean_email(request.form.get("security_email"))
                reset = clean_email(request.form.get("reset_email"))
                current_user.security_email = security
                current_user.reset_email = reset or None
                db.session.commit()
                flash("Inboxes saved. The 2FA address and the reset address can match, or they can be different.", "ok")
            elif do == "totp-start":
                twofa_util.begin_totp_setup(current_user)
                db.session.commit()
                flash("Scan the key into an authenticator app, then confirm one code.", "ok")
            elif do == "totp-confirm":
                ok, msg = twofa_util.confirm_totp_setup(current_user, request.form.get("code") or "")
                if ok:
                    db.session.commit()
                else:
                    db.session.rollback()
                flash(msg, "ok" if ok else "warn")
            elif do == "email-method":
                if not twofa_util.twofa_inbox_for(current_user):
                    flash("Add a 2FA inbox first, then emailed codes will work.", "warn")
                else:
                    twofa_util.save_twofa(current_user, method="email", secret=None)
                    db.session.commit()
                    flash("Sign-in codes will go to the 2FA inbox.", "ok")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "warn")
        return redirect("/bot-setup")
    gaps = twofa_util.bot_setup_remaining(current_user)
    if not gaps:
        return redirect("/")
    blob = twofa_util.twofa_settings(current_user)
    pending = ""
    if not twofa_util.twofa_enabled(current_user):
        pending = (blob.get("secret") or "").strip()
    return render_template(
        "bot_setup.html",
        gaps=gaps,
        pending_secret=pending,
        otpauth=twofa_util.otpauth_url(pending, current_user.username) if pending else "",
        inbox=twofa_util.twofa_inbox_for(current_user),
        login_email=(current_user.email or "").strip(),
        security_email=(current_user.security_email or "").strip(),
        reset_email=(current_user.reset_email or "").strip(),
    )


@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    from app.services.passwords import SAME_MSG, find_for_reset, issue_reset, send_reset

    if request.method == "POST":
        ident = (request.form.get("ident") or "").strip()
        person = find_for_reset(ident)
        if person:
            token = issue_reset(person)
            if token:
                send_reset(person, token)
        flash(SAME_MSG, "ok")
        return redirect("/forgot")
    return render_template("forgot.html")


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    from app.services.passwords import consume_reset, user_for_token

    person = user_for_token(token)
    if not person:
        flash("That reset link is used up or expired.", "warn")
        return redirect("/forgot")
    if request.method == "POST":
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        if len(password) < 8:
            flash("Password needs at least 8 characters.", "warn")
            return render_template("reset.html", token=token)
        if password != confirm:
            flash("Those passwords do not match.", "warn")
            return render_template("reset.html", token=token)
        consume_reset(person, password)
        flash("Password updated. Sign in with it.", "ok")
        return redirect("/login")
    return render_template("reset.html", token=token)
