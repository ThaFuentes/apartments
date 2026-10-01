"""Gemini vision helper for equipment nameplates."""
from __future__ import annotations
import requests
from app.services.gemini.client import _generate
from app.services.gemini.common import QuotaError

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
    parsed["tokens"] = int(result.get("tokens") or 0)
    if not parsed.get("kind") and not any(parsed.get(k) for k in ("brand", "model", "serial", "size")):
        parsed = empty()
        parsed["ok"] = True
        parsed["tokens"] = int(result.get("tokens") or 0)
        parsed["confidence"] = 0.2
        parsed["missing"] = ["kind", "brand", "model", "serial"]
    return parsed

