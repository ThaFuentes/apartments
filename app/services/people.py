"""Usernames and passwords. Email is optional and stored only when she gives one."""
from __future__ import annotations

import re
import secrets

from werkzeug.security import check_password_hash, generate_password_hash

from app.builddb.builddb import db
from app.models import AssistantProfile, User
from app.services.clock import utcnow
from app.services.roles import BUILTIN_ROLES, known_role, normalize_role as normalize_job

USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,79}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE = re.compile(r"^\+?[0-9][0-9 ().-]{6,24}$")
ROLES = BUILTIN_ROLES + ("field", "employee", "boss")


def clean_email(value) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if not EMAIL.match(text):
        raise ValueError("That email does not look usable. Leave it blank if they have none.")
    return text.lower()


def clean_phone(value) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if not PHONE.match(text):
        raise ValueError("That phone number does not look usable. Leave it blank if there is none.")
    return text[:40]


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


def suggest_username(display_name: str) -> str:
    """A login from a person's name: Dana Desk → dana.desk, then dana.desk2 if taken."""
    text = re.sub(r"[^a-z0-9]+", ".", (display_name or "").strip().lower()).strip(".")
    if len(text) < 2:
        text = "staff"
    base = text[:70]
    ident = base
    n = 2
    while find_user(ident):
        ident = f"{base}{n}"[:80]
        n += 1
        if n > 80:
            ident = f"{base}.{secrets.token_hex(2)}"[:80]
            break
    return ident


def find_user(username: str) -> User | None:
    text = (username or "").strip()
    if not text:
        return None
    return User.query.filter(db.func.lower(User.username) == text.lower()).first()


def find_person(query: str) -> User | None:
    """Find a login the way she says a person: 'tiffany', 'Tiffany Doe', the username.

    Exact username first. Then a display-name match on every word. One hit wins.
    Two or more hits return None so the caller can ask which one.
    """
    text = (query or "").strip()
    if not text:
        return None
    exact = find_user(text)
    if exact:
        return exact
    words = [word for word in re.split(r"\s+", text.lower()) if word]
    if not words:
        return None
    hits = []
    for person in User.query.order_by(User.id.asc()).all():
        blob = f"{person.display_name or ''} {person.username}".lower()
        if all(word in blob for word in words):
            hits.append(person)
    return hits[0] if len(hits) == 1 else None


def create_user(
    *,
    username: str,
    password: str,
    display_name: str = "",
    role: str = "viewer",
    email=None,
    phone: str = "",
    created_by: User | None = None,
    can_see_reports: bool = True,
    can_see_history: bool = True,
    can_see_live_map: bool = False,
    capability_overrides: dict[str, bool] | None = None,
    active: bool = True,
    is_bot: bool = False,
    security_email=None,
    reset_email=None,
) -> tuple[User, str]:
    role = normalize_job(role or "viewer")
    if not known_role(role) and role not in ROLES:
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
    security = clean_email(security_email)
    reset = clean_email(reset_email)
    # The account owner is reachable for recovery and billing; every other role
    # may legitimately have no email ("no email" in the chat).
    if role == "owner" and not mail:
        raise ValueError("An owner login needs an email address.")
    if is_bot and role == "owner":
        raise ValueError("The owner login cannot be a bot.")
    digits = clean_phone(phone)
    user = User(
        username=ident,
        display_name=(display_name or ident).strip()[:150],
        email=mail,
        security_email=security,
        reset_email=reset,
        is_bot=bool(is_bot),
        phone=digits,
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


def try_login(username: str, password: str) -> tuple[User | None, str]:
    """Return the user on success, or (None, message) without extending a lock."""
    user = find_user(username)
    if not user:
        return None, "That username and password did not match."
    if not user.active:
        return None, "That login is turned off. An owner or supervisor can turn it back on from People."
    if user.locked_until and user.locked_until > utcnow():
        return None, "That login is locked. An owner or supervisor can unlock it from People."
    if not check_password_hash(user.password_hash, password or ""):
        note_login(user, False)
        if user.locked_until and user.locked_until > utcnow():
            return None, "That login is locked after too many tries. An owner or supervisor can unlock it from People."
        return None, "That username and password did not match."
    return user, ""


def unlock_login(person: User) -> None:
    person.active = True
    person.failed_login_attempts = 0
    person.locked_until = None


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
