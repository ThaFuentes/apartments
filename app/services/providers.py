"""Bring-your-own-key models. Gemini, Groq, OpenAI, Grok, Claude, and a few others."""
from __future__ import annotations

import json

import requests

from app.builddb.builddb import db
from app.models import ApiCredential
from app.services import budget
from app.services.clock import utcnow
from app.services.crypto import decrypt_text, encrypt_text, last4
from app.services.gemini import CHAT_RULES, TOOL_DECLS, backoff_until, complete as gemini_complete, read_nameplate, resolve_model
from app.services.parse import catalog_lines, parse_rules

PROVIDERS = {
    "gemini": {
        "label": "Google Gemini",
        "kind": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "models": ("gemini-3.8-flash", "gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.0-flash"),
        "hint": "aistudio.google.com/apikey — a free key works. Apt picks the latest free Flash model.",
        "placeholder": "AIza…",
        "vision": True,
    },
    "groq": {
        "label": "Groq",
        "kind": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "models": ("llama-3.3-70b-versatile", "openai/gpt-oss-20b", "qwen/qwen3-32b"),
        "hint": "console.groq.com/keys",
        "placeholder": "gsk_…",
        "vision": True,
    },
    "openai": {
        "label": "OpenAI",
        "kind": "openai",
        "base_url": "https://api.openai.com/v1",
        "models": ("gpt-4.1-mini", "gpt-4o-mini", "gpt-4o", "gpt-4.1"),
        "hint": "platform.openai.com",
        "placeholder": "sk-…",
        "vision": True,
    },
    "xai": {
        "label": "Grok",
        "kind": "openai",
        "base_url": "https://api.x.ai/v1",
        "models": ("grok-4", "grok-3-mini", "grok-2-vision-1212"),
        "hint": "console.x.ai",
        "placeholder": "xai-…",
        "vision": True,
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "kind": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "models": ("claude-sonnet-4-5", "claude-3-5-haiku-latest"),
        "hint": "console.anthropic.com",
        "placeholder": "sk-ant-…",
        "vision": True,
    },
    "openrouter": {
        "label": "OpenRouter",
        "kind": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "models": ("openai/gpt-4o-mini", "google/gemini-2.0-flash-001", "x-ai/grok-4-fast"),
        "hint": "openrouter.ai — one key, many models.",
        "placeholder": "sk-or-…",
        "vision": True,
    },
    "mistral": {
        "label": "Mistral",
        "kind": "openai",
        "base_url": "https://api.mistral.ai/v1",
        "models": ("mistral-small-latest", "mistral-large-latest"),
        "hint": "console.mistral.ai",
        "placeholder": "",
        "vision": False,
    },
    "custom": {
        "label": "Other OpenAI-compatible",
        "kind": "openai",
        "base_url": "",
        "models": (),
        "hint": "Ollama, Together, Fireworks, LM Studio. Paste the base URL and model id.",
        "placeholder": "",
        "vision": False,
    },
}

ROLE_LABELS = {
    "owner": "owner",
    "admin": "admin",
    "regional_manager": "regional manager",
    "regional_property_manager": "regional property manager",
    "maintenance_regional": "regional maintenance manager",
    "property_manager": "property manager",
    "assistant_manager": "assistant manager",
    "office": "office",
    "maintenance_supervisor": "maintenance supervisor",
    "maintenance_manager": "maintenance manager",
    "maintenance_person": "maintenance person",
    "field": "maintenance person",
    "viewer": "read-only",
}


def provider_spec(name: str) -> dict:
    return PROVIDERS.get((name or "").strip().lower()) or {}


def openai_tools() -> list[dict]:
    out = []
    for tool in TOOL_DECLS:
        out.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description") or "",
                    "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
                },
            }
        )
    return out


def _retry_after(resp) -> int:
    try:
        return max(30, int(resp.headers.get("retry-after") or 60))
    except (TypeError, ValueError):
        return 60


