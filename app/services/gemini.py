"""Free Gemini BYOK. Pick the latest free-compatible flash model. On 429, stop."""
from __future__ import annotations

import re
from datetime import timedelta

import requests

from app.services.clock import utcnow

BASE = "https://generativelanguage.googleapis.com/v1beta"
FREE_BLOCK = (
    "pro",
    "ultra",
    "embed",
    "imagen",
    "tts",
    "aqa",
    "robot",
    "live",
    "image",
    "audio",
    "exp",
    "veo",
    "native",
)
# Used only when the list endpoint is down. Live ids still win by version.
FALLBACK_FREE = (
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
)

TOOL_DECLS = [
    {
        "name": "plan_trip",
        "description": "She is going on a trip. Create the property if it is new, or update the open trip if that place is already planned. Save immediately. Include every detail she gave: property, city, what the visit is for, the day, and the miles.",
        "parameters": {
            "type": "object",
            "properties": {
                "property_name": {"type": "string"},
                "city": {"type": "string"},
                "region": {"type": "string"},
                "address": {"type": "string"},
                "starts_on": {"type": "string", "description": "YYYY-MM-DD"},
                "purpose": {"type": "string", "description": "What the visit is for"},
                "miles_estimate": {"type": "number", "description": "Miles she stated for the drive"},
                "day_stated": {"type": "boolean"},
                "day_assumed": {"type": "boolean"},
            },
            "required": ["property_name", "city"],
        },
    },
    {
        "name": "update_trip",
        "description": "Change a trip, arrive on site, end the visit, or end the day.",
        "parameters": {
            "type": "object",
            "properties": {
                "trip_id": {"type": "integer"},
                "property_name": {"type": "string"},
                "city": {"type": "string"},
                "miles_estimate": {"type": "number"},
                "miles_actual": {"type": "number"},
                "arrive": {"type": "boolean"},
                "end_visit": {"type": "boolean"},
                "end_day": {"type": "boolean"},
                "handoff": {"type": "string"},
                "purpose": {"type": "string"},
            },
        },
    },
    {
        "name": "upsert_property",
        "description": "Save a property name, address, and map pin. No floor plans.",
        "parameters": {
            "type": "object",
            "properties": {
                "property_name": {"type": "string"},
                "city": {"type": "string"},
                "region": {"type": "string"},
                "address": {"type": "string"},
                "lat": {"type": "number"},
                "lng": {"type": "number"},
            },
            "required": ["property_name", "city"],
        },
    },
    {
        "name": "record_unit_visit",
        "description": "Log work against a unit number. Creates the unit the first time it is named.",
        "parameters": {
            "type": "object",
            "properties": {
                "unit_number": {"type": "string"},
                "title": {"type": "string"},
                "status": {"type": "string", "description": "done, planned, blocked, followup, skipped"},
                "note": {"type": "string"},
                "property_name": {"type": "string"},
            },
            "required": ["unit_number"],
        },
    },
    {
        "name": "log_job_event",
        "description": "Add a note to an existing job.",
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {"type": "integer"},
                "body": {"type": "string"},
            },
            "required": ["job_id", "body"],
        },
    },
    {
        "name": "attach_media",
        "description": "Attach an already uploaded photo, receipt, or nameplate.",
        "parameters": {
            "type": "object",
            "properties": {
                "media_id": {"type": "integer"},
                "job_id": {"type": "integer"},
                "unit_number": {"type": "string"},
                "caption": {"type": "string"},
            },
            "required": ["media_id"],
        },
    },
    {
        "name": "log_expense",
        "description": "File gas, food, or other. Always leave it for her to confirm. Never finalize offline.",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "amount_cents": {"type": "integer"},
                "merchant": {"type": "string"},
                "odometer": {"type": "integer"},
                "note": {"type": "string"},
                "media_id": {"type": "integer"},
                "confidence": {"type": "number"},
            },
            "required": ["kind"],
        },
    },
    {
        "name": "estimate_miles",
        "description": "Estimate drive miles from home base to the property. She can edit the number.",
        "parameters": {
            "type": "object",
            "properties": {"trip_id": {"type": "integer"}, "miles": {"type": "number"}},
        },
    },
    {
        "name": "query_record",
        "description": "Answer from her saved jobs, units, expenses, and trips.",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "draft_report",
        "description": "Build a weekly, company, or property report for her bosses.",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "weekly, company, or property"},
                "property_name": {"type": "string"},
                "starts_on": {"type": "string"},
            },
            "required": ["kind"],
        },
    },
    {
        "name": "send_report",
        "description": "Publish a saved report to viewers. Email only if that person has an email.",
        "parameters": {
            "type": "object",
            "properties": {"report_id": {"type": "integer"}},
        },
    },
    {
        "name": "invite_viewer",
        "description": "Add a user. Email is optional. Role is viewer, field, or owner.",
        "parameters": {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "display_name": {"type": "string"},
                "role": {"type": "string"},
                "email": {"type": "string"},
                "password": {"type": "string"},
                "can_see_reports": {"type": "boolean"},
                "can_see_history": {"type": "boolean"},
                "can_see_live_map": {"type": "boolean"},
            },
            "required": ["username", "role"],
        },
    },
    {
        "name": "update_viewer",
        "description": "Change a user's role or what they can see. Email may be cleared.",
        "parameters": {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "role": {"type": "string"},
                "email": {"type": "string"},
                "clear_email": {"type": "boolean"},
                "can_see_reports": {"type": "boolean"},
                "can_see_history": {"type": "boolean"},
                "can_see_live_map": {"type": "boolean"},
                "active": {"type": "boolean"},
            },
            "required": ["username"],
        },
    },
    {
        "name": "delete_property",
        "description": "Remove a property. Use the property id from her record. Use this when she says delete or remove a property, a site, or a duplicate.",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {"type": "integer"},
                "property_name": {"type": "string"},
                "city": {"type": "string"},
            },
        },
    },
    {
        "name": "update_property",
        "description": "Rename a property, change its city, or set its street address. Use the property id from her record.",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {"type": "integer"},
                "property_name": {"type": "string"},
                "city": {"type": "string"},
                "region": {"type": "string"},
                "address": {"type": "string"},
            },
            "required": ["property_id"],
        },
    },
    {
        "name": "clear_plan",
        "description": "Delete an open plan or trip immediately. Use when she says delete, remove, or cancel a plan, stop, or trip. Pass the property or job name if she said one. Set trip true only when she said delete the trip.",
        "parameters": {
            "type": "object",
            "properties": {
                "property_name": {"type": "string"},
                "title": {"type": "string"},
                "trip": {"type": "boolean"},
            },
        },
    },
    {
        "name": "soft_delete",
        "description": "Hide a job, unit, or expense by its id so it can be restored.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity": {"type": "string"},
                "entity_id": {"type": "integer"},
            },
            "required": ["entity", "entity_id"],
        },
    },
    {
        "name": "restore",
        "description": "Bring back a soft-deleted job, unit, or expense.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity": {"type": "string"},
                "entity_id": {"type": "integer"},
            },
            "required": ["entity", "entity_id"],
        },
    },
    {
        "name": "update_settings",
        "description": "Change the assistant name, tone, home base, company name, or default city.",
        "parameters": {
            "type": "object",
            "properties": {
                "assistant_name": {"type": "string"},
                "tone": {"type": "string"},
                "always_ask": {"type": "string"},
                "default_city": {"type": "string"},
                "default_region": {"type": "string"},
                "report_voice": {"type": "string"},
                "company_name": {"type": "string"},
                "home_label": {"type": "string"},
                "timezone": {"type": "string"},
            },
        },
    },
]


