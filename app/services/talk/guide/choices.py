"""Turn a short list into A B C, and read a letter, a number, or the words."""
from __future__ import annotations

import re

from app.services.talk.guide.hear import is_yes, norm

LETTERS = "ABCDEF"
TYPE_KEY = "__type__"


def shown(choices: list[dict], *, limit: int = 6) -> list[dict]:
    """At most six. The type-it choice stays if it was last."""
    rows = [row for row in choices if row.get("label")]
    if len(rows) <= limit:
        return rows
    typed = [row for row in rows if row.get("key") == TYPE_KEY]
    head = [row for row in rows if row.get("key") != TYPE_KEY][: limit - len(typed[:1])]
    return head + typed[:1]


def render(question: str, choices: list[dict], *, optional: bool = False, preface: str = "", tail: str = "") -> str:
    """One plain question plus the letters. The person can also type."""
    lines = []
    if preface:
        lines.append(preface.strip())
        lines.append("")
    lines.append(question.strip())
    rows = shown(choices)
    if rows:
        lines.append("")
        for index, row in enumerate(rows):
            lines.append(f"{LETTERS[index]}. {row['label']}")
        letters = ", ".join(LETTERS[i] for i in range(len(rows) - 1))
        last = LETTERS[len(rows) - 1]
        how = f"Reply {letters}, or {last}." if letters else f"Reply {last}."
        lines.append("")
        lines.append(f"{how} Or type it your own way.")
    if optional:
        lines.append("You can skip this.")
    if tail:
        lines.append(tail)
    lines.append("Nothing is saved yet.")
    return "\n".join(lines).strip()


def _letter_index(text: str, count: int) -> int | None:
    raw = norm(text)
    match = re.fullmatch(r"(?:option\s*)?([a-f])(?:[.)])?", raw)
    if match:
        index = ord(match.group(1)) - 97
        if 0 <= index < count:
            return index
    if re.fullmatch(r"[1-6]", raw):
        index = int(raw) - 1
        if 0 <= index < count:
            return index
    return None


def _phrase(hay: str, needle: str) -> bool:
    """True when needle is a whole word or phrase inside hay. 43 is not inside 430."""
    if not hay or not needle:
        return False
    return bool(re.search(rf"(^| ){re.escape(needle)}( |$)", hay))


def pick(text: str, choices: list[dict]) -> dict | None:
    """The one choice this reply names. None when it is not a choice."""
    rows = shown(choices)
    if not rows:
        return None
    index = _letter_index(text, len(rows))
    if index is not None:
        return rows[index]
    raw = norm(text)
    if not raw:
        return None
    if is_yes(raw):
        yeses = [row for row in rows if norm(row.get("label") or "").startswith("yes")]
        if len(yeses) == 1:
            return yeses[0]
    hits = []
    for row in rows:
        label = norm(row.get("label") or "")
        key = norm(str(row.get("key") or ""))
        if not label:
            continue
        if raw == label or (key and raw == key) or (len(raw) > 1 and (_phrase(label, raw) or _phrase(raw, label))):
            hits.append(row)
    if len(hits) == 1:
        return hits[0]
    return None


def typed(choice: dict | None) -> bool:
    return bool(choice and choice.get("key") == TYPE_KEY)
