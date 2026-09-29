"""Gemini HTTP generation client and quota backoff."""
from __future__ import annotations
from datetime import timedelta
import requests
from app.services.clock import utcnow
from app.services.gemini.tools import BASE, CHAT_RULES, TOOL_DECLS
from app.services.gemini.common import QuotaError, retry_after

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

