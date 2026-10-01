"""Password reset links. They go to the reset inbox, which can differ from login and 2FA."""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from werkzeug.security import generate_password_hash

from app.builddb.builddb import db
from app.models import User
from app.services.clock import utcnow
from app.services.twofa import reset_inbox_for

TTL_HOURS = 2
SAME_MSG = "If that login has a reset inbox, a link is on the way."


def _hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def find_for_reset(ident: str) -> User | None:
    text = (ident or "").strip()
    if not text:
        return None
    from sqlalchemy import func

    person = User.query.filter(func.lower(User.username) == text.lower()).first()
    if person:
        return person
    if "@" not in text:
        return None
    addr = text.lower()
    return (
        User.query.filter(
            (func.lower(User.reset_email) == addr)
            | (func.lower(User.email) == addr)
            | (func.lower(User.security_email) == addr)
        )
        .order_by(User.id.asc())
        .first()
    )


def issue_reset(user: User) -> str | None:
    if not user or not user.active:
        return None
    if not (reset_inbox_for(user) or "").strip():
        return None
    raw = secrets.token_urlsafe(32)
    user.reset_token_hash = _hash(raw)
    user.reset_token_expires = utcnow() + timedelta(hours=TTL_HOURS)
    db.session.commit()
    return raw


def send_reset(user: User, token: str) -> tuple[bool, str]:
    from flask import url_for

    from app.services.mail import send_text

    inbox = reset_inbox_for(user)
    if not inbox:
        return False, "No reset inbox on this login."
    link = url_for("desk.reset_password", token=token, _external=True)
    who = user.display_name or user.username
    bot_line = (
        "This is a bot account: the new password takes effect on the next sign-in.\n"
        if bool(getattr(user, "is_bot", False))
        else ""
    )
    body = (
        f"Hi {who},\n\n"
        "Someone asked to reset your Apt password. Use this link:\n\n"
        f"{link}\n\n"
        f"It expires in {TTL_HOURS} hours. If you did not ask for this, ignore the email.\n"
        f"{bot_line}"
    )
    return send_text(inbox, "Reset your Apt password", body)


def user_for_token(token: str) -> User | None:
    raw = (token or "").strip()
    if not raw:
        return None
    person = User.query.filter_by(reset_token_hash=_hash(raw)).first()
    if not person or not person.active:
        return None
    if not person.reset_token_expires or person.reset_token_expires < utcnow():
        return None
    return person


def consume_reset(user: User, password: str) -> None:
    user.password_hash = generate_password_hash(password)
    user.reset_token_hash = None
    user.reset_token_expires = None
    user.failed_login_attempts = 0
    user.locked_until = None
    db.session.commit()
