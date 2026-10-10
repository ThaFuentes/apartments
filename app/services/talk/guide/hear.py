"""Spelling, yes, skip, and trade words. No model and no database."""
from __future__ import annotations

import difflib
import re

_YES = {
    "yes", "yeah", "yep", "yup", "y", "correct", "uh huh", "uhhuh",
    "thats right", "that's right", "that is right",
}
_NO = {"no", "nope", "nah", "wrong"}
_SKIP = {"skip", "idk", "i dont know", "i don't know", "dont know", "don't know", "not sure", "dunno", "pass"}
_BACK = {"back", "go back", "previous"}
_CANCEL = {"cancel", "never mind", "nevermind", "start over", "stop"}

# Whole phrases people type. Longest first when scanning a sentence.
_TRADE_ALIASES = (
    ("thrash out", "trashout"),
    ("thrashout", "trashout"),
    ("trsh out", "trashout"),
    ("trshout", "trashout"),
    ("trash out", "trashout"),
    ("trashout", "trashout"),
    ("paynt", "paint"),
    ("pait", "paint"),
    ("pant", "paint"),
    ("painter", "paint"),
    ("painters", "paint"),
    ("painting", "paint"),
    ("paint", "paint"),
    ("carpet cleaning", "carpet"),
    ("carpet", "carpet"),
    ("flooring", "floors"),
    ("floors", "floors"),
    ("floor", "floors"),
    ("bug spray", "spray"),
    ("spraying", "spray"),
    ("spray", "spray"),
    ("resurface", "resurfacing"),
    ("resurfacing", "resurfacing"),
    ("punchlist", "punch"),
    ("punch list", "punch"),
    ("punch", "punch"),
    ("cleaners", "clean"),
    ("cleaner", "clean"),
    ("cleaning", "clean"),
    ("clean", "clean"),
    ("appliances", "appliances"),
    ("appliance", "appliances"),
    ("keys", "keys"),
    ("key", "keys"),
)


