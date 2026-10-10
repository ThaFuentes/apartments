"""Finish one vendor trade. Questions live here. The save stays in the field applier."""
from __future__ import annotations

import re

from app.services.ready import READY_JOBS, job_label, match_job, spoken_job
from app.services.talk.guide.choices import TYPE_KEY, pick, typed
from app.services.talk.guide.hear import (
    close_names,
    clock_phrase,
    finish_sentence,
    is_no,
    is_skip,
    is_yes,
    norm,
    trade_guess,
    unit_numbers,
)
from app.services.talk.guide.script import Job, Step
from app.services.talk.guide.slots import (
    clock_stamp,
    make_ready_choices,
    stamp,
    task_for,
    units_named,
    units_similar,
    vendor_names,
)

_STOP = {"they", "started", "finished", "and", "on", "was", "were", "the", "then", "it"}


def _pretty(trade: str) -> str:
    words = spoken_job(trade)
    if not words:
        return "That trade"
    return words[:1].upper() + words[1:]


def _unit_again(rows: list[dict], *, again: bool) -> dict:
    """Show every close unit as a letter. A short number never becomes a different unit."""
    shown = list(rows[:5])
    shown.append({"key": TYPE_KEY, "label": "I'll type the number"})
    preface = "I found more than one. I won't guess." if len(rows) > 1 else "I won't pick a different unit number."
    body = {"question": "Which unit?", "choices": shown, "preface": preface}
    if again:
        body["again"] = True
    return body


def _unit_extra(choice: dict) -> dict:
    return {
        "unit_id": choice.get("unit_id"),
        "unit_number": choice.get("unit_number") or "",
        "property_id": choice.get("property_id"),
        "property_name": choice.get("property_name") or "",
        "city": choice.get("city") or "",
    }


def _trade_choices(user, filled: dict) -> list[dict]:
    from app.models import UnitTask

    choices = []
    seen = set()
    unit_id = filled.get("unit_id")
    if unit_id:
        rows = (
            UnitTask.query.filter_by(unit_id=int(unit_id))
            .filter(UnitTask.deleted_at.is_(None), UnitTask.status.in_(("needed", "vendored")))
            .order_by(UnitTask.id.asc())
            .all()
        )
        for row in rows:
            slug = match_job(row.title or "")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            choices.append({"key": slug, "label": _pretty(slug)})
    if not choices:
        for slug, _label in READY_JOBS[:5]:
            choices.append({"key": slug, "label": _pretty(slug)})
    choices.append({"key": TYPE_KEY, "label": "I'll type it"})
    return choices


def ask_unit(user, filled: dict) -> dict:
    if filled.get("unit_id"):
        return {"value": filled["unit_id"]}
    token = str(filled.get("unit_token") or "")
    if token:
        exact = units_named(user, token)
        if len(exact) == 1:
            return {"value": exact[0]["key"], "extra": _unit_extra(exact[0])}
        many = exact or units_similar(user, token)
        if many:
            return _unit_again(many, again=False)
    return {"question": "Which unit?", "choices": make_ready_choices(user)}


def _pure_number(text: str) -> str:
    numbers = unit_numbers(text)
    if len(numbers) == 1 and norm(text) == numbers[0]:
        return numbers[0]
    return ""


def take_unit(user, text: str, filled: dict, choices: list[dict]) -> dict:
    if is_skip(text):
        return {"miss": True}
    # A bare number is a unit, never a letter and never a piece of a longer number.
    if not _pure_number(text):
        picked = pick(text, choices)
        if typed(picked):
            return {
                "again": True,
                "expect_type": True,
                "question": "Type the unit number.",
                "choices": [],
            }
        if picked and picked.get("unit_id"):
            number = picked.get("unit_number") or ""
            return {"value": picked["key"], "extra": _unit_extra(picked), "said": f"Unit {number}."}
    numbers = unit_numbers(text)
    if len(numbers) != 1:
        return {"miss": True}
    exact = units_named(user, numbers[0])
    if len(exact) == 1:
        return {"value": exact[0]["key"], "extra": _unit_extra(exact[0]), "said": f"Unit {exact[0]['unit_number']}."}
    many = exact or units_similar(user, numbers[0])
    if many:
        return _unit_again(many, again=True)
    return {"miss": True, "preface": f"I don't see unit {numbers[0]}."}


def ask_trade(user, filled: dict) -> dict:
    if filled.get("trade"):
        return {"value": filled["trade"], "extra": {"trade_label": filled.get("trade_label") or job_label(filled["trade"])}}
    return {"question": "Which trade?", "choices": _trade_choices(user, filled)}


