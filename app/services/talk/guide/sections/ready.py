"""Make a unit ready. New or existing, then one next step. The save uses the board writers."""
from __future__ import annotations

import difflib
import re

from app.services.ready import job_label, spoken_job
from app.services.talk.guide.choices import TYPE_KEY, pick, typed
from app.services.talk.guide.hear import (
    finish_sentence,
    is_skip,
    make_ready_sentence,
    norm,
    trade_guess,
    unit_numbers,
)
from app.services.talk.guide.script import Job, Step
from app.services.talk.guide.slots import units_for, units_named, units_similar

_KIND_NEW = (
    {"key": "new", "label": "A new make ready"},
    {"key": "existing", "label": "An existing make ready"},
    {"key": TYPE_KEY, "label": "I'll type it"},
)


def _pretty(trade: str) -> str:
    words = spoken_job(trade)
    if not words:
        return "That trade"
    return words[:1].upper() + words[1:]


def _unit_extra(choice: dict) -> dict:
    return {
        "unit_id": choice.get("unit_id"),
        "unit_number": choice.get("unit_number") or "",
        "property_id": choice.get("property_id"),
        "property_name": choice.get("property_name") or "",
        "city": choice.get("city") or "",
        "occupancy": choice.get("occupancy") or "",
    }


def _kind_word(text: str) -> str:
    raw = norm(text)
    if not raw:
        return ""
    if re.search(r"\bnew\b", raw) and not re.search(r"\bexisting\b", raw):
        return "new"
    if re.search(r"\b(existing|current|already)\b", raw):
        return "existing"
    for word in raw.split():
        if difflib.SequenceMatcher(None, word, "existing").ratio() >= 0.75:
            return "existing"
        if difflib.SequenceMatcher(None, word, "new").ratio() >= 0.99 and word != "now":
            return "new"
    return ""


def _pure_number(text: str) -> str:
    numbers = unit_numbers(text)
    if len(numbers) == 1 and norm(text) == numbers[0]:
        return numbers[0]
    return ""


def _with_type(rows: list[dict], label: str = "I'll type the number") -> list[dict]:
    shown = list(rows[:5])
    shown.append({"key": TYPE_KEY, "label": label})
    return shown


def _next_choices(kind: str, trade: str = "") -> list[dict]:
    pretty = _pretty(trade) if trade else ""
    if kind == "existing":
        rows = [
            {"key": "finish", "label": f"Finish {pretty}" if pretty else "Finish"},
            {"key": "add", "label": f"Add {pretty}" if pretty else "Add work"},
        ]
    else:
        rows = [
            {"key": "mark", "label": "Just mark it"},
            {"key": "add", "label": f"Add {pretty}" if pretty else "Add work"},
        ]
    rows.append({"key": TYPE_KEY, "label": "I'll type it"})
    return rows


def matches(text: str) -> bool:
    return make_ready_sentence(text) and not finish_sentence(text)


def absorb(user, text: str, filled: dict) -> dict:
    """Keep a unit or a new-or-existing word when the sentence already said it."""
    data = dict(filled)
    kind = _kind_word(text)
    if kind and not data.get("kind"):
        data["kind"] = kind
    if not data.get("unit_id"):
        numbers = unit_numbers(text)
        if len(numbers) == 1:
            exact = units_named(user, numbers[0])
            if len(exact) == 1:
                data["unit"] = exact[0]["key"]
                data.update(_unit_extra(exact[0]))
                if not data.get("kind"):
                    data["kind"] = "existing" if exact[0].get("occupancy") == "make_ready" else "new"
    if not data.get("next"):
        raw = norm(text)
        guess = trade_guess(text)
        if guess.get("slug") and re.search(r"\b(needs|need|add)\b", raw):
            data["next"] = "add"
            data["trade"] = guess["slug"]
            data["trade_label"] = job_label(guess["slug"])
        elif data.get("kind") == "new" and data.get("unit_id") and re.search(r"\bmake\s*ready\b", raw):
            data["next"] = "mark"
    return data


def allow(user) -> str:
    from app.services.access import has_capability

    if has_capability(user, "write_maintenance"):
        return ""
    return "You can't change make-ready work from this login."


