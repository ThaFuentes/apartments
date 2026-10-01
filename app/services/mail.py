"""Plain SMTP for sign-in codes, password-reset links, and reports."""
from __future__ import annotations

import ssl

INBOUND_MAIL_PORTS = frozenset({110, 143, 993, 995})


def normalize_smtp_port(port) -> tuple[int, str]:
    """465 is implicit SSL. 587 is STARTTLS. IMAP/POP ports are not SMTP."""
    try:
        port = int(port or 587)
    except (TypeError, ValueError):
        port = 587
    if port in INBOUND_MAIL_PORTS:
        return port, "inbound"
    if port == 465:
        return port, "ssl"
    return port, "tls"


def explain_smtp_failure(exc, port: int) -> str:
    text = str(exc or "")
    lowered = text.lower()
    if port in INBOUND_MAIL_PORTS or "dovecot" in lowered:
        return (
            "That host answered as a mailbox (IMAP/POP3), not SMTP. "
            "Use smtp.hostinger.com port 465, with the full email as the username."
        )
    if "timed out" in lowered or "unexpectedly closed" in lowered:
        return (
            "The mail server did not answer in time. "
            "On this host use smtp.hostinger.com port 465 with the full mailbox address as username. "
            "Gmail from this server often times out."
        )
    return text or "The mail server did not take that message."


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


def send_message(msg) -> tuple[bool, str]:
    import smtplib

    host, port, user_name, password, sender = mail_config()
    inbox = str(msg["To"] or "").strip()
    if not host:
        return False, "Mail is not set up yet. Add SMTP in Settings."
    if not inbox or "@" not in inbox:
        return False, "That inbox is missing."
    if not msg["From"]:
        msg["From"] = sender
    port, enc = normalize_smtp_port(port)
    if enc == "inbound":
        return False, (
            f"Port {port} is for reading mail, not sending. "
            "Use smtp.hostinger.com port 465, with the full email as the username."
        )
    try:
        context = ssl.create_default_context()
        if enc == "ssl":
            smtp = smtplib.SMTP_SSL(host, port, timeout=20, context=context)
        else:
            smtp = smtplib.SMTP(host, port, timeout=20)
        try:
            smtp.ehlo()
            if enc == "tls":
                smtp.starttls(context=context)
                smtp.ehlo()
            if user_name:
                smtp.login(user_name, password)
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except Exception:
                pass
        return True, f"Sent to {inbox}."
    except Exception as exc:
        return False, explain_smtp_failure(exc, port)


def send_text(address: str, subject: str, body: str) -> tuple[bool, str]:
    from email.message import EmailMessage

    host, _port, _user_name, _password, sender = mail_config()
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
    return send_message(msg)


def send_test(address: str) -> tuple[bool, str]:
    return send_text(
        address,
        "Apt mail test",
        "This is a test from Apt. If you got it, the mailbox in Settings is working.\n",
    )
