"""Shared blueprint and access helpers for Apt route modules."""
from __future__ import annotations

import secrets
from functools import wraps

from flask import Blueprint, abort, redirect, request, url_for
from flask_login import current_user

bp = Blueprint("desk", __name__)


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not getattr(current_user, "is_authenticated", False):
            return redirect(url_for("desk.login", next=request.path))
        return fn(*args, **kwargs)

    return wrapped


def owner_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not getattr(current_user, "is_authenticated", False):
            return redirect(url_for("desk.login", next=request.path))
        if current_user.role != "owner":
            abort(403)
        return fn(*args, **kwargs)

    return wrapped


def people_admin(fn):
    """Owner, admin, and anyone who hires people may work the people page."""

    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not getattr(current_user, "is_authenticated", False):
            return redirect(url_for("desk.login", next=request.path))
        from app.services.access.management import can_open_people_page

        if not can_open_people_page(current_user):
            abort(403)
        return fn(*args, **kwargs)

    return wrapped


def _key() -> str:
    return (request.form.get("idempotency_key") or request.headers.get("X-Idempotency-Key") or "").strip()[:120]


def _new_key() -> str:
    return secrets.token_hex(16)


def _history_ok() -> bool:
    if not current_user.is_viewer:
        return True
    return bool(current_user.can_see_history)


def _reports_ok() -> bool:
    if not current_user.is_viewer:
        return True
    return bool(current_user.can_see_reports)
