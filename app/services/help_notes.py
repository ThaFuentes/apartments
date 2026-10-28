"""Help notes the owner or a bot pipes onto the home page."""
from __future__ import annotations

import hashlib
import re
import unicodedata

from app.builddb.builddb import db
from app.models import HelpNote
from app.services.clock import utcnow
from app.services.records import audit

_SLUG = re.compile(r"[^a-z0-9]+")
_HEADING = re.compile(r"^#{1,6}\s+(.+)$")
_INVISIBLE = frozenset("\u200b\u200c\u200d\ufeff\u2060")
MAX_NOTES = 100
_REPLY_LIST = 8


def slug_for(title: str, given: str = "") -> str:
    """Stable slug. A long or mixed title must not land on a shorter note's slug."""
    title_basis = unicodedata.normalize("NFC", (title or "").strip().lower())
    given_basis = unicodedata.normalize("NFC", (given or "").strip().lower())
    basis = given_basis or title_basis
    full = _ascii_slug(basis)
    if not full and given_basis:
        basis = title_basis
        full = _ascii_slug(basis)
    if full:
        return _fit_slug(full, basis)
    if not basis:
        return ""
    return "note-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _ascii_slug(basis: str) -> str:
    return _SLUG.sub("-", basis).strip("-")


def _fit_slug(full: str, basis: str) -> str:
    mixed = any(ord(ch) > 127 for ch in basis)
    if len(full) <= 80 and not mixed:
        return full
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
    head = full if len(full) <= 63 else (full[:63].strip("-") or "note")
    return head.strip("-") + "-" + digest


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("help text")
    return str(value)


def _clean(text: str, limit: int, *, one_line: bool = False) -> str:
    """Drop characters a note should not store. Newlines stay in the body."""
    keep = []
    for ch in text or "":
        if ch == "\r":
            continue
        if ch in _INVISIBLE or ch in "\x00\x7f" or 0x80 <= ord(ch) <= 0x9f:
            continue
        if ch in "\n\t" or ord(ch) >= 32:
            keep.append(" " if one_line and ch in "\n\t" else ch)
    cleaned = unicodedata.normalize("NFC", "".join(keep).strip())
    if one_line:
        cleaned = " ".join(cleaned.split())
    return cleaned[:limit]


def _heading_text(line: str) -> str:
    """A heading may be indented up to three spaces. Four spaces stay in the note."""
    spaces = len(line) - len(line.lstrip(" "))
    if spaces > 3:
        return ""
    found = _HEADING.match(line[spaces:])
    return found.group(1).strip() if found else ""


def parse_pipe_text(text: str) -> list[dict]:
    """A piped file. A heading is `# Title` after a blank line. Hashes inside a paragraph stay in the note."""
    notes: list[dict] = []
    title = ""
    lines: list[str] = []
    prev_blank = True

    def flush() -> None:
        nonlocal title, lines
        if title:
            notes.append({"title": title, "body": "\n".join(lines).strip()})
        title = ""
        lines = []

    normalized = (text or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    for line in normalized.split("\n"):
        heading = _heading_text(line) if prev_blank else ""
        if heading:
            flush()
            title = heading
            prev_blank = False
            continue
        if title:
            lines.append(line)
        prev_blank = not line.strip()
    flush()
    return notes


def items_from_payload(data) -> tuple[list[dict], bool]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)], False
    if not isinstance(data, dict):
        return [], False
    replace = _flag(data.get("replace"), False)
    rows = data.get("notes") if data.get("notes") is not None else data.get("items")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)], replace
    if data.get("title") or data.get("body") or data.get("slug"):
        return [data], replace
    return [], replace


def can_post_help(user) -> bool:
    """Owner, a granted settings login, or a bot. A turned-off login cannot post."""
    if not user or not getattr(user, "active", False):
        return False
    from app.services.access import can_manage_company_settings

    return bool(can_manage_company_settings(user))