def review(user, filled: dict) -> str:
    number = filled.get("unit_number") or "that unit"
    place = filled.get("property_name") or "that property"
    if filled.get("next") == "add":
        trade = _pretty(filled.get("trade") or filled.get("trade_label") or "").lower()
        return f"Make unit {number} at {place} a make ready and add {trade}."
    if filled.get("kind") == "existing":
        return f"Unit {number} at {place} is already a make ready."
    return f"Mark unit {number} at {place} a make ready."


def apply_payload(user, filled: dict) -> dict:
    action = filled.get("next") or "mark"
    return {
        "property_id": filled.get("property_id"),
        "property_name": filled.get("property_name") or "",
        "city": filled.get("city") or "",
        "unit_id": filled.get("unit_id"),
        "unit_number": filled.get("unit_number") or "",
        "action": action,
        "job": filled.get("trade_label") or filled.get("trade") or "",
        "trade": filled.get("trade") or "",
    }


def changes(user, filled: dict) -> list[dict]:
    number = filled.get("unit_number") or ""
    place = filled.get("property_name") or ""
    before = "Make ready" if filled.get("occupancy") == "make_ready" or filled.get("kind") == "existing" else "Not a make ready"
    rows = [
        {"field": "Unit", "before": before, "after": f"{number} at {place}".strip(), "step": "unit"},
        {
            "field": "Kind",
            "before": "—",
            "after": "Existing make ready" if filled.get("kind") == "existing" else "New make ready",
            "step": "kind",
        },
    ]
    if filled.get("next") == "add":
        rows.append({"field": "Work", "before": "—", "after": _pretty(filled.get("trade") or ""), "step": "trade"})
    else:
        rows.append({"field": "Work", "before": "—", "after": "Just mark it a make ready", "step": "next"})
    return rows


def ask_kind(user, filled: dict) -> dict:
    if filled.get("kind") in {"new", "existing"}:
        return {"value": filled["kind"]}
    return {"question": "Is this a new make ready or an existing one?", "choices": list(_KIND_NEW)}


def take_kind(user, text: str, filled: dict, choices: list[dict]) -> dict:
    if is_skip(text):
        return {"miss": True}
    picked = pick(text, choices)
    if typed(picked):
        return {"again": True, "expect_type": True, "question": "Type new or existing.", "choices": []}
    kind = ""
    if picked and picked.get("key") in {"new", "existing"}:
        kind = picked["key"]
    if not kind:
        kind = _kind_word(text)
    if not kind:
        return {"miss": True}
    word = "a new make ready" if kind == "new" else "an existing make ready"
    return {"value": kind, "said": f"Okay, {word}."}


def _unit_again(rows: list[dict]) -> dict:
    preface = "I found more than one. I won't guess." if len(rows) > 1 else "I won't pick a different unit number."
    return {"again": True, "question": "Which unit?", "choices": _with_type(rows), "preface": preface}


def _list_for(user, kind: str) -> list[dict]:
    return units_for(user, ready=(kind == "existing"))


def ask_unit(user, filled: dict) -> dict:
    if filled.get("unit_id"):
        return {"value": filled["unit_id"]}
    kind = filled.get("kind") or "existing"
    rows = _list_for(user, kind)
    if rows:
        question = "Which unit?" if kind == "existing" else "Which unit should become a make ready?"
        return {"question": question, "choices": _with_type(rows)}
    if kind == "existing":
        return {
            "question": "I don't see an existing make ready. Start a new one?",
            "choices": [
                {"key": "switch_new", "label": "Yes, a new make ready"},
                {"key": TYPE_KEY, "label": "I'll type the number"},
            ],
            "preface": "I don't see an existing make ready.",
        }
    return {
        "question": "Every unit I can see is already a make ready. Work on one of those?",
        "choices": [
            {"key": "switch_existing", "label": "Yes, an existing one"},
            {"key": TYPE_KEY, "label": "I'll type the number"},
        ],
    }


def _accept_unit(filled: dict, choice: dict) -> dict:
    extra = _unit_extra(choice)
    number = extra.get("unit_number") or ""
    ready = extra.get("occupancy") == "make_ready"
    said = f"Unit {number}."
    if filled.get("kind") == "existing" and not ready:
        extra["kind"] = "new"
        said = f"Unit {number} is not a make ready yet. Starting a new one."
    elif filled.get("kind") == "new" and ready:
        extra["kind"] = "existing"
        said = f"Unit {number} is already a make ready."
    return {"value": choice["key"], "extra": extra, "said": said}


