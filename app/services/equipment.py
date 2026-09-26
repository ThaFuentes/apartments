"""Turn a sentence or a nameplate reading into kind, brand, model, serial, size."""
from __future__ import annotations

import re

BRANDS = (
    "american standard",
    "carrier",
    "trane",
    "lennox",
    "goodman",
    "rheem",
    "ruud",
    "york",
    "payne",
    "daikin",
    "mitsubishi",
    "heil",
    "bryant",
    "amana",
    "coleman",
    "nordyne",
    "bosch",
    "lg",
    "samsung",
    "whirlpool",
    "ge",
    "maytag",
    "frigidaire",
    "kenmore",
    "kitchenaid",
    "speed queen",
    "electrolux",
    "haier",
    "hotpoint",
)

KINDS = (
    ("washer dryer", ("washer dryer", "washer/dryer", "laundry center")),
    ("refrigerator", ("refrigerator", "fridge", "freezer")),
    ("washer", ("washing machine", "washer")),
    ("dryer", ("clothes dryer", "dryer")),
    ("dishwasher", ("dishwasher",)),
    ("range", ("range", "stove", "oven", "cooktop")),
    ("microwave", ("microwave",)),
    ("disposal", ("garbage disposal", "disposal")),
    ("air conditioner", ("air conditioner", "a/c", "ac", "condenser", "heat pump", "mini split", "minisplit")),
    ("furnace", ("furnace",)),
    ("air handler", ("air handler", "airhandler")),
    ("coil", ("evaporator coil", "evap coil", "cased coil")),
    ("water heater", ("water heater",)),
    ("thermostat", ("thermostat", "tstat")),
    ("package unit", ("package unit", "rooftop unit", "rtu")),
)

KIND_CHOICES = tuple(kind for kind, _words in KINDS)
KIND_LABELS = {
    "range": "stove",
    "washer dryer": "washer and dryer",
    "air conditioner": "AC",
}

SERIAL = re.compile(
    r"\b(?:sn|s/?n|serial(?:\s*(?:number|no\.?|#))?)\s*[:#]?\s*([A-Z0-9][A-Z0-9\-]{2,})",
    re.I,
)
MODEL = re.compile(
    r"\b(?:model|m/?n)\s*[:#]?\s*([A-Z0-9][A-Z0-9\-]{2,})",
    re.I,
)
TON = re.compile(r"\b(\d(?:\.\d)?)\s*[- ]?\s*tons?\b", re.I)
CONFIDENCE_FLOOR = 0.75


def empty() -> dict:
    return {
        "kind": "",
        "brand": "",
        "model": "",
        "serial": "",
        "size": "",
        "confidence": 0.0,
        "missing": [],
        "conflict": "",
    }


def has_identity(row: dict | None) -> bool:
    if not row:
        return False
    return any((row.get(key) or "").strip() for key in ("brand", "model", "serial", "size"))


def kind_label(kind: str) -> str:
    key = (kind or "").strip().lower()
    if not key:
        return ""
    return KIND_LABELS.get(key, kind)


def kind_choices() -> list[tuple[str, str]]:
    return [(kind, kind_label(kind)) for kind in KIND_CHOICES]


def describe(row: dict | None) -> str:
    if not row:
        return ""
    bits = []
    if row.get("brand"):
        bits.append(str(row["brand"]))
    if row.get("style"):
        bits.append(str(row["style"]))
    if row.get("color"):
        bits.append(str(row["color"]))
    if row.get("size"):
        bits.append(str(row["size"]))
    label = kind_label(row.get("kind") or "")
    if label and label.lower() not in " ".join(bits).lower():
        bits.append(label)
    if row.get("model"):
        bits.append(f"model {row['model']}")
    if row.get("serial"):
        bits.append(f"serial {row['serial']}")
    return " ".join(bits).strip()


def appliance_kinds(text: str) -> list[str]:
    """Every appliance named in the sentence. Drier counts as dryer."""
    low = (text or "").lower().replace("drier", "dryer")
    found = []
    for kind, words in KINDS:
        if kind == "washer dryer":
            continue
        if any(re.search(rf"(^|[^a-z]){re.escape(word)}([^a-z]|$)", low) for word in words):
            found.append(kind)
    return found


def parse_equipment(text: str) -> dict:
    raw = text or ""
    low = raw.lower()
    row = empty()
    for kind, words in KINDS:
        if any(re.search(rf"(^|[^a-z]){re.escape(word)}([^a-z]|$)", low) for word in words):
            row["kind"] = kind
            break
    for brand in BRANDS:
        if re.search(rf"(^|[^a-z]){re.escape(brand)}([^a-z]|$)", low):
            row["brand"] = brand.title() if brand != "ge" else "GE"
            if brand == "lg":
                row["brand"] = "LG"
            break
    model = MODEL.search(raw)
    if model:
        row["model"] = model.group(1).upper()
    serial = SERIAL.search(raw)
    if serial:
        row["serial"] = serial.group(1).upper()
    ton = TON.search(raw)
    if ton:
        row["size"] = f"{ton.group(1)} ton"
        if not row["kind"]:
            row["kind"] = "air conditioner"
    filled = [key for key in ("brand", "model", "serial", "size") if row[key]]
    if row["serial"] and (row["model"] or row["brand"]):
        row["confidence"] = 0.9
    elif len(filled) >= 2:
        row["confidence"] = 0.82
    elif filled:
        row["confidence"] = 0.7
    elif row["kind"]:
        row["confidence"] = 0.55
    if not row["serial"] and (row["model"] or row["brand"] or "nameplate" in low or "serial" in low):
        row["missing"].append("the serial")
    return row


