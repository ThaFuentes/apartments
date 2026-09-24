"""Reminders that show up on her next page, not a separate inbox product."""
from __future__ import annotations

from datetime import timedelta

from app.builddb.builddb import db
from app.models import Expense, Notice, PendingAction, Report, Shift
from app.services.clock import local_today, utcnow, week_bounds
from app.services.records import open_shift, site_profile


def _open(user_id: int, kind: str) -> Notice | None:
    return Notice.query.filter_by(user_id=user_id, kind=kind, read_at=None).order_by(Notice.id.desc()).first()


def _ensure(user_id: int, kind: str, body: str, href: str) -> None:
    row = _open(user_id, kind)
    if row:
        if row.body != body:
            row.body = body
            row.href = href
        return
    db.session.add(Notice(user_id=user_id, kind=kind, body=body, href=href, created_at=utcnow()))


def _clear(user_id: int, kind: str) -> None:
    for row in Notice.query.filter_by(user_id=user_id, kind=kind, read_at=None).all():
        row.read_at = utcnow()


def refresh_notices(user) -> None:
    if not user or not getattr(user, "is_authenticated", False):
        return
    if user.role == "viewer":
        return
    now = utcnow()
    old = (
        PendingAction.query.filter_by(user_id=user.id, tool="log_expense", status="pending")
        .filter(PendingAction.created_at < now - timedelta(hours=48))
        .count()
    )
    stale_money = Expense.query.filter(
        Expense.user_id == user.id,
        Expense.deleted_at.is_(None),
        Expense.status != "confirmed",
        Expense.created_at < now - timedelta(hours=48),
    ).count()
    if old or stale_money:
        _ensure(user.id, "expense_old", f"{old + stale_money} expense(s) have been waiting more than 48 hours.", "/expenses")
    else:
        _clear(user.id, "expense_old")

    profile = site_profile()
    today = local_today(profile.timezone if profile else None)
    start, end = week_bounds(today)
    have = Report.query.filter(
        Report.kind.in_(("weekly", "company")),
        Report.period_start == start,
        Report.deleted_at.is_(None),
        Report.status.in_(("ready", "sent")),
    ).first()
    if today.weekday() >= 4 and not have:
        _ensure(user.id, "weekly_due", "The weekly company report is not saved for this week yet.", "/reports")
    else:
        _clear(user.id, "weekly_due")

    shift = open_shift(user)
    if shift and shift.sharing_on:
        _ensure(user.id, "share_on", "Location sharing is on. It turns itself off if you leave or the day rolls over.", "/map")
    else:
        _clear(user.id, "share_on")
    if shift and not shift.ended_at and shift.started_at and shift.started_at < now - timedelta(hours=8):
        _ensure(user.id, "end_visit", "This visit has been open for hours. End it when you leave.", "/")
    else:
        _clear(user.id, "end_visit")
    db.session.commit()
