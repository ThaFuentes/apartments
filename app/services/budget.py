"""A self-imposed token budget per AI key.

A free key meters tokens in a window and answers a burst with a 429 and a
multi-minute lockout, which leaves the chat without an answer. We would rather
rest the key ourselves, try the next one, and let local chat take over when
every key is resting.

The window is rolling: only spend from the last ``burst_seconds`` counts.
``burst_tokens`` of 0 turns the limiter off for that key. The per-request
``max_reply_tokens`` cap keeps one reply from eating the whole window; 0 means
the provider client's own default.
"""
from __future__ import annotations

from datetime import timedelta

from app.builddb.builddb import db
from app.models import ApiUsage
from app.services.clock import utcnow

DEFAULT_BURST_TOKENS = 5000
DEFAULT_BURST_SECONDS = 180
# Used to reserve room for a reply when a key has no explicit per-request cap.
_ASSUMED_REPLY = 1000
# Rough cost of one nameplate photo, for providers that omit usage numbers.
IMAGE_TOKENS = 750


def burst_tokens(row) -> int:
    try:
        return max(0, int(getattr(row, "burst_tokens", 0) or 0))
    except (TypeError, ValueError):
        return 0


def burst_seconds(row) -> int:
    try:
        value = int(getattr(row, "burst_seconds", 0) or 0)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else DEFAULT_BURST_SECONDS


def reply_cap(row) -> int:
    """Per-request output cap. 0 leaves the provider client's default in place."""
    try:
        return max(0, int(getattr(row, "max_reply_tokens", 0) or 0))
    except (TypeError, ValueError):
        return 0


def enabled(row) -> bool:
    return bool(getattr(row, "id", None)) and burst_tokens(row) > 0


def estimate_tokens(text: str) -> int:
    """Rough English estimate; only used to reserve window room before a call."""
    return max(1, (len(text or "") + 3) // 4)


def spent(row, window: int | None = None) -> int:
    """Total tokens this key spent inside the rolling window."""
    if not getattr(row, "id", None):
        return 0
    seconds = window or burst_seconds(row)
    since = utcnow() - timedelta(seconds=seconds)
    total = (
        db.session.query(db.func.coalesce(db.func.sum(ApiUsage.tokens), 0))
        .filter(ApiUsage.credential_id == row.id, ApiUsage.created_at >= since)
        .scalar()
    )
    return int(total or 0)


def resting_seconds(row) -> int:
    """Seconds until the key's oldest spend ages out of the window, else 0."""
    if not enabled(row):
        return 0
    window = burst_seconds(row)
    if spent(row, window) < burst_tokens(row):
        return 0
    since = utcnow() - timedelta(seconds=window)
    oldest = (
        db.session.query(db.func.min(ApiUsage.created_at))
        .filter(ApiUsage.credential_id == row.id, ApiUsage.created_at >= since)
        .scalar()
    )
    if not oldest:
        return window
    free_at = oldest + timedelta(seconds=window)
    return max(1, int((free_at - utcnow()).total_seconds()))


def would_exceed(row, prompt: str, reply: int = 0) -> bool:
    """True when this call would push the key past its window.

    A key with nothing spent in the window always gets one call, so a prompt
    larger than the whole budget can never deadlock the chat.
    """
    if not enabled(row):
        return False
    used = spent(row)
    if used <= 0:
        return False
    reserve = estimate_tokens(prompt) + (reply or reply_cap(row) or _ASSUMED_REPLY)
    return used + reserve > burst_tokens(row)


def record(row, tokens: int) -> None:
    """Log what a call actually cost. No-op for a key with no saved row."""
    if not getattr(row, "id", None):
        return
    db.session.add(
        ApiUsage(
            credential_id=row.id,
            tokens=max(0, int(tokens or 0)),
            created_at=utcnow(),
        )
    )
    _prune(row)
    db.session.commit()


def _prune(row) -> None:
    """Keep only spend that can still matter to the window."""
    cutoff = utcnow() - timedelta(seconds=max(burst_seconds(row), 3600))
    ApiUsage.query.filter(
        ApiUsage.credential_id == row.id,
        ApiUsage.created_at < cutoff,
    ).delete(synchronize_session=False)
