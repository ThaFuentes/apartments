"""Free-compatible Gemini model selection and health checks."""
from __future__ import annotations
import re
import requests
from app.services.gemini.tools import BASE, FREE_BLOCK, FALLBACK_FREE
from app.services.gemini.common import QuotaError, retry_after
from app.services.gemini.client import _generate

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

