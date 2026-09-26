"""Usernames and passwords. Email is optional and stored only when she gives one."""
from __future__ import annotations

import re
import secrets

from werkzeug.security import check_password_hash, generate_password_hash

from app.builddb.builddb import db
from app.models import AssistantProfile, User
from app.services.clock import utcnow

USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,79}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ROLES = (
    "owner",
    "admin",
    "regional_manager",
    "property_manager",
    "assistant_manager",
    "office",
    "maintenance_manager",
    "maintenance_person",
    # Keep legacy values accepted for existing invitations and stored accounts.
    "field",
    "viewer",
)


def clean_email(value) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if not EMAIL.match(text):
        raise ValueError("That email does not look usable. Leave it blank if they have none.")
    return text.lower()


def clean_username(value) -> str:
    text = (value or "").strip()
    if not USERNAME.match(text):
        raise ValueError("Username needs 2–80 letters, numbers, dots, or dashes. No spaces. No email required.")
    return text


def person_label(user_id: int | None) -> str:
    if not user_id:
        return ""
    person = db.session.get(User, int(user_id))
    if not person:
        return ""
    return (person.display_name or person.username or "").strip()


def find_user(username: str) -> User | None:
    text = (username or "").strip()
    if not text:
        return None
    return User.query.filter(db.func.lower(User.username) == text.lower()).first()


def create_user(
    *,
    username: str,
    password: str,
    display_name: str = "",
    role: str = "viewer",
    email=None,
    created_by: User | None = None,
    can_see_reports: bool = True,
    can_see_history: bool = True,
    can_see_live_map: bool = False,
    capability_overrides: dict[str, bool] | None = None,
    active: bool = True,
) -> tuple[User, str]:
    role = (role or "viewer").strip().lower()
    if role not in ROLES:
        raise ValueError("Choose a valid Apt role.")
    if created_by is not None:
        from app.services.access import can_create_user

        if not can_create_user(created_by, role):
            raise ValueError("This login cannot add that role or scope.")
    ident = clean_username(username)
    if find_user(ident):
        raise ValueError(f"{ident} already has a login.")
    password = password or ""
    generated = ""
    if len(password) < 8:
        if password:
            raise ValueError("Password needs at least 8 characters.")
        generated = secrets.token_urlsafe(9)
        password = generated
    mail = clean_email(email)
    if User.query.count() == 0 and mail is None:
        raise ValueError("The first login needs an email.")
    user = User(
        username=ident,
        display_name=(display_name or ident).strip()[:150],
        email=mail,
        password_hash=generate_password_hash(password),
        role=role,
        active=bool(active),
        can_see_reports=bool(can_see_reports) if role in ("viewer", "office") else True,
        can_see_history=bool(can_see_history) if role in ("viewer", "office") else True,
        can_see_live_map=bool(can_see_live_map) if role in ("viewer", "office") else False,
        created_by_id=created_by.id if created_by else None,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.session.add(user)
    db.session.flush()
    if capability_overrides:
        from app.models import UserCapability
        from app.services.access import CAPABILITIES

        unknown = set(capability_overrides) - CAPABILITIES
        if unknown:
            raise ValueError("One or more selected permissions are invalid.")
        for capability, granted in capability_overrides.items():
            db.session.add(UserCapability(
                user_id=user.id,
                capability=capability,
                granted=bool(granted),
                changed_by_id=created_by.id if created_by else None,
                created_at=utcnow(),
            ))
    if role == "owner" and AssistantProfile.query.filter_by(user_id=user.id).first() is None:
        db.session.add(AssistantProfile(user_id=user.id))
    return user, generated


def check_password(user: User, password: str) -> bool:
    if not user or not user.active:
        return False
    if user.locked_until and user.locked_until > utcnow():
        return False
    return check_password_hash(user.password_hash, password or "")


def note_login(user: User, ok: bool) -> None:
    if ok:
        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login_at = utcnow()
    else:
        user.failed_login_attempts = int(user.failed_login_attempts or 0) + 1
        if user.failed_login_attempts >= 8:
            from datetime import timedelta

            user.locked_until = utcnow() + timedelta(minutes=15)
    db.session.commit()
