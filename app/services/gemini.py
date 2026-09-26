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

GEAR_PROPS = {
    "kind": {"type": "string", "description": "refrigerator, washer, dryer, dishwasher, range, microwave, air conditioner, furnace, water heater, thermostat"},
    "brand": {"type": "string"},
    "model": {"type": "string"},
    "serial": {"type": "string"},
    "size": {"type": "string"},
    "style": {"type": "string"},
    "color": {"type": "string"},
    "notes": {"type": "string"},
}

TOOL_DECLS = [
    {
        "name": "plan_trip",
        "description": "Save a plan or a trip. Use this when she says plan, schedule, or trip. Do not use upsert_property for a plan. property_name is the apartment name only. Each unit job is its own work_items record. Never combine two units into one purpose.",
        "parameters": {
            "type": "object",
            "properties": {
                "property_name": {"type": "string"},
                "city": {"type": "string"},
                "region": {"type": "string"},
                "address": {"type": "string"},
                "starts_on": {"type": "string", "description": "YYYY-MM-DD"},
                "purpose": {"type": "string", "description": "Short reason for the trip. Not a list of unit jobs."},
                "miles_estimate": {"type": "number", "description": "Miles she stated for the drive, not the odometer"},
                "odometer_start": {"type": "integer", "description": "Starting mileage on the vehicle"},
                "odometer_end": {"type": "integer", "description": "Ending mileage on the vehicle"},
                "work_items": {
                    "type": "array",
                    "description": "One record per unit job. worked on the AC at unit 12 and fix the tub clog at unit 26 are two items.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "unit_number": {"type": "string"},
                            "title": {"type": "string", "description": "The job at that unit only"},
                        },
                        "required": ["title"],
                    },
                },
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
                "odometer_start": {"type": "integer", "description": "Starting mileage"},
                "odometer_end": {"type": "integer", "description": "Ending mileage"},
                "arrive": {"type": "boolean"},
                "end_visit": {"type": "boolean"},
                "end_day": {"type": "boolean"},
                "handoff": {"type": "string"},
                "purpose": {"type": "string"},
            },
        },
    },
    {
        "name": "lookup_address",
        "description": "Search the web, not her saved sites, for a property's street address. Use this when she says Google, online, or look up an address. Does not add the property.",
        "parameters": {
            "type": "object",
            "properties": {
                "property_name": {"type": "string"},
                "city": {"type": "string"},
                "region": {"type": "string"},
            },
            "required": ["property_name", "city"],
        },
    },
    {
        "name": "upsert_property",
        "description": "Add or update an apartment property. property_name is only the apartment name, never her whole sentence. If she did not give a name, do not call this. Use update_property when she says edit, change, or correct. Use plan_trip when she says plan.",
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
        "description": "Log work against a unit number. Creates the unit the first time it is named. When she says she added, installed, or replaced an appliance in a unit, pass the appliance in equipment; title can be a short phrase such as 'Added a fridge', and equipment saves even with no title.",
        "parameters": {
            "type": "object",
            "properties": {
                "unit_number": {"type": "string"},
                "title": {"type": "string"},
                "status": {"type": "string", "description": "done, planned, blocked, followup, skipped"},
                "note": {"type": "string"},
                "property_name": {"type": "string"},
                "equipment": {
                    "type": "object",
                    "description": "The one appliance she named on this unit. Fill this even when there is no work title.",
                    "properties": GEAR_PROPS,
                },
                "equipment_items": {
                    "type": "array",
                    "description": "More appliances on this same unit, one object each. A washer and a dryer are two items.",
                    "items": {"type": "object", "properties": GEAR_PROPS},
                },
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
        "name": "unit_board",
        "description": "Move an existing apartment unit between buildings or rename its unit number. Include the saved property name and existing unit number. Use set_building or set_unit_number; never create a replacement unit for an edit.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["set_building", "set_unit_number"]},
                "property_hint": {"type": "string"},
                "unit_number": {"type": "string"},
                "new_number": {"type": "string"},
                "building": {"type": "string"},
            },
            "required": ["action", "property_hint", "unit_number"],
        },
    },
    {
        "name": "soft_delete",
        "description": "Soft-remove a job, unit, unit task, equipment record, or expense. Use an exact entity ID from saved records. A unit removal also soft-removes its linked jobs, tasks, and equipment so they can be restored together.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "enum": ["job", "unit", "unit_task", "equipment", "expense"]},
                "entity_id": {"type": "integer"},
                "record_number": {"type": "string"},
                "record_property": {"type": "string"},
            },
            "required": ["entity"],
        },
    },
    {
        "name": "restore",
        "description": "Restore a soft-deleted record. Use an exact entity ID from saved records. A removed unit restores only its linked records that were removed at the same time.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "enum": ["job", "unit", "unit_task", "equipment", "expense"]},
                "entity_id": {"type": "integer"},
                "record_number": {"type": "string"},
                "record_property": {"type": "string"},
            },
            "required": ["entity"],
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


