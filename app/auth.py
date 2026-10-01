from __future__ import annotations

from flask import redirect, request, session
from flask_login import LoginManager, current_user, login_user, logout_user

from app.builddb.builddb import db
from app.models import User
from app.services.people import note_login

login_manager = LoginManager()
login_manager.login_view = "desk.login"
login_manager.session_protection = "basic"


@login_manager.unauthorized_handler
def _login_needed():
    from flask import url_for

    return redirect(url_for("desk.login", next=return_path()))


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
    session["apt_bot"] = bool(getattr(user, "is_bot", False))
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
    session.pop("apt_bot", None)


def attempt(username: str, password: str) -> User | None:
    from app.services.people import try_login

    user, _reason = try_login(username, password)
    return user


def sanitize_next(nxt: str, default: str = "/") -> str:
    """Same-site relative path only: starts with / and not // or /\\."""
    raw = (nxt or "").strip()
    if not raw.startswith("/") or raw.startswith("//") or raw.startswith("/\\"):
        return default
    if "\\" in raw or "://" in raw:
        return default
    if any(ord(ch) < 32 for ch in raw):
        return default
    return raw


def safe_next(default: str = "/") -> str:
    raw = request.values.get("next")
    if raw is None or str(raw).strip() == "":
        return default
    return sanitize_next(str(raw), default)


def return_path() -> str:
    """Current request path plus query string, safe to stash as login next."""
    raw = request.full_path or request.path or "/"
    if raw.endswith("?"):
        raw = request.path or "/"
    return sanitize_next(raw, "/")


def home_for(user) -> str:
    from app.services.twofa import bot_setup_remaining

    if bot_setup_remaining(user):
        return "/bot-setup"
    if getattr(user, "role", "") == "viewer":
        return "/reports"
    return "/"