def _clean_id(name: str) -> str:
    text = (name or "").strip()
    if text.startswith("models/"):
        text = text[len("models/") :]
    return text[:120]


def free_ok(name: str) -> bool:
    n = _clean_id(name).lower()
    if "flash" not in n or not n.startswith("gemini-"):
        return False
    return not any(bit in n for bit in FREE_BLOCK)


def _rank(name: str):
    n = _clean_id(name).lower()
    nums = [int(x) for x in re.findall(r"\d+", n)]
    nums = (nums + [0, 0, 0, 0])[:4]
    lite = 1 if "lite" in n else 0
    latest = 1 if "latest" in n else 0
    return (-nums[0], -nums[1], -nums[2], -nums[3], lite, -latest, n)


def pick_free_model(names) -> str | None:
    ok = []
    for raw in names or []:
        name = _clean_id(str(raw or ""))
        if name and free_ok(name) and name not in ok:
            ok.append(name)
    if not ok:
        return None
    ok.sort(key=_rank)
    return ok[0]


def ids_from_list_payload(data) -> list[str]:
    rows = (data or {}).get("models") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        methods = row.get("supportedGenerationMethods") or row.get("supportedActions") or []
        blob = " ".join(str(m).lower() for m in methods)
        if blob and "generatecontent" not in blob and "generate_content" not in blob:
            continue
        name = _clean_id(row.get("name") or "")
        if name and name not in out:
            out.append(name)
    return out


def list_models(api_key: str, timeout: int = 8) -> list[str]:
    resp = requests.get(
        f"{BASE}/models",
        params={"key": api_key, "pageSize": 100},
        headers={"x-goog-api-key": api_key},
        timeout=timeout,
    )
    if resp.status_code == 429:
        raise QuotaError(retry_after(resp))
    if resp.status_code >= 400:
        return []
    return ids_from_list_payload(resp.json() if resp.content else {})


def retry_after(resp) -> int:
    try:
        return max(30, int(resp.headers.get("retry-after") or 60))
    except (TypeError, ValueError):
        return 60


class QuotaError(Exception):
    def __init__(self, seconds: int = 60):
        super().__init__("quota")
        self.seconds = seconds


