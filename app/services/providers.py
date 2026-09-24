"""Bring-your-own-key models. Gemini, Groq, OpenAI, Grok, Claude, and a few others."""
from __future__ import annotations

import json

import requests

from app.builddb.builddb import db
from app.models import ApiCredential, User
from app.services.clock import utcnow
from app.services.crypto import decrypt_text, encrypt_text, last4
from app.services.gemini import CHAT_RULES, TOOL_DECLS, backoff_until, complete as gemini_complete, read_nameplate, resolve_model

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

ROLE_LABELS = {"owner": "owner", "field": "employee", "viewer": "boss"}


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


def _openai_call(base: str, api_key: str, model: str, messages: list, tools: bool, timeout: int) -> dict:
    url = base.rstrip("/") + "/chat/completions"
    body = {"model": model, "messages": messages, "temperature": 0.2}
    if tools:
        body["tools"] = openai_tools()
        body["tool_choice"] = "auto"
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
    return {"ok": True, "text": message.get("content") or "", "calls": calls}


def _anthropic_call(api_key: str, model: str, text: str, image: bytes | None, mime: str, timeout: int) -> dict:
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
        json={"model": model, "max_tokens": 800, "tools": tools, "messages": [{"role": "user", "content": content}]},
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
    return {"ok": True, "text": "\n".join(texts), "calls": calls}


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
    """The whole saved thread, not counting the line she just sent. Clear is the only reset."""
    from app.models import ChatMessage

    rows = ChatMessage.query.filter_by(user_id=user.id).order_by(ChatMessage.id.asc()).all()
    if rows and rows[-1].role == "user" and (rows[-1].body or "").strip() == (current or "").strip():
        rows = rows[:-1]
    return [{"role": row.role, "body": (row.body or "")[:1500]} for row in rows]


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
        return gemini_complete(api_key, model, text, timeout=timeout, history=history)
    if not model:
        return {"ok": False, "error": "Pick a model for this key."}
    messages = _with_history([{"role": "system", "content": CHAT_RULES}], history, text)
    try:
        if spec.get("kind") == "anthropic":
            return _anthropic_call(api_key, model, text, None, "", timeout)
        return _openai_call(_base(row), api_key, model, messages, True, timeout)
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
    try:
        if spec.get("kind") == "anthropic":
            result = _anthropic_call(api_key, model, prompt, image, mime, timeout)
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
            )
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)}
    if not result.get("ok"):
        return result
    from app.services.equipment import plate_from_json

    parsed = plate_from_json(result.get("text") or "")
    parsed["ok"] = True
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
    owner = user if getattr(user, "role", "") == "owner" else User.query.filter_by(role="owner").order_by(User.id.asc()).first()
    if not owner:
        return []
    rows = ApiCredential.query.filter_by(user_id=owner.id).order_by(ApiCredential.id.asc()).all()
    usable = [row for row in rows if getattr(row, "active", True)]
    usable.sort(key=lambda row: (row.use_order or 99, 0 if getattr(row, "preferred", False) else 1, row.id))
    return usable


def voice_brief() -> str:
    """Name and tone from Settings. This is what the chat is supposed to sound like."""
    from app.services.records import site_profile

    profile = site_profile()
    name = ((profile.assistant_name if profile else "") or "Apt").strip() or "Apt"
    tone = ((profile.tone if profile else "") or "").strip()
    ask = ((profile.always_ask if profile else "") or "").strip()
    voice = ((profile.report_voice if profile else "") or "").strip()
    lines = [f"Your name is {name}. Use that name if you introduce yourself."]
    if tone:
        lines.append(f"Talk this way: {tone}.")
    if ask:
        lines.append(f"Always ask about: {ask}.")
    else:
        lines.append("Do not add extra questions.")
    if voice:
        lines.append(f"When a report is written, use this voice: {voice}.")
    return "\n".join(lines)


def record_brief() -> str:
    """What she already has, so the model can see duplicates instead of guessing."""
    from app.models import Equipment, Job, Property

    props = Property.query.filter(Property.deleted_at.is_(None)).order_by(Property.name.asc()).limit(40).all()
    lines = ["Her record:"]
    if not props:
        lines.append("No properties yet.")
    names = {}
    for prop in props:
        city = prop.city.name if prop.city else ""
        region = prop.city.region if prop.city else ""
        where = ", ".join(bit for bit in (city, region) if bit)
        lines.append(f"property {prop.id}: {prop.name} | {where or 'no city'} | {prop.address or 'no address'}")
        names[prop.name.lower()] = names.get(prop.name.lower(), 0) + 1
    dupes = [name for name, count in names.items() if count > 1]
    if dupes:
        lines.append("Same name more than once: " + ", ".join(dupes))
    jobs = Job.query.filter(Job.deleted_at.is_(None)).order_by(Job.id.desc()).limit(8).all()
    for job in jobs:
        lines.append(f"job {job.id}: {job.title} at property {job.property_id}")
    gear = Equipment.query.filter(Equipment.deleted_at.is_(None)).order_by(Equipment.id.desc()).limit(6).all()
    for item in gear:
        bits = " ".join(bit for bit in (item.brand, item.style, item.kind, item.serial_number) if bit)
        unit = item.unit.unit_number if item.unit else "-"
        note = f" note: {item.notes[:80]}" if item.notes else ""
        lines.append(f"appliance {item.id}: {bits} unit {unit} property {item.property_id}{note}")
    return "\n".join(lines)[:3500]


def collect_tool_calls(user, text: str):
    """Try each saved key in order. Stop on a real answer. Local chat runs only after every key fails."""
    rows = keys_for(user)
    if not rows:
        return None
    prompt = (
        voice_brief()
        + "\n\n"
        + record_brief()
        + "\n\nEach appliance is its own card on one unit. A serial, style, or note belongs to that one item. "
        + "A washer in unit 26 does not share a note with any other washer. "
        + "A trip plan is a plan. Gas is a line on that plan, not a place. "
        + "Make-ready units and occupied units are statuses on that unit. "
        + "A note, task, or vendor on one unit stays on that unit. "
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
            return {"calls": calls, "text": prose, "note": ""}
        notes.append(f"{label} did not answer.")
    return {"calls": [], "text": "", "note": " ".join(notes)}
