"""The first question: what would you like to do. It writes nothing."""
from __future__ import annotations

from app.services.talk.guide.choices import TYPE_KEY, pick, typed
from app.services.talk.guide.hear import (
    finish_sentence,
    is_greeting,
    is_skip,
    make_ready_sentence,
    norm,
)
from app.services.talk.guide.script import Job, Step

_ASKS = {
    "what would you like to do",
    "what do you want to do",
    "what can you do",
    "what can i do",
    "show me the menu",
    "show the menu",
    "walk me through",
    "walk me through it",
}


def _menu_request(text: str) -> bool:
    raw = norm(text)
    if not raw or finish_sentence(raw) or make_ready_sentence(raw):
        return False
    if is_greeting(raw) or raw in _ASKS:
        return True
    if raw in {"guide me", "guide me through", "help me"} or raw.startswith("guide me "):
        return True
    return False


def matches(text: str) -> bool:
    return _menu_request(text)


def absorb(user, text: str, filled: dict) -> dict:
    return dict(filled)


def allow(user) -> str:
    return ""


def review(user, filled: dict) -> str:
    return "What would you like to do?"


def apply_payload(user, filled: dict) -> dict:
    return {}


def changes(user, filled: dict) -> list[dict]:
    return []


# One short screen. The next question comes after they pick. Not every job at once.
_AREAS = (
    {"key": "make_ready", "label": "Make ready"},
    {"key": "vendor", "label": "Vendor"},
    {"key": "units", "label": "Units"},
    {"key": "equipment", "label": "Equipment"},
    {"key": "more", "label": "More"},
    {"key": TYPE_KEY, "label": "I'll type it"},
)
_MORE = (
    {"key": "plans", "label": "Plans"},
    {"key": "places", "label": "Places"},
    {"key": "money", "label": "Money"},
    {"key": "reports", "label": "Reports"},
    {"key": "people", "label": "People"},
    {"key": "office", "label": "Office"},
)
_VERBS = {
    "vendor": (("complete", "Complete"), ("out", "Out"), ("note", "Note"), ("add", "Add")),
    "units": (("status", "Status"), ("note", "Note"), ("parts", "Parts")),
    "equipment": (("move", "Move"), ("swap", "Swap"), ("replace", "Replace"), ("add", "Add")),
    "plans": (("day", "Day"), ("arrive", "Arrive"), ("end", "End")),
    "places": (("add", "Add"), ("change", "Change"), ("remove", "Remove")),
    "money": (("gas", "Gas"), ("miles", "Miles"), ("odometer", "Odometer")),
    "reports": (("question", "Question"), ("weekly", "Weekly"), ("property", "Property")),
    "people": (("add", "Add"), ("change", "Change"), ("access", "Access")),
    "office": (("map", "Map"), ("region", "Region"), ("watch", "Watch")),
}
_PROMPTS = {
    ("vendor", "out"): "Which unit, and who is checking out?",
    ("vendor", "note"): "Which unit, and what should the note say?",
    ("vendor", "add"): "Which unit, and who is the vendor?",
    ("units", "status"): "Which unit, and is it occupied, a make ready, or vacant?",
    ("units", "note"): "Which unit, and what should the note say?",
    ("units", "parts"): "Which unit, and what part was used?",
    ("equipment", "move"): "What are you moving, and from which unit to which unit?",
    ("equipment", "swap"): "What are you swapping, and between which units?",
    ("equipment", "replace"): "What are you replacing, and in which unit?",
    ("equipment", "add"): "What are you adding, and to which unit?",
    ("plans", "day"): "Where are you going, and what day?",
    ("plans", "arrive"): "Which property are you at?",
    ("plans", "end"): "End the visit, or end the day?",
    ("places", "add"): "What is the property name, and what city?",
    ("places", "change"): "Which property, and what should change?",
    ("places", "remove"): "Which property should be removed?",
    ("money", "gas"): "Where was the gas, and how much?",
    ("money", "miles"): "How many miles?",
    ("money", "odometer"): "What is the odometer reading?",
    ("reports", "question"): "What do you want to know?",
    ("reports", "weekly"): "Who is the weekly report for?",
    ("reports", "property"): "Which property is the report for?",
    ("people", "add"): "Who are you adding, and what is their role?",
    ("people", "change"): "Who is it, and what should change?",
    ("people", "access"): "Who should get access, and to which property?",
    ("office", "map"): "Which property's map?",
    ("office", "region"): "Which region, and what should change?",
    ("office", "watch"): "What should the security watch do?",
}
_NAMES = {
    "make ready": "make_ready",
    "makeready": "make_ready",
    "vendor": "vendor",
    "vendors": "vendor",
    "units": "units",
    "unit": "units",
    "equipment": "equipment",
    "gear": "equipment",
    "more": "more",
    "plans": "plans",
    "plan": "plans",
    "places": "places",
    "place": "places",
    "property": "places",
    "properties": "places",
    "money": "money",
    "gas": "money",
    "reports": "reports",
    "report": "reports",
    "people": "people",
    "office": "office",
}