CHAT_RULES = (
    "You are the conversation. Talk like a person she works with every day. "
    "The server runs your tool calls and shows your words. "
    "property_name is only the apartment name, never her sentence. "
    "If she says create a property in a city and does not name it, ask for the name and do not call upsert_property. "
    "A plan or a trip is plan_trip, not a new property. Gas and meals are not properties. "
    "Each unit job is its own record. Pass work_items with one object per job, unit_number and title. "
    "Do not put two jobs into one purpose or one detail. "
    "Worked on the AC at unit 12 and fix the tub clog at unit 26 are two records. "
    "Starting mileage and ending mileage are odometer_start and odometer_end on that same plan. "
    "Edit, change, or correct uses update_property on the property she already has. Do not create a second one. "
    "Delete all of a name removes every match. If several match and she did not say all, list them with the city. "
    "A typed street address is the address. Do not say you searched and could not find it. "
    "The earlier messages are this same chat. Do not ask again for a city, address, or name she already gave. "
    "When the apartment name and the city are both in the thread, call upsert_property once and include any street she already typed. "
    "Do not say there is no matching job unless she asked about a job."
)


class QuotaError(Exception):
    def __init__(self, seconds: int = 60):
        super().__init__("quota")
        self.seconds = seconds


def _generate(api_key: str, model: str, parts: list, timeout: int, tools=False, contents: list | None = None) -> dict:
    url = f"{BASE}/models/{model}:generateContent"
    body: dict = {
        "contents": contents or [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1600},
    }
    if tools:
        body["systemInstruction"] = {
            "parts": [
                {
                    "text": CHAT_RULES + " You have Google Search. Use it for addresses and businesses that are not already in her record."
                }
            ]
        }
        body["tools"] = [{"functionDeclarations": TOOL_DECLS}, {"google_search": {}}]
    resp = requests.post(
        url,
        params={"key": api_key},
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    if tools and resp.status_code >= 400 and resp.status_code != 429:
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


def _contents(history: list | None, text: str) -> list:
    contents = []
    for turn in history or []:
        body = (turn.get("body") or "").strip()
        if not body:
            continue
        role = "model" if turn.get("role") == "assistant" else "user"
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"][0]["text"] += "\n" + body
        else:
            contents.append({"role": role, "parts": [{"text": body}]})
    if contents and contents[-1]["role"] == "user":
        contents[-1]["parts"][0]["text"] += "\n" + text
    else:
        contents.append({"role": "user", "parts": [{"text": text}]})
    return contents


def complete(api_key: str, model: str, text: str, timeout: int = 25, history: list | None = None) -> dict:
    try:
        return _generate(api_key, model, [{"text": text}], timeout, tools=True, contents=_contents(history, text))
    except QuotaError as exc:
        return {"ok": False, "quota": True, "seconds": exc.seconds, "calls": [], "text": ""}
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc), "calls": [], "text": ""}


def backoff_until(seconds: int):
    return utcnow() + timedelta(seconds=max(30, int(seconds or 60)))