def take_trade(user, text: str, filled: dict, choices: list[dict]) -> dict:
    if is_skip(text):
        return {"miss": True}
    picked = pick(text, choices)
    if typed(picked):
        return {"again": True, "expect_type": True, "question": "Type the trade.", "choices": []}
    if picked and picked.get("key") not in (None, "", TYPE_KEY):
        label = job_label(picked["key"])
        return {"value": picked["key"], "extra": {"trade_label": label}, "said": f"{_pretty(picked['key'])}."}
    guess = trade_guess(text)
    if guess.get("slug"):
        label = job_label(guess["slug"])
        return {"value": guess["slug"], "extra": {"trade_label": label}, "said": f"{_pretty(guess['slug'])}."}
    if guess.get("slugs"):
        rows = [{"key": slug, "label": _pretty(slug)} for slug in guess["slugs"][:5]]
        rows.append({"key": TYPE_KEY, "label": "I'll type it"})
        return {"again": True, "question": "Which trade?", "choices": rows, "preface": "That could be more than one trade."}
    return {"miss": True}


def _known_vendor(filled: dict) -> str:
    title = filled.get("trade_label") or ""
    task = task_for(int(filled.get("unit_id") or 0), title)
    if task and (task.vendor or "").strip():
        return task.vendor.strip()
    return ""


def ask_vendor(user, filled: dict) -> dict:
    if filled.get("vendor"):
        return {"value": filled["vendor"]}
    known = _known_vendor(filled)
    if known:
        title = spoken_job(filled.get("trade") or filled.get("trade_label") or "") or "that trade"
        number = filled.get("unit_number") or "that unit"
        return {
            "question": f"Unit {number} {title} is already {known}. Was that the vendor?",
            "choices": [
                {"key": known, "label": f"Yes, {known}"},
                {"key": TYPE_KEY, "label": "Someone else"},
            ],
        }
    choices = [{"key": name, "label": name} for name in vendor_names()[:5]]
    choices.append({"key": TYPE_KEY, "label": "I'll type the name"})
    return {"question": "Who did it?", "choices": choices}


def _clean_vendor(text: str) -> str:
    return _as_vendor(" ".join(norm(text).split()[:4]))


def take_vendor(user, text: str, filled: dict, choices: list[dict]) -> dict:
    if is_skip(text):
        return {"miss": True}
    picked = pick(text, choices)
    if typed(picked):
        return {"again": True, "expect_type": True, "question": "Type the vendor's name.", "choices": []}
    if picked and picked.get("key") not in (None, "", TYPE_KEY):
        name = str(picked["key"])
        return {"value": name, "said": f"{name} it is."}
    hit = close_names(text, vendor_names())
    if hit.get("names"):
        rows = [{"key": name, "label": name} for name in hit["names"]]
        rows.append({"key": TYPE_KEY, "label": "I'll type the name"})
        return {"again": True, "question": "Which vendor?", "choices": rows, "preface": "Did you mean one of these?"}
    name = _clean_vendor(text)
    if name:
        return {"value": name, "said": f"{name} it is."}
    return {"miss": True}


def _clock_choices(kind: str) -> list[dict]:
    if kind == "start":
        rows = [
            {"key": "today", "label": "Today"},
            {"key": "yesterday", "label": "Yesterday"},
            {"key": "morning", "label": "This morning"},
        ]
    else:
        rows = [
            {"key": "now", "label": "Just now"},
            {"key": "lunch", "label": "Around lunch"},
        ]
    rows.append({"key": TYPE_KEY, "label": "I'll type the time"})
    return rows


def _store_clock(info: dict, step_id: str) -> dict:
    iso, label = stamp(info)
    if step_id == "start":
        return {"value": iso, "extra": {"check_in": iso, "check_in_label": label}, "said": f"{label[:1].upper()}{label[1:]}."}
    return {"value": iso, "extra": {"check_out": iso, "check_out_label": label}, "said": f"{label[:1].upper()}{label[1:]}."}


def ask_start(user, filled: dict) -> dict:
    if filled.get("check_in"):
        return {"value": filled["check_in"]}
    return {
        "question": "When did they start? There isn't a start time yet.",
        "choices": _clock_choices("start"),
    }


def ask_finish(user, filled: dict) -> dict:
    if filled.get("check_out"):
        return {"value": filled["check_out"]}
    return {"question": "When did they finish?", "choices": _clock_choices("finish")}


