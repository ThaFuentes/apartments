from __future__ import annotations

from flask import redirect, request, session
from flask_login import LoginManager, current_user, login_user, logout_user

from app.builddb.builddb import db
from app.models import User
from app.services.people import check_password, find_user, note_login

login_manager = LoginManager()
login_manager.login_view = "desk.login"
login_manager.session_protection = "basic"


@login_manager.user_loader
def load_user(user_id):
    try:
        return db_get(int(user_id))
    except (TypeError, ValueError):
        return None


def db_get(user_id: int) -> User | None:
    return db.session.get(User, user_id)


def needs_setup() -> bool:
    return User.query.count() == 0


def login_person(user: User) -> None:
    login_user(user, remember=True)
    session.permanent = True
    session["apt_role"] = user.role
    try:
        from poweredbytop.auth.session import bind_login_session

        bind_login_session(user)
    except Exception:
        pass
    try:
        from poweredbytop.security.csrf import rotate_csrf_token

        rotate_csrf_token()
    except Exception:
        pass
    note_login(user, True)


def logout_person() -> None:
    try:
        from poweredbytop.auth.session import clear_login_bind

        clear_login_bind()
    except Exception:
        pass
    try:
        logout_user()
    except Exception:
        pass
    session.pop("apt_role", None)


def attempt(username: str, password: str) -> User | None:
    user = find_user(username)
    if not user or not check_password(user, password):
        if user:
            note_login(user, False)
        return None
    return user


def safe_next(default: str = "/") -> str:
    nxt = request.values.get("next") or default
    nxt = str(nxt)
    if not nxt.startswith("/") or nxt.startswith("//"):
        return default
    return nxt


def home_for(user) -> str:
    if getattr(user, "role", "") == "viewer":
        return "/reports"
    return "/"