def take_unit(user, text: str, filled: dict, choices: list[dict]) -> dict:
    if is_skip(text):
        return {"miss": True}
    if not _pure_number(text):
        picked = pick(text, choices)
        if typed(picked):
            return {"again": True, "expect_type": True, "question": "Type the unit number.", "choices": []}
        if picked and picked.get("key") == "switch_new":
            rows = _list_for(user, "new")
            return {
                "again": True,
                "patch": {"kind": "new"},
                "question": "Which unit should become a make ready?",
                "choices": _with_type(rows),
                "preface": "Okay, a new make ready.",
            }
        if picked and picked.get("key") == "switch_existing":
            rows = _list_for(user, "existing")
            return {
                "again": True,
                "patch": {"kind": "existing"},
                "question": "Which unit?",
                "choices": _with_type(rows),
                "preface": "Okay, an existing make ready.",
            }
        if picked and picked.get("unit_id"):
            return _accept_unit(filled, picked)
    numbers = unit_numbers(text)
    if len(numbers) != 1:
        return {"miss": True}
    exact = units_named(user, numbers[0])
    if len(exact) == 1:
        return _accept_unit(filled, exact[0])
    many = exact or units_similar(user, numbers[0])
    if many:
        return _unit_again(many)
    return {"miss": True, "preface": f"I don't see unit {numbers[0]}."}


def _finish_switch(filled: dict) -> dict:
    kept = {
        "unit": filled.get("unit") or filled.get("unit_id"),
        "unit_id": filled.get("unit_id"),
        "unit_number": filled.get("unit_number") or "",
        "property_id": filled.get("property_id"),
        "property_name": filled.get("property_name") or "",
        "city": filled.get("city") or "",
    }
    if filled.get("trade"):
        kept["trade"] = filled["trade"]
        kept["trade_label"] = filled.get("trade_label") or job_label(filled["trade"])
    number = kept.get("unit_number") or "that unit"
    return {"switch": "finish_vendor_trade", "filled": kept, "said": f"Unit {number}."}


def ask_next(user, filled: dict) -> dict:
    if filled.get("next") in {"mark", "add"}:
        return {"value": filled["next"]}
    number = filled.get("unit_number") or "that unit"
    return {
        "question": f"What should we do with unit {number}?",
        "choices": _next_choices(filled.get("kind") or "new", filled.get("trade") or ""),
    }


def take_next(user, text: str, filled: dict, choices: list[dict]) -> dict:
    if is_skip(text):
        return {"miss": True}
    picked = pick(text, choices)
    if typed(picked):
        return {"again": True, "expect_type": True, "question": "Type what you want to do with this unit.", "choices": []}
    key = picked.get("key") if picked else ""
    raw = norm(text)
    if not key:
        if re.search(r"\b(finish|done|completed)\b", raw):
            key = "finish"
        elif re.search(r"\b(add|needs|need)\b", raw):
            key = "add"
        elif re.search(r"\b(just mark|mark it|only mark|no work)\b", raw):
            key = "mark"
    guess = trade_guess(text)
    if guess.get("slug") and key not in {"finish", "add"}:
        slug = guess["slug"]
        return {
            "again": True,
            "patch": {"trade": slug, "trade_label": job_label(slug)},
            "question": f"Should I finish {_pretty(slug).lower()} or add it?",
            "choices": [
                {"key": "finish", "label": f"Finish {_pretty(slug)}"},
                {"key": "add", "label": f"Add {_pretty(slug)}"},
            ],
            "preface": "That names the work. I won't guess which.",
        }
    if key == "finish":
        if guess.get("slug") and not filled.get("trade"):
            filled = dict(filled)
            filled["trade"] = guess["slug"]
            filled["trade_label"] = job_label(guess["slug"])
        return _finish_switch(filled)
    if key == "add":
        extra = {}
        if guess.get("slug"):
            extra = {"trade": guess["slug"], "trade_label": job_label(guess["slug"])}
        return {"value": "add", "extra": extra, "said": "We'll add the work."}
    if key == "mark":
        return {"value": "mark", "said": "Just mark it a make ready."}
    return {"miss": True}