def _take_clock(step_id: str, text: str, choices: list[dict]) -> dict:
    if is_skip(text):
        return {"miss": True}
    picked = pick(text, choices)
    if typed(picked):
        return {"again": True, "expect_type": True, "question": "Type the time.", "choices": []}
    if picked and picked.get("key") not in (None, "", TYPE_KEY):
        info = {"kind": picked["key"]}
        if picked["key"] == "now":
            info = {"kind": "now", "label": "just now"}
        elif picked["key"] == "lunch":
            info = {"kind": "lunch", "label": "around lunch"}
        elif picked["key"] == "morning":
            info = {"kind": "morning", "label": "this morning"}
        elif picked["key"] == "yesterday":
            info = {"kind": "yesterday", "label": "yesterday"}
        elif picked["key"] == "today":
            info = {"kind": "today", "label": "today"}
        return _store_clock(info, step_id)
    heard = clock_phrase(text)
    if not heard:
        stamped = clock_stamp(text)
        if not stamped:
            return {"miss": True}
        iso, label = stamped
        extra = {"check_in" if step_id == "start" else "check_out": iso}
        extra["check_in_label" if step_id == "start" else "check_out_label"] = label
        return {"value": iso, "extra": extra, "said": f"{label[:1].upper()}{label[1:]}."}
    return _store_clock(heard, step_id)


def take_start(user, text: str, filled: dict, choices: list[dict]) -> dict:
    return _take_clock("start", text, choices)


def take_finish(user, text: str, filled: dict, choices: list[dict]) -> dict:
    return _take_clock("finish", text, choices)


def ask_on_time(user, filled: dict) -> dict:
    if "on_time" in filled:
        return {"value": filled.get("on_time") or ""}
    return {
        "question": "Were they on time?",
        "choices": [
            {"key": "yes", "label": "Yes"},
            {"key": "no", "label": "No"},
            {"key": "skip", "label": "Skip"},
        ],
    }


def take_on_time(user, text: str, filled: dict, choices: list[dict]) -> dict:
    picked = pick(text, choices)
    if picked:
        if picked["key"] == "skip" or is_skip(picked["key"]):
            return {"value": "", "said": "Skipped."}
        return {"value": picked["key"], "extra": {"on_time": picked["key"]}}
    raw = norm(text)
    if is_skip(text):
        return {"value": "", "said": "Skipped."}
    if re.search(r"\bnot on time\b|\blate\b", raw) or is_no(text):
        return {"value": "no", "extra": {"on_time": "no"}}
    if re.search(r"\bon time\b", raw) or is_yes(text):
        return {"value": "yes", "extra": {"on_time": "yes"}}
    return {"miss": True}


def _as_vendor(hint: str) -> str:
    """A company name. A trade, a clock, or a bare number is not a vendor."""
    hint = (hint or "").strip()
    if not hint or hint.isdigit() or is_skip(hint) or is_yes(hint) or is_no(hint):
        return ""
    if trade_guess(hint).get("slug") or clock_phrase(hint):
        return ""
    hit = close_names(hint, vendor_names())
    if hit.get("name"):
        return hit["name"]
    if hit.get("names"):
        return ""
    if len(hint) <= 4 and hint.isalnum():
        return hint.upper()
    return hint.title()


def _vendor_phrase(text: str) -> str:
    raw = norm(text)
    # "vendor trash out" names the walk. "it was fvs" and "by fvs" name the company.
    match = re.search(r"\b(?:it was|vendor was|by)\s+([a-z0-9][a-z0-9 ]{0,40})", raw)
    if match:
        kept = []
        for word in match.group(1).split():
            if word in _STOP:
                break
            kept.append(word)
        found = _as_vendor(" ".join(kept))
        if found:
            return found
    for name in vendor_names():
        token = norm(name)
        if not token or trade_guess(token).get("slug"):
            continue
        if re.search(rf"\b{re.escape(token)}\b", raw):
            return name
    return ""


def _clause_clock(text: str, words: str) -> tuple[str, str] | None:
    raw = norm(text)
    match = re.search(rf"\b(?:{words})\b(.{{0,48}})", raw)
    if not match:
        return None
    tail = re.split(r"\b(?:and|but|then|finished|finish|started|start)\b", match.group(1), maxsplit=1)[0]
    return clock_stamp(tail)


def _guide_title(text: str) -> bool:
    """'Guide me through the trash out' names the walk. 'Trash out is done' is the fact."""
    raw = norm(text)
    return "guide me" in raw and not re.search(r"\b(?:is|was|were)\s+done\b", raw)


