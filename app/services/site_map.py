"""One uploaded site plan for one property.

This is a file the office already has. It is not a street map, and it never
plots every property.
"""
from __future__ import annotations

import re

from flask import abort

from app.builddb.builddb import db
from app.models import Media, Property
from app.services.clock import utcnow
from app.services.files import delete_blob, save_blob
from app.services.records import audit

MAP_KIND = "property_map"
MAX_BYTES = 12 * 1024 * 1024

_UPLOAD_ROLES = {
    "owner",
    "admin",
    "regional_manager",
    "regional_property_manager",
    "property_manager",
    "assistant_manager",
    "office",
}

_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
    (b"%PDF-", "application/pdf", ".pdf"),
)


def sniff_map(raw: bytes) -> tuple[str, str] | None:
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp", ".webp"
    for magic, mime, ext in _MAGIC:
        if raw.startswith(magic):
            return mime, ext
    return None


def sniff_photo(raw: bytes) -> tuple[str, str] | None:
    """A chat photo. A PDF is a property map, not a nameplate."""
    found = sniff_map(raw)
    if not found or found[0] == "application/pdf":
        return None
    return found


def map_for(property_id: int) -> Media | None:
    return (
        Media.query.filter_by(property_id=int(property_id), kind=MAP_KIND)
        .order_by(Media.id.desc())
        .first()
    )


def can_upload_map(user, property_id: int) -> bool:
    """Property office people may replace this one property's map."""
    if not user or not getattr(user, "active", True):
        return False
    from app.services.access import can_edit_property, can_see_property, role_of

    role = role_of(user)
    if role not in _UPLOAD_ROLES:
        return False
    prop_id = int(property_id)
    if role in {"assistant_manager", "office"}:
        return can_see_property(user, prop_id)
    return can_edit_property(user, prop_id)


def pick_property(user, raw: str) -> tuple[list[Property], Property | None]:
    """The one property this page is about. Never the whole portfolio."""
    from app.services.access import can_see_property
    from app.services.context import remembered_property
    from app.services.parse import property_catalog

    choices = property_catalog(user)
    text = (raw or "").strip()
    if text:
        if not text.isdigit():
            abort(404)
        prop = db.session.get(Property, int(text))
        if not prop or prop.deleted_at or not can_see_property(user, prop.id):
            abort(404)
        return choices, prop
    remembered = remembered_property(user)
    if remembered and any(row.id == remembered.id for row in choices):
        return choices, remembered
    if len(choices) == 1:
        return choices, choices[0]
    return choices, None


def download_name(prop: Property, mime: str) -> str:
    ext = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "application/pdf": ".pdf",
    }.get(mime or "", "")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", (prop.name or "property")).strip("-")[:40] or "property"
    return stem + "-map" + ext


def replace_map(user, prop: Property, raw: bytes) -> tuple[bool, str]:
    if not raw:
        return False, "That file was empty."
    if len(raw) > MAX_BYTES:
        return False, "That map is too large. Keep it under 12 MB."
    found = sniff_map(raw)
    if not found:
        return False, "Use a JPEG, PNG, WEBP, GIF, or PDF of this property."
    mime, _ext = found
    name = save_blob(raw)
    row = Media(
        user_id=user.id,
        kind=MAP_KIND,
        storage_name=name,
        mime=mime,
        caption="Property map",
        property_id=prop.id,
        created_at=utcnow(),
    )
    db.session.add(row)
    db.session.flush()
    old = Media.query.filter(
        Media.property_id == prop.id,
        Media.kind == MAP_KIND,
        Media.id != row.id,
    ).all()
    retired = [item.storage_name for item in old]
    for item in old:
        db.session.delete(item)
    audit(
        user.id,
        "human",
        "upload_property_map",
        "property",
        prop.id,
        {},
        {"property_id": prop.id, "media_id": row.id},
    )
    db.session.commit()
    for storage in retired:
        delete_blob(storage)
    return True, "Property map saved."


def remove_map(user, prop: Property) -> str:
    rows = Media.query.filter_by(property_id=prop.id, kind=MAP_KIND).all()
    if not rows:
        return "No map was uploaded for this property."
    retired = [item.storage_name for item in rows]
    for item in rows:
        db.session.delete(item)
    audit(
        user.id,
        "human",
        "remove_property_map",
        "property",
        prop.id,
        {"property_id": prop.id},
        {"property_id": prop.id},
    )
    db.session.commit()
    for storage in retired:
        delete_blob(storage)
    return "Property map removed."