def norm(text: str) -> str:
    raw = (text or "").lower().replace("'", "'")
    raw = raw.replace("'", "")
    raw = re.sub(r"[^a-z0-9#]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _whole(phrase: str, text: str) -> bool:
    return bool(re.search(rf"(^| ){re.escape(phrase)}( |$)", text))


def is_yes(text: str) -> bool:
    return norm(text) in _YES


def is_no(text: str) -> bool:
    return norm(text) in _NO


def is_skip(text: str) -> bool:
    return norm(text) in _SKIP


def is_back(text: str) -> bool:
    return norm(text) in _BACK


def is_cancel(text: str) -> bool:
    return norm(text) in _CANCEL


def finish_sentence(text: str) -> bool:
    """The finish-a-trade walk already owns this sentence."""
    raw = norm(text)
    if not raw:
        return False
    if re.search(r"\bis done\b", raw) and trade_guess(raw).get("slug"):
        return True
    if "guide me" in raw and (trade_guess(raw).get("slug") or re.search(r"\b(?:complet\w*|finish\w*|done)\b", raw)):
        return True
    return False


def make_ready_sentence(text: str) -> bool:
    """A make-ready walk. A finish sentence keeps the finish walk."""
    raw = norm(text)
    if not raw or finish_sentence(raw):
        return False
    if re.search(r"\bmake\s*ready\b", raw) or "makeready" in raw.replace(" ", ""):
        return True
    if "make" not in raw.split():
        return False
    return any(difflib.SequenceMatcher(None, word, "ready").ratio() >= 0.8 for word in raw.split())


def is_greeting(text: str) -> bool:
    raw = norm(text)
    if raw in {"hi", "hello", "hey", "help", "menu", "yo"}:
        return True
    return raw.startswith(("hi ", "hello ", "hey ")) and len(raw.split()) <= 4


def trade_guess(text: str) -> dict:
    """One trade, several, or none. A close call between two trades does not pick."""
    raw = norm(text)
    if not raw:
        return {"slug": ""}
    found: list[str] = []
    for phrase, slug in _TRADE_ALIASES:
        if _whole(phrase, raw) and slug not in found:
            found.append(slug)
    if len(found) == 1:
        return {"slug": found[0]}
    if len(found) > 1:
        return {"slug": "", "slugs": found}
    scores = []
    for phrase, slug in _TRADE_ALIASES:
        if len(phrase) < 4:
            continue
        ratio = difflib.SequenceMatcher(None, raw, phrase).ratio()
        scores.append((ratio, slug))
    scores.sort(reverse=True)
    if not scores or scores[0][0] < 0.72:
        return {"slug": ""}
    top, second = scores[0], scores[1] if len(scores) > 1 else (0, "")
    if second[0] >= 0.72 and top[0] - second[0] < 0.08 and top[1] != second[1]:
        return {"slug": "", "slugs": [top[1], second[1]]}
    if top[0] >= 0.82:
        return {"slug": top[1]}
    return {"slug": ""}


def close_names(text: str, names: list[str]) -> dict:
    """One saved name, or the two closest when a company record must not guess."""
    raw = norm(text)
    cleaned = [name for name in names if norm(name)]
    if not raw or not cleaned:
        return {"name": ""}
    exact = [name for name in cleaned if norm(name) == raw or raw in norm(name).split()]
    if len(exact) == 1:
        return {"name": exact[0]}
    if len(exact) > 1:
        return {"name": "", "names": exact[:2]}
    scores = sorted(
        ((difflib.SequenceMatcher(None, raw, norm(name)).ratio(), name) for name in cleaned),
        reverse=True,
    )
    top, second = scores[0], scores[1] if len(scores) > 1 else (0, "")
    if top[0] < 0.72:
        return {"name": ""}
    if second[0] >= 0.72 and top[0] - second[0] < 0.08:
        return {"name": "", "names": [top[1], second[1]]}
    if top[0] >= 0.8:
        return {"name": top[1]}
    return {"name": ""}


def clock_phrase(text: str) -> dict | None:
    """A time of day from plain words. The label is what we say back."""
    raw = norm(text)
    if not raw:
        return None
    if raw in {"just now", "now", "right now"}:
        return {"kind": "now", "label": "just now"}
    if "this morning" in raw or raw in {"morning", "c"}:
        return None if raw == "c" else {"kind": "morning", "label": "this morning"}
    if raw in {"yesterday", "b"} and raw != "b":
        return {"kind": "yesterday", "label": "yesterday"}
    if "yesterday" in raw:
        return {"kind": "yesterday", "label": "yesterday"}
    if raw == "today" or "today" in raw and "morning" not in raw:
        if re.search(r"\d", raw):
            pass
        else:
            return {"kind": "today", "label": "today"}
    if "lunch" in raw:
        return {"kind": "lunch", "label": "around lunch"}
    match = re.search(r"\b(?:at|around|about)?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", raw)
    if not match:
        return None
    if match.group(0).strip() == raw and raw.isdigit() and len(raw) == 1:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    half = match.group(3) or ""
    if hour > 23 or minute > 59:
        return None
    if half == "pm" and hour < 12:
        hour += 12
    elif half == "am" and hour == 12:
        hour = 0
    elif not half and 1 <= hour <= 7:
        hour += 12
    label_hour = hour - 12 if hour > 12 else hour
    suffix = "pm" if hour >= 12 else "am"
    if label_hour == 0:
        label_hour = 12
    label = f"about {label_hour}:{minute:02d} {suffix}"
    return {"kind": "clock", "hour": hour, "minute": minute, "label": label}


def unit_numbers(text: str) -> list[str]:
    """Unit-like numbers. A one-digit hour and a year are not units."""
    raw = norm(text)
    found = []
    for match in re.finditer(r"\b(\d{2,5})\b", raw):
        number = match.group(1)
        if len(number) == 4 and number.startswith(("19", "20")):
            continue
        if number not in found:
            found.append(number)
    return found