def absorb(user, text: str, filled: dict) -> dict:
    """Pull every fact this one sentence already answered. Do not guess the rest."""
    data = dict(filled)
    guess = trade_guess(text)
    if not data.get("trade") and guess.get("slug") and not _guide_title(text):
        data["trade"] = guess["slug"]
        data["trade_label"] = job_label(guess["slug"])
    if not data.get("unit_id"):
        numbers = unit_numbers(text)
        if len(numbers) == 1:
            data["unit_token"] = numbers[0]
            exact = units_named(user, numbers[0])
            if len(exact) == 1:
                data["unit"] = exact[0]["key"]
                data.update(_unit_extra(exact[0]))
    if not data.get("vendor"):
        name = _vendor_phrase(text)
        if name:
            data["vendor"] = name
    if not data.get("check_in"):
        heard = _clause_clock(text, "start(?:ed)?")
        if heard is None and "this morning" in norm(text):
            heard = clock_stamp("this morning")
        if heard:
            iso, label = heard
            data["start"] = iso
            data["check_in"] = iso
            data["check_in_label"] = label
    if not data.get("check_out"):
        heard = _clause_clock(text, "finish(?:ed)?")
        if heard:
            iso, label = heard
            data["finish"] = iso
            data["check_out"] = iso
            data["check_out_label"] = label
    if "on_time" not in data:
        raw = norm(text)
        if re.search(r"\bnot on time\b|\blate\b", raw):
            data["on_time"] = "no"
        elif re.search(r"\bon time\b", raw):
            data["on_time"] = "yes"
    return data


def matches(text: str) -> bool:
    return finish_sentence(text)


def allow(user) -> str:
    from app.services.access import has_capability

    if has_capability(user, "write_maintenance"):
        return ""
    return "You can't change make-ready work from this login."


def review(user, filled: dict) -> str:
    place = filled.get("property_name") or "that property"
    number = filled.get("unit_number") or "that unit"
    trade = _pretty(filled.get("trade") or filled.get("trade_label") or "")
    vendor = filled.get("vendor") or "the vendor"
    started = filled.get("check_in_label") or "the start time"
    finished = filled.get("check_out_label") or "the finish time"
    bits = [
        f"{number} at {place}.",
        f"{trade}.",
        f"Vendor {vendor}.",
        f"Started {started}.",
        f"Finished {finished}.",
    ]
    if filled.get("on_time") == "yes":
        bits.append("On time.")
    elif filled.get("on_time") == "no":
        bits.append("Late.")
    return " ".join(bits)


def apply_payload(user, filled: dict) -> dict:
    note = ""
    if filled.get("on_time") == "yes":
        note = "On time"
    elif filled.get("on_time") == "no":
        note = "Late"
    return {
        "property_id": filled.get("property_id"),
        "property_name": filled.get("property_name") or "",
        "city": filled.get("city") or "",
        "unit_id": filled.get("unit_id"),
        "unit_number": filled.get("unit_number") or "",
        "trade": filled.get("trade") or "",
        "job": filled.get("trade_label") or filled.get("trade") or "",
        "vendor": filled.get("vendor") or "",
        "contractor": filled.get("vendor") or "",
        "check_in": filled.get("check_in") or "",
        "check_out": filled.get("check_out") or "",
        "note": note,
        "done": True,
    }


def changes(user, filled: dict) -> list[dict]:
    task = task_for(int(filled.get("unit_id") or 0), filled.get("trade_label") or "")
    before_vendor = (task.vendor or "").strip() if task else ""
    before_status = (task.status or "").replace("_", " ") if task else "—"
    rows = [
        {"field": "Unit", "before": "—", "after": f"{filled.get('unit_number') or ''} at {filled.get('property_name') or ''}".strip(), "step": "unit"},
        {"field": "Trade", "before": before_status or "—", "after": "Done", "step": "trade"},
        {"field": "Vendor", "before": before_vendor or "—", "after": filled.get("vendor") or "—", "step": "vendor"},
        {"field": "Started", "before": "—", "after": filled.get("check_in_label") or "—", "step": "start"},
        {"field": "Finished", "before": "—", "after": filled.get("check_out_label") or "—", "step": "finish"},
    ]
    if filled.get("on_time") in {"yes", "no"}:
        rows.append({"field": "On time", "before": "—", "after": "Yes" if filled["on_time"] == "yes" else "No", "step": "on_time"})
    return rows


FINISH = Job(
    id="finish_vendor_trade",
    tool="finish_vendor_trade",
    matches=matches,
    absorb=absorb,
    allow=allow,
    review=review,
    apply_payload=apply_payload,
    changes=changes,
    steps=(
        Step("unit", True, ask_unit, take_unit, ("unit_id", "unit_number", "property_id", "property_name", "city", "unit_token"), ("trade", "vendor")),
        Step("trade", True, ask_trade, take_trade, ("trade_label",), ("vendor",)),
        Step("vendor", True, ask_vendor, take_vendor, ()),
        Step("start", True, ask_start, take_start, ("check_in", "check_in_label")),
        Step("finish", True, ask_finish, take_finish, ("check_out", "check_out_label")),
        Step("on_time", False, ask_on_time, take_on_time, ()),
    ),
)