def _openai_call(base: str, api_key: str, model: str, messages: list, tools: bool, timeout: int, max_tokens: int = 0) -> dict:
    url = base.rstrip("/") + "/chat/completions"
    body = {"model": model, "messages": messages, "temperature": 0.2}
    if tools:
        body["tools"] = openai_tools()
        body["tool_choice"] = "auto"
    if max_tokens:
        body["max_tokens"] = max_tokens
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    if resp.status_code == 429:
        return {"ok": False, "quota": True, "seconds": _retry_after(resp)}
    if resp.status_code >= 400:
        return {"ok": False, "status": resp.status_code, "error": (resp.text or "")[:300]}
    data = resp.json() if resp.content else {}
    message = ((data.get("choices") or [{}])[0].get("message") or {})
    calls = []
    for tool in message.get("tool_calls") or []:
        fn = tool.get("function") or {}
        raw_args = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            args = {}
        if fn.get("name"):
            calls.append({"name": fn["name"], "args": args if isinstance(args, dict) else {}})
    usage = data.get("usage") or {}
    tokens = int(usage.get("total_tokens") or ((usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)))
    return {"ok": True, "text": message.get("content") or "", "calls": calls, "tokens": tokens}


def _anthropic_call(api_key: str, model: str, text: str, image: bytes | None, mime: str, timeout: int, max_tokens: int = 0) -> dict:
    content = []
    if image:
        import base64

        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime or "image/jpeg",
                    "data": base64.b64encode(image).decode("ascii"),
                },
            }
        )
    content.append({"type": "text", "text": text})
    tools = [
        {
            "name": tool["name"],
            "description": tool.get("description") or "",
            "input_schema": tool.get("parameters") or {"type": "object", "properties": {}},
        }
        for tool in TOOL_DECLS
    ]
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={"model": model, "max_tokens": max_tokens or 800, "tools": tools, "messages": [{"role": "user", "content": content}]},
        timeout=timeout,
    )
    if resp.status_code == 429:
        return {"ok": False, "quota": True, "seconds": _retry_after(resp)}
    if resp.status_code >= 400:
        return {"ok": False, "status": resp.status_code, "error": (resp.text or "")[:300]}
    data = resp.json() if resp.content else {}
    calls = []
    texts = []
    for block in data.get("content") or []:
        if block.get("type") == "text" and block.get("text"):
            texts.append(block["text"])
        if block.get("type") == "tool_use" and block.get("name"):
            args = block.get("input") or {}
            calls.append({"name": block["name"], "args": args if isinstance(args, dict) else {}})
    usage = data.get("usage") or {}
    tokens = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
    return {"ok": True, "text": "\n".join(texts), "calls": calls, "tokens": tokens}


def _base(row) -> str:
    custom = (getattr(row, "base_url", None) or "").strip()
    if custom:
        return custom
    return provider_spec(row.provider).get("base_url") or ""


def _model(row) -> str:
    if (row.model_id or "").strip():
        return row.model_id.strip()
    models = provider_spec(row.provider).get("models") or ()
    return models[0] if models else ""


def chat_history(user, current: str) -> list[dict]:
    """Recent turns only. The full thread stays on screen; the model does not need all of it."""
    from app.models import ChatMessage

    rows = (
        ChatMessage.query.filter_by(user_id=user.id)
        .order_by(ChatMessage.id.desc())
        .limit(6)
        .all()
    )
    rows.reverse()
    if rows and rows[-1].role == "user" and (rows[-1].body or "").strip() == (current or "").strip():
        rows = rows[:-1]
    return [{"role": row.role, "body": (row.body or "")[:400]} for row in rows]


def _with_history(messages: list, history: list | None, text: str) -> list:
    out = list(messages)
    for turn in history or []:
        body = (turn.get("body") or "").strip()
        if not body:
            continue
        role = "assistant" if turn.get("role") == "assistant" else "user"
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n" + body
        else:
            out.append({"role": role, "content": body})
    out.append({"role": "user", "content": text})
    return out


