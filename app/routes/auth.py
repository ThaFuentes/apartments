"""Login and invite routes."""
from __future__ import annotations

from flask import flash, redirect, render_template, request
from flask_login import current_user
from werkzeug.security import generate_password_hash

from app.auth import attempt, home_for, login_person, logout_person, needs_setup, safe_next
from app.builddb.builddb import db
from app.models import User
from app.services.clock import utcnow
from app.services.people import create_user
from app.routes.common import bp


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