def active_notes() -> list:
    return (
        HelpNote.query.filter_by(active=True)
        .order_by(HelpNote.sort_order.asc(), HelpNote.title.asc(), HelpNote.id.asc())
        .all()
    )


def _sort_value(value) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, min(number, 100000))


def _flag(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(value)


def _snap(row) -> dict:
    if row is None:
        return {}
    return {
        "slug": row.slug or "",
        "title": row.title or "",
        "body": row.body or "",
        "active": bool(row.active),
        "sort": int(row.sort_order or 0),
    }


def _next_sort() -> int:
    current = db.session.query(db.func.max(HelpNote.sort_order)).scalar()
    return int(current or 0) + 10


def pipe_notes(user, items: list[dict], *, replace: bool = False) -> dict:
    """Upsert help notes. The same slug updates the old note instead of adding another."""
    if not can_post_help(user):
        return {"ok": False, "error": "Only the owner or a bot can post help notes."}
    if not items:
        return {"ok": False, "error": "Send at least one help note. Start each one with a # title."}
    if len(items) > MAX_NOTES:
        return {"ok": False, "error": "Send at most 100 help notes at once."}
    batch = len(items) > 1
    saved = []
    seen = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            db.session.rollback()
            return {"ok": False, "error": "Each note needs a title."}
        try:
            title = _clean(_as_text(item.get("title")), 160, one_line=True)
            body = _clean(_as_text(item.get("body")), 8000)
            given_slug = _clean(_as_text(item.get("slug")), 200, one_line=True)
        except TypeError:
            db.session.rollback()
            return {"ok": False, "error": "Each title, slug, and the instructions have to be text."}
        slug = slug_for(title, given_slug)
        if not title or not slug:
            db.session.rollback()
            return {"ok": False, "error": "Each note needs a title."}
        active = _flag(item.get("active"), True) and bool(body)
        row = HelpNote.query.filter_by(slug=slug).first()
        before = _snap(row) if row is not None else {}
        if row is None:
            row = HelpNote(slug=slug, sort_order=_next_sort(), updated_at=utcnow())
            db.session.add(row)
        if "sort" in item and item.get("sort") not in (None, ""):
            sort_order = _sort_value(item.get("sort"))
        elif batch:
            sort_order = (index + 1) * 10
        else:
            sort_order = int(row.sort_order or 0)
        row.title = title
        row.body = body
        row.active = active
        row.sort_order = sort_order
        row.updated_by_id = getattr(user, "id", None)
        row.updated_at = utcnow()
        db.session.flush()
        after = _snap(row)
        changed = after != before
        if changed:
            audit(user.id, "human", "update" if before else "create", "help_note", row.id, before, after)
        saved.append({"slug": slug, "title": title, "active": active, "changed": changed})
        seen.append(slug)
    if replace and not any(row["active"] for row in saved):
        db.session.rollback()
        return {"ok": False, "error": "A replace needs at least one note that stays on the page."}
    if replace:
        extras = HelpNote.query.filter(HelpNote.active.is_(True), HelpNote.slug.notin_(seen)).all()
        for row in extras:
            before = _snap(row)
            row.active = False
            row.updated_by_id = getattr(user, "id", None)
            row.updated_at = utcnow()
            audit(user.id, "human", "update", "help_note", row.id, before, _snap(row))
            saved.append({"slug": row.slug, "title": row.title, "active": False, "changed": True})
    db.session.commit()
    shown = [row["title"] for row in saved if row["active"] and row["changed"]]
    hidden = [row["title"] for row in saved if not row["active"] and row["changed"]]
    parts = []
    if shown:
        parts.append(f"Saved {len(shown)} help notes." if len(shown) > _REPLY_LIST else "Saved " + ", ".join(shown) + ".")
    if hidden:
        parts.append(f"Hidden {len(hidden)} help notes." if len(hidden) > _REPLY_LIST else "Hidden " + ", ".join(hidden) + ".")
    if not parts:
        parts.append("Those notes are already on the page.")
    return {"ok": True, "saved": saved, "reply": " ".join(parts)}