def ask_trade(user, filled: dict) -> dict:
    if filled.get("next") != "add":
        return {"value": filled.get("trade") or ""}
    if filled.get("trade"):
        return {"value": filled["trade"], "extra": {"trade_label": filled.get("trade_label") or job_label(filled["trade"])}}
    from app.services.ready import READY_JOBS

    choices = [{"key": slug, "label": _pretty(slug)} for slug, _label in READY_JOBS[:5]]
    choices.append({"key": TYPE_KEY, "label": "I'll type it"})
    return {"question": "Which work does it need?", "choices": choices}


def take_trade(user, text: str, filled: dict, choices: list[dict]) -> dict:
    if is_skip(text):
        return {"miss": True}
    picked = pick(text, choices)
    if typed(picked):
        return {"again": True, "expect_type": True, "question": "Type the work it needs.", "choices": []}
    if picked and picked.get("key") not in (None, "", TYPE_KEY):
        return {"value": picked["key"], "extra": {"trade_label": job_label(picked["key"])}, "said": f"{_pretty(picked['key'])}."}
    guess = trade_guess(text)
    if guess.get("slug"):
        return {"value": guess["slug"], "extra": {"trade_label": job_label(guess["slug"])}, "said": f"{_pretty(guess['slug'])}."}
    if guess.get("slugs"):
        rows = [{"key": slug, "label": _pretty(slug)} for slug in guess["slugs"][:5]]
        rows.append({"key": TYPE_KEY, "label": "I'll type it"})
        return {"again": True, "question": "Which work does it need?", "choices": rows, "preface": "That could be more than one trade."}
    return {"miss": True}


def apply_make_ready(user, payload: dict, source: str) -> dict:
    """One confirm. Mark the unit, or add one trade. Occupied units stay occupied."""
    from app.builddb.builddb import db
    from app.models import Unit
    from app.services.ready import add_ready_job

    unit = None
    raw_id = payload.get("unit_id")
    if raw_id not in (None, ""):
        try:
            unit = db.session.get(Unit, int(raw_id))
        except (TypeError, ValueError):
            unit = None
        if unit is not None and unit.deleted_at:
            unit = None
    if unit is None:
        return {"ok": False, "reply": "Which unit, and at which property?"}
    action = (payload.get("action") or "mark").strip()
    if action == "add":
        job = (payload.get("job") or payload.get("trade") or "").strip()
        if not job:
            return {"ok": False, "reply": "Which work should I add?"}
        result = add_ready_job(user, unit, job, source, vendor="")
        if not result.get("ok"):
            return result
        title = spoken_job(job)
        if "already has" in (result.get("reply") or "").lower():
            result["reply"] = f"Saved. Unit {unit.unit_number} already has {title} on the make-ready list."
        else:
            result["reply"] = f"Saved. Unit {unit.unit_number} is a make ready and needs {title}."
        return result
    if (unit.occupancy or "") == "occupied":
        return {"ok": False, "reply": f"Mark unit {unit.unit_number} vacant before starting a make ready."}
    if (unit.occupancy or "") != "make_ready":
        from app.services.board import set_occupancy

        set_occupancy(user, unit, "make_ready", source)
    return {"ok": True, "reply": f"Saved. Unit {unit.unit_number} is a make ready."}


MAKE_READY = Job(
    id="make_ready",
    tool="make_ready_unit",
    matches=matches,
    absorb=absorb,
    allow=allow,
    review=review,
    apply_payload=apply_payload,
    changes=changes,
    steps=(
        Step(
            "kind",
            True,
            ask_kind,
            take_kind,
            ("unit", "unit_id", "unit_number", "property_id", "property_name", "city", "unit_token", "occupancy", "next", "trade", "trade_label"),
            ("unit", "next", "trade"),
        ),
        Step(
            "unit",
            True,
            ask_unit,
            take_unit,
            ("unit_id", "unit_number", "property_id", "property_name", "city", "unit_token", "occupancy"),
        ),
        Step("next", True, ask_next, take_next, ("trade", "trade_label"), ("trade",)),
        Step("trade", False, ask_trade, take_trade, ("trade_label",)),
    ),
)