def merge_equipment(typed: dict | None, seen: dict | None) -> dict:
    """What she typed wins. The photo fills blanks. Two different serials get asked."""
    base = empty()
    typed = typed or empty()
    seen = seen or empty()
    for key in ("kind", "brand", "model", "serial", "size"):
        if (typed.get(key) or "").strip():
            base[key] = str(typed[key]).strip()
        elif (seen.get(key) or "").strip():
            base[key] = str(seen[key]).strip()
    if typed.get("serial") and seen.get("serial") and str(typed["serial"]).upper() != str(seen["serial"]).upper():
        base["conflict"] = f"You wrote serial {typed['serial']} and the photo looks like {seen['serial']}."
        base["confidence"] = 0.4
        base["missing"] = ["which serial is right"]
        return base
    scores = [float(row.get("confidence") or 0) for row in (typed, seen) if has_identity(row) or row.get("kind")]
    base["confidence"] = max(scores) if scores else 0.0
    if not base["serial"] and has_identity(base):
        base["missing"] = ["the serial"]
        if base["confidence"] >= CONFIDENCE_FLOOR:
            base["confidence"] = 0.7
    return base


def plate_from_json(text: str) -> dict:
    import json

    raw = (text or "").strip()
    match = re.search(r"\{.*\}", raw, re.S)
    row = empty()
    if not match:
        row["confidence"] = 0.2
        row["missing"] = ["the nameplate"]
        return row
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        row["confidence"] = 0.2
        row["missing"] = ["the nameplate"]
        return row
    if not isinstance(data, dict):
        return row
    row["kind"] = str(data.get("kind") or "").strip()[:80]
    row["brand"] = str(data.get("brand") or "").strip()[:80]
    row["model"] = str(data.get("model") or "").strip().upper()[:80]
    row["serial"] = str(data.get("serial") or "").strip().upper()[:80]
    row["size"] = str(data.get("size") or "").strip()[:40]
    try:
        row["confidence"] = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        row["confidence"] = 0.0
    missing = data.get("missing") if isinstance(data.get("missing"), list) else []
    row["missing"] = [str(item) for item in missing][:6]
    if not row["serial"] and "the serial" not in row["missing"] and "serial" not in row["missing"]:
        row["missing"].append("the serial")
    if row["serial"] and row["confidence"] < CONFIDENCE_FLOOR and (row["model"] or row["brand"]):
        row["confidence"] = CONFIDENCE_FLOOR
    return row


def gemini_key(user):
    from app.models import ApiCredential, User
    from app.services.clock import utcnow
    from app.services.crypto import decrypt_text
    from app.services.gemini import resolve_model

    owner = user if getattr(user, "role", "") in ("owner", "admin") else User.query.filter_by(role="owner").order_by(User.id.asc()).first()
    if not owner:
        return "", None
    cred = (
        ApiCredential.query.filter_by(user_id=owner.id, provider="gemini")
        .order_by(ApiCredential.id.desc())
        .first()
    )
    if not cred:
        return "", None
    if cred.backoff_until and cred.backoff_until > utcnow():
        return "", cred
    try:
        api_key = decrypt_text(cred.secret_ciphertext)
    except Exception:
        return "", cred
    if not cred.model_id:
        resolved = resolve_model(api_key, None)
        if resolved.get("quota"):
            from app.services.gemini import backoff_until

            cred.backoff_until = backoff_until(resolved.get("seconds") or 60)
            return "", cred
        if resolved.get("model"):
            cred.model_id = resolved["model"]
            cred.model_checked_at = utcnow()
            from app.builddb.builddb import db

            db.session.commit()
    return api_key, cred


def read_photo(user, image: bytes, mime: str) -> dict:
    """Ask the saved AI keys to read a label. One try each. A quota stop moves to the next key."""
    from app.builddb.builddb import db
    from app.services.gemini import backoff_until
    from app.services.providers import keys_for, provider_spec, read_image

    rows = [row for row in keys_for(user) if provider_spec(row.provider).get("vision")]
    if not rows:
        return empty()
    notes = []
    for cred in rows:
        from app.services.clock import utcnow

        if cred.backoff_until and cred.backoff_until > utcnow():
            notes.append(provider_spec(cred.provider).get("label") or cred.provider)
            continue
        seen = read_image(cred, image, mime)
        if seen.get("quota"):
            cred.backoff_until = backoff_until(seen.get("seconds") or 60)
            db.session.commit()
            notes.append(provider_spec(cred.provider).get("label") or cred.provider)
            continue
        seen.pop("ok", None)
        if has_identity(seen) or seen.get("kind"):
            return seen
    if notes:
        row = empty()
        row["quota"] = True
        row["reply"] = "Out of quota on " + ", ".join(notes) + ". I kept the photo and will not keep calling. Type the brand, model, and serial."
        return row
    return empty()


def plate_ready(row: dict, *, from_photo: bool) -> bool:
    """A nameplate photo must clear the floor and include a serial. Typed work can save without one."""
    if not from_photo:
        return True
    if row.get("conflict"):
        return False
    if float(row.get("confidence") or 0) < CONFIDENCE_FLOOR:
        return False
    return bool((row.get("serial") or "").strip())
