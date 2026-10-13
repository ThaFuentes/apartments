"""One idempotency key, one write. A retry returns the first result."""
from __future__ import annotations

from app.builddb.builddb import db


def finish_once(user, key: str, tool: str, result: dict) -> dict:
    """Commit the open changes under this key. The same key does not write again."""
    from app.services.pending import _prior, _remember

    key = (key or "").strip()[:120]
    if not key:
        db.session.commit()
        return result
    prior = _prior(user.id, key)
    if prior:
        db.session.rollback()
        return prior
    return _remember(user.id, key, tool, result)
