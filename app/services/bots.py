"""Bot logins: their own key, and the words on the history line."""
from __future__ import annotations

KEY_MAIL_SUBJECT = "Apt API key changed"


def is_bot_user(user) -> bool:
    return bool(user and getattr(user, "is_bot", False) and getattr(user, "role", "") != "owner")


def runs_platform(user) -> bool:
    """A real owner, or a finished active bot, runs the apartment platform.

    An unfinished bot still has to set two different addresses and turn on 2FA.
    This does not let a bot create, turn off, transfer, or delete the owner login.
    """
    if not user:
        return False
    if not bool(getattr(user, "is_bot", False)):
        return getattr(user, "role", "") == "owner"
    if not getattr(user, "active", True):
        return False
    from app.services.twofa import bot_setup_remaining

    return not bot_setup_remaining(user)


def history_who(actor_id, source: str = "") -> str:
    """History names a bot as a bot agent. A person stays their name."""
    from app.builddb.builddb import db
    from app.models import User

    person = None
    if actor_id:
        try:
            person = db.session.get(User, int(actor_id))
        except (TypeError, ValueError):
            person = None
    name = ""
    if person is not None:
        name = (person.display_name or person.username or "").strip()
    if is_bot_user(person) or (source or "") == "bot":
        return f"bot agent {name}".strip()
    return name or "Someone"


def replace_own_key(user, provider: str, raw: str, model: str = "", base_url: str = "") -> dict:
    """Check a new key, retire the old one for that provider, and mail the last 4."""
    from app.models import ApiCredential
    from app.services.clock import utcnow
    from app.services.crypto import encrypt_text, last4
    from app.services.mail import send_text
    from app.services.providers import PROVIDERS, check_key
    from app.services.records import audit
    from app.services.twofa import bot_setup_remaining, key_inbox_for
    from app.builddb.builddb import db

    if not is_bot_user(user):
        return {"ok": False, "error": "Only a bot can replace its key here."}
    gaps = bot_setup_remaining(user)
    if gaps:
        return {"ok": False, "error": "Finish bot setup first.", "gaps": gaps}
    provider = (provider or "gemini").strip().lower()
    raw = (raw or "").strip()
    if provider not in PROVIDERS:
        return {"ok": False, "error": "Pick a provider."}
    if len(raw) < 8:
        return {"ok": False, "error": "Paste the API key."}
    inbox = key_inbox_for(user)
    if not inbox:
        return {"ok": False, "error": "This bot has no API key address."}
    checked = check_key(provider, raw, model, base_url)
    if not checked.get("ok"):
        return {"ok": False, "error": checked.get("error") or "That key did not answer."}
    rows = (
        ApiCredential.query.filter_by(user_id=user.id, provider=provider)
        .order_by(ApiCredential.use_order.asc(), ApiCredential.id.asc())
        .all()
    )
    previous = rows[0].last4 if rows else ""
    new_last4 = last4(raw)
    model_id = (checked.get("model") or model or "")[:120]
    if rows:
        row = rows[0]
        row.secret_ciphertext = encrypt_text(raw)
        row.last4 = new_last4
        row.model_id = model_id or row.model_id
        row.base_url = (base_url or "").strip()[:300] or row.base_url
        row.active = True
        row.preferred = True
        row.use_order = 1
        row.backoff_until = None
        row.model_checked_at = utcnow()
        for other in rows[1:]:
            other.active = False
            other.preferred = False
    else:
        row = ApiCredential(
            user_id=user.id,
            provider=provider,
            secret_ciphertext=encrypt_text(raw),
            last4=new_last4,
            model_id=model_id,
            base_url=(base_url or "").strip()[:300] or None,
            active=True,
            preferred=True,
            use_order=1,
            shared=False,
            model_checked_at=utcnow(),
            created_at=utcnow(),
        )
        db.session.add(row)
    db.session.flush()
    who = (user.display_name or user.username or "bot").strip()
    audit(
        user.id,
        "bot",
        "update",
        "api_credential",
        row.id if getattr(row, "id", None) else None,
        {"provider": provider, "last4": previous},
        {"provider": provider, "last4": new_last4, "model": row.model_id or ""},
    )
    db.session.commit()
    body = (
        f"{who} replaced the {PROVIDERS[provider]['label']} key on this bot login.\n\n"
        f"The new key ends in {new_last4}.\n"
        "The full key is not in this email.\n"
    )
    mailed, mail_note = send_text(inbox, KEY_MAIL_SUBJECT, body)
    reply = f"Key saved. It ends in {new_last4}."
    if mailed:
        reply += f" A note went to {inbox}."
    else:
        reply += f" The note to {inbox} did not send. {mail_note}"
    return {
        "ok": True,
        "reply": reply,
        "last4": new_last4,
        "provider": provider,
        "mailed": bool(mailed),
        "inbox": inbox,
    }
