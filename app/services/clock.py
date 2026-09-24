from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_TZ = "America/Chicago"
WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo((name or "").strip() or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def local_now(tz_name: str | None = None) -> datetime:
    return datetime.now(zone(tz_name))


def local_today(tz_name: str | None = None):
    return local_now(tz_name).date()


def as_utc_naive(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def next_named_day(name: str, today):
    key = (name or "").strip().lower()
    if key == "today":
        return today
    if key == "tomorrow":
        return today + timedelta(days=1)
    if key not in WEEKDAYS:
        return today
    target = WEEKDAYS.index(key)
    delta = (target - today.weekday()) % 7
    return today + timedelta(days=delta)


def week_bounds(day):
    start = day - timedelta(days=day.weekday())
    end = start + timedelta(days=6)
    return start, end


def money(cents) -> str:
    n = int(cents or 0)
    sign = "-" if n < 0 else ""
    n = abs(n)
    return f"{sign}${n // 100:,}.{n % 100:02d}"