def chat_with_tools(row, text: str, timeout: int = 25, history: list | None = None) -> dict:
    spec = provider_spec(row.provider)
    try:
        api_key = decrypt_text(row.secret_ciphertext)
    except Exception:
        return {"ok": False, "error": "Could not read that key."}
    model = _model(row)
    cap = budget.reply_cap(row)
    if spec.get("kind") == "gemini":
        resolved = {}
        if not model:
            resolved = resolve_model(api_key, row.model_id)
            if resolved.get("quota"):
                return {"ok": False, "quota": True, "seconds": resolved.get("seconds") or 60}
            model = resolved.get("model") or ""
            if model and getattr(row, "id", None):
                row.model_id = model
                row.model_checked_at = utcnow()
                db.session.commit()
        if not model:
            return {"ok": False, "error": resolved.get("error") or "No Gemini model."}
        return gemini_complete(api_key, model, text, timeout=timeout, history=history, max_output_tokens=cap)
    if not model:
        return {"ok": False, "error": "Pick a model for this key."}
    messages = _with_history([{"role": "system", "content": CHAT_RULES}], history, text)
    try:
        if spec.get("kind") == "anthropic":
            return _anthropic_call(api_key, model, text, None, "", timeout, max_tokens=cap)
        return _openai_call(_base(row), api_key, model, messages, True, timeout, max_tokens=cap)
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)}


def read_image(row, image: bytes, mime: str, timeout: int = 25) -> dict:
    spec = provider_spec(row.provider)
    if not spec.get("vision"):
        return {"ok": False, "error": "This provider does not read photos."}
    try:
        api_key = decrypt_text(row.secret_ciphertext)
    except Exception:
        return {"ok": False, "error": "Could not read that key."}
    model = _model(row)
    if spec.get("kind") == "gemini":
        if not model:
            resolved = resolve_model(api_key, row.model_id)
            if resolved.get("quota"):
                return {"ok": False, "quota": True, "seconds": resolved.get("seconds") or 60}
            model = resolved.get("model") or ""
        if not model:
            return {"ok": False, "error": "No Gemini model."}
        return read_nameplate(api_key, model, image, mime, timeout=timeout)
    prompt = (
        "Read this appliance or equipment sticker. Return JSON only with keys "
        "kind, brand, model, serial, size, confidence, missing. "
        "kind can be refrigerator, washer, dryer, dishwasher, range, microwave, "
        "air conditioner, furnace, water heater, or another short name. Do not invent a serial."
    )
    cap = budget.reply_cap(row)
    try:
        if spec.get("kind") == "anthropic":
            result = _anthropic_call(api_key, model, prompt, image, mime, timeout, max_tokens=cap)
        else:
            import base64

            data_url = f"data:{mime or 'image/jpeg'};base64,{base64.b64encode(image).decode('ascii')}"
            result = _openai_call(
                _base(row),
                api_key,
                model,
                [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]}],
                False,
                timeout,
                max_tokens=cap,
            )
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)}
    if not result.get("ok"):
        return result
    from app.services.equipment import plate_from_json

    parsed = plate_from_json(result.get("text") or "")
    parsed["ok"] = True
    parsed["tokens"] = int(result.get("tokens") or 0)
    return parsed


def check_key(provider: str, api_key: str, model: str, base_url: str = "") -> dict:
    spec = provider_spec(provider)
    if not spec:
        return {"ok": False, "error": "Pick a provider."}
    if spec["kind"] == "gemini":
        resolved = resolve_model(api_key, model or None)
        if resolved.get("quota"):
            return {"ok": False, "quota": True, "seconds": resolved.get("seconds") or 60, "model": resolved.get("model") or ""}
        if not resolved.get("model"):
            return {"ok": False, "error": resolved.get("error") or "That Gemini key did not answer."}
        return {"ok": True, "model": resolved["model"]}
    model = (model or (spec["models"][0] if spec["models"] else "")).strip()
    base = (base_url or spec.get("base_url") or "").strip()
    if not model or not base:
        return {"ok": False, "error": "This provider needs a model id and a base URL."}
    dummy = ApiCredential(provider=provider, model_id=model, base_url=base, secret_ciphertext=encrypt_text(api_key))
    result = chat_with_tools(dummy, "Reply with the word ok.", timeout=20)
    if result.get("quota"):
        return {"ok": False, "quota": True, "seconds": result.get("seconds") or 60, "model": model}
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error") or f"The key was refused ({result.get('status')}).", "model": model}
    return {"ok": True, "model": model}


def keys_for(user) -> list:
    from app.services.ai_voice import keys_for_user

    return keys_for_user(user)


