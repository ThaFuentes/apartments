"""Plain SMTP for sign-in codes and password-reset links."""
from __future__ import annotations


def mail_config() -> tuple[str, int, str, str, str]:
    import os

    from app.services.records import site_profile

    profile = site_profile()
    host = ((getattr(profile, "smtp_host", None) or "") or os.getenv("SMTP_HOST") or "").strip()
    try:
        port = int(getattr(profile, "smtp_port", None) or os.getenv("SMTP_PORT") or 587)
    except (TypeError, ValueError):
        port = 587
    user_name = ((getattr(profile, "smtp_user", None) or "") or os.getenv("SMTP_USER") or "").strip()
    sender = ((getattr(profile, "smtp_from", None) or "") or os.getenv("SMTP_FROM") or user_name or "apt@poweredby.top").strip()
    password = ""
    cipher = getattr(profile, "smtp_password_ciphertext", None) if profile else None
    if cipher:
        from app.services.crypto import decrypt_text

        try:
            password = decrypt_text(cipher)
        except Exception:
            password = ""
    if not password:
        password = os.getenv("SMTP_PASSWORD") or ""
    return host, port, user_name, password, sender


def send_text(address: str, subject: str, body: str) -> tuple[bool, str]:
    import smtplib
    from email.message import EmailMessage

    host, port, user_name, password, sender = mail_config()
    inbox = (address or "").strip()
    if not host:
        return False, "Mail is not set up yet. Add SMTP in Settings."
    if not inbox or "@" not in inbox:
        return False, "That inbox is missing."
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = inbox
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.starttls()
            if user_name:
                smtp.login(user_name, password)
            smtp.send_message(msg)
        return True, f"Sent to {inbox}."
    except Exception as exc:
        return False, str(exc) or "The mail server did not take that message."