def _choices(pairs) -> list[dict]:
    rows = [{"key": key, "label": label} for key, label in pairs]
    rows.append({"key": TYPE_KEY, "label": "I'll type it"})
    return rows


def _area_name(text: str) -> str:
    raw = norm(text)
    if raw in _NAMES:
        return _NAMES[raw]
    for word, key in _NAMES.items():
        if f" {word} " in f" {raw} ":
            return key
    return ""


def _switch_to(user, text: str, job_id: str) -> dict:
    from app.services.talk.guide.catalog import load

    job = load(job_id)
    if job is None:
        return {"miss": True}
    return {"switch": job.id, "filled": job.absorb(user, text, {}), "said": ""}


def ask_area(user, filled: dict) -> dict:
    if filled.get("page") == "more":
        return {"question": "What else?", "choices": [dict(row) for row in _MORE]}
    return {"question": "What would you like to do?", "choices": [dict(row) for row in _AREAS]}


def _picked_key(text: str, choices: list[dict]) -> str:
    picked = pick(text, choices)
    if typed(picked):
        return TYPE_KEY
    if picked and picked.get("key") not in (None, "", TYPE_KEY):
        return str(picked["key"])
    if len(norm(text).split()) <= 2:
        return _area_name(text)
    return ""


def take_area(user, text: str, filled: dict, choices: list[dict]) -> dict:
    from app.services.talk.guide.catalog import match

    key = _picked_key(text, choices)
    if key == TYPE_KEY:
        return {"again": True, "expect_type": True, "question": "Type what you would like to do.", "choices": []}
    if key == "more":
        return {"again": True, "patch": {"page": "more"}, "question": "What else?", "choices": [dict(row) for row in _MORE]}
    if key == "make_ready" or (make_ready_sentence(text) and len(norm(text).split()) <= 3):
        return {"value": "make_ready"}
    if key in _VERBS:
        return {"value": key}
    job = match(text)
    if job is not None and job.id != "guide_menu":
        return {"switch": job.id, "filled": job.absorb(user, text, {}), "said": ""}
    if len(norm(text).split()) >= 3:
        return {"release": True}
    return {"miss": True}


def ask_verb(user, filled: dict) -> dict:
    area = filled.get("area") or ""
    if area == "make_ready":
        return {"switch": "make_ready", "filled": {}, "said": ""}
    verbs = _VERBS.get(area) or ()
    label = next((row["label"] for row in (*_AREAS, *_MORE) if row["key"] == area), "that")
    return {"question": f"What about {label.lower()}?", "choices": _choices(verbs)}


def take_verb(user, text: str, filled: dict, choices: list[dict]) -> dict:
    picked = pick(text, choices)
    if typed(picked):
        return {"again": True, "expect_type": True, "question": "Say what you want to do.", "choices": []}
    key = str(picked.get("key")) if picked and picked.get("key") not in (None, "", TYPE_KEY) else ""
    if not key:
        raw = norm(text)
        for verb, _label in _VERBS.get(filled.get("area") or "", ()):
            if raw == verb or raw == norm(_label):
                key = verb
                break
    if not key:
        if len(norm(text).split()) >= 3:
            return {"release": True}
        return {"miss": True}
    if filled.get("area") == "vendor" and key == "complete":
        return _switch_to(user, text, "finish_vendor_trade")
    return {"value": key}


def ask_say(user, filled: dict) -> dict:
    prompt = _PROMPTS.get((filled.get("area") or "", filled.get("verb") or "")) or "Say what you want to do."
    return {"question": prompt, "choices": []}


def take_say(user, text: str, filled: dict, choices: list[dict]) -> dict:
    if is_skip(text) or len(norm(text).split()) < 2:
        return {"miss": True, "preface": "Say it in a short sentence."}
    return {"release": True}


MENU = Job(
    id="guide_menu",
    tool="guide_menu",
    matches=matches,
    absorb=absorb,
    allow=allow,
    review=review,
    apply_payload=apply_payload,
    changes=changes,
    steps=(
        Step("area", True, ask_area, take_area, ("page", "verb")),
        Step("verb", True, ask_verb, take_verb, ()),
        Step("say", False, ask_say, take_say, ()),
    ),
)