def voice_brief(user=None) -> str:
    """Platform rules, the house style, and this person's name and added notes."""
    from app.services.ai_voice import voice_for

    return voice_for(user)


def record_brief(user=None) -> str:
    """Only put the caller's visible records in the model context."""
    from app.models import Property
    from app.services.access import sees_all, visible_property_ids

    prop_query = Property.query.filter(Property.deleted_at.is_(None))
    allowed = None
    if user is not None and not sees_all(user):
        allowed = visible_property_ids(user)
        prop_query = prop_query.filter(Property.id.in_(allowed or {-1}))
    props = prop_query.order_by(Property.name.asc()).limit(24).all()
    lines = ["Visible properties (name and city only):", catalog_lines(props, user=user)]
    if not props:
        lines.append("No properties yet.")
    from app.services.context import current_property, remembered_property
    from app.services.records import property_place

    here = current_property(user) if user is not None else None
    remembered = remembered_property(user) if user is not None else None
    if here:
        lines.append(f"Locked/working property: {property_place(here)} (id {here.id}).")
    elif remembered:
        lines.append(f"Default property waiting confirm: {property_place(remembered)} (id {remembered.id}).")
    return "\n".join(lines)[:1800]


def collect_tool_calls(user, text: str):
    """Try each saved key in order. Stop on a real answer. Local chat runs only after every key fails."""
    rows = keys_for(user)
    if not rows:
        return None
    from app.services.context import context_brief

    prompt = (
        voice_brief(user)
        + "\n\n"
        + record_brief(user)
        + "\n\n"
        + context_brief(user)
        + "\n\n"
        + parse_rules()
        + "\n\nEach appliance is its own card on one unit. A serial, style, or note belongs to that one item. "
        + "A washer in unit 26 does not share a note with any other washer. "
        + "When she says she added, installed, or replaced an appliance in a unit, call record_unit_visit with equipment filled in: kind, brand, model, serial, size, style, color, notes. Equipment saves even without a work title. "
        + "A trip plan is a plan. Gas is a line on that plan, not a place. "
        + "Make-ready units and occupied units are statuses on that unit. "
        + "A note, task, or vendor on one unit stays on that unit. A vendor covers one trade only, and other trades stay with maintenance or their own contractor. "
        + "Call plan_trip or plan_day only for a trip or a day of stops. "
        + "Say who is logged in by using her words; the server stamps her login on the change.\n\nShe said: "
        + (text or "")
    )
    history = chat_history(user, text)
    notes = []
    for row in rows:
        label = provider_spec(row.provider).get("label") or row.provider
        if getattr(row, "backoff_until", None) and row.backoff_until > utcnow():
            notes.append(f"{label} is cooling down.")
            continue
        if budget.would_exceed(row, prompt):
            # Resting the key here beats letting the provider 429 us into a
            # multi-minute lockout with no answer.
            notes.append(_rest_note(label, row))
            continue
        try:
            result = chat_with_tools(row, prompt, history=history)
        except Exception:
            notes.append(f"{label} did not answer.")
            continue
        if not isinstance(result, dict):
            notes.append(f"{label} did not answer.")
            continue
        if result.get("quota"):
            row.backoff_until = backoff_until(result.get("seconds") or 60)
            db.session.commit()
            notes.append(f"{label} is out of quota.")
            continue
        calls = result.get("calls") or []
        prose = (result.get("text") or "").strip()
        if calls or prose:
            tokens = int(result.get("tokens") or 0)
            if tokens <= 0:
                # A provider that omits usage still has to count against the key.
                tokens = budget.estimate_tokens(prompt) + budget.estimate_tokens(prose or json.dumps(calls))
            budget.record(row, tokens)
            return {"calls": calls, "text": prose, "note": ""}
        notes.append(f"{label} did not answer.")
    return {"calls": [], "text": "", "note": " ".join(notes)}


def _rest_note(label: str, row) -> str:
    seconds = budget.resting_seconds(row)
    if seconds >= 60:
        return f"{label} is resting {max(1, seconds // 60)} min to stay under its token limit."
    return f"{label} is resting to stay under its token limit."