def _generate(api_key: str, model: str, parts: list, timeout: int, tools=False) -> dict:
    url = f"{BASE}/models/{model}:generateContent"
    body: dict = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 800},
    }
    if tools:
        body["systemInstruction"] = {
            "parts": [
                {
                    "text": (
                        "You help one regional manager. The message tells you your name and how to talk. Use that name and that tone. "
                        "Her record is in the message. Answer questions from it. "
                        "Use tools to add, edit, and delete. delete_property and update_property take the property id. "
                        "Do not say there is no matching job when she asked about a property. "
                        "Do not ask again for a fact she already said. Never invent a unit she did not name."
                    )
                }
            ]
        }
        body["tools"] = [{"functionDeclarations": TOOL_DECLS}]
    resp = requests.post(
        url,
        params={"key": api_key},
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    if resp.status_code == 429:
        raise QuotaError(retry_after(resp))
    if resp.status_code >= 400:
        return {"ok": False, "status": resp.status_code, "error": (resp.text or "")[:400]}
    data = resp.json() if resp.content else {}
    calls = []
    texts = []
    for cand in data.get("candidates") or []:
        content = cand.get("content") or {}
        for part in content.get("parts") or []:
            if part.get("text"):
                texts.append(part["text"])
            fc = part.get("functionCall") or part.get("function_call")
            if isinstance(fc, dict) and fc.get("name"):
                calls.append({"name": fc.get("name"), "args": fc.get("args") or {}})
    return {"ok": True, "status": 200, "text": "\n".join(texts).strip(), "calls": calls}


def health_check(api_key: str, model: str, timeout: int = 12) -> dict:
    try:
        result = _generate(
            api_key,
            model,
            [{"text": "Reply with the word ok."}],
            timeout,
            tools=False,
        )
    except QuotaError as exc:
        return {"ok": False, "quota": True, "seconds": exc.seconds, "model": model}
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc), "model": model}
    result["model"] = model
    return result


def resolve_model(api_key: str, current: str | None = None, timeout: int = 8) -> dict:
    """Return {model, quota, seconds, error}. One health check. No 429 spin."""
    names: list[str] = []
    try:
        names = list_models(api_key, timeout=timeout)
    except QuotaError as exc:
        return {"model": "", "quota": True, "seconds": exc.seconds}
    except requests.RequestException as exc:
        names = []
        err = str(exc)
    else:
        err = ""
    picked = pick_free_model(names) if names else None
    ladder = []
    if picked:
        ladder.append(picked)
    if current and free_ok(current) and current not in ladder:
        ladder.append(_clean_id(current))
    for name in FALLBACK_FREE:
        if name not in ladder:
            ladder.append(name)
    last_error = err
    for model in ladder[:4]:
        check = health_check(api_key, model, timeout=timeout)
        if check.get("quota"):
            return {"model": model, "quota": True, "seconds": check.get("seconds") or 60}
        if check.get("ok"):
            return {"model": model, "quota": False, "seconds": 0}
        last_error = check.get("error") or f"HTTP {check.get('status')}"
        if check.get("status") == 429:
            return {"model": model, "quota": True, "seconds": 60}
    return {"model": "", "quota": False, "seconds": 0, "error": last_error or "No free Gemini model answered."}


def read_nameplate(api_key: str, model: str, image: bytes, mime: str = "image/jpeg", timeout: int = 25) -> dict:
    """One vision read of a nameplate or product label. A 429 stops. It does not try another model."""
    import base64

    if not image or not api_key or not model:
        return {"ok": False, "error": "no image"}
    encoded = base64.b64encode(image).decode("ascii")
    prompt = (
        "Read this equipment nameplate, sticker, or product label for a field technician. "
        "Return JSON only with keys kind, brand, model, serial, size, confidence, missing. "
        "kind is a short name such as refrigerator, washer, dryer, dishwasher, range, air conditioner, furnace, or water heater. "
        "size is like 3 ton when it is printed. confidence is 0 to 1. "
        "missing is a list of fields you could not read. Do not invent a serial or model."
    )
    try:
        result = _generate(
            api_key,
            model,
            [
                {"text": prompt},
                {"inline_data": {"mime_type": mime or "image/jpeg", "data": encoded}},
            ],
            timeout,
            tools=False,
        )
    except QuotaError as exc:
        return {"ok": False, "quota": True, "seconds": exc.seconds}
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)}
    if not result.get("ok"):
        return result
    from app.services.equipment import empty, plate_from_json

    parsed = plate_from_json(result.get("text") or "")
    parsed["ok"] = True
    if not parsed.get("kind") and not any(parsed.get(k) for k in ("brand", "model", "serial", "size")):
        parsed = empty()
        parsed["ok"] = True
        parsed["confidence"] = 0.2
        parsed["missing"] = ["kind", "brand", "model", "serial"]
    return parsed


def complete(api_key: str, model: str, text: str, timeout: int = 25) -> dict:
    try:
        return _generate(api_key, model, [{"text": text}], timeout, tools=True)
    except QuotaError as exc:
        return {"ok": False, "quota": True, "seconds": exc.seconds, "calls": [], "text": ""}
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc), "calls": [], "text": ""}


def backoff_until(seconds: int):
    return utcnow() + timedelta(seconds=max(30, int(seconds or 60)))
