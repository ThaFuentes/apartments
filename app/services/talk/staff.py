"""Logins, roles, and who can see a property."""
from __future__ import annotations

import re

from app.builddb.builddb import db
from app.models import ApiCredential

from app.services.talk.phrases import ADD_USER, AS_ROLE, EMAIL, GIVE_BOSS, PASSWORD, _ORDINALS, _STAFF_NAME_FIRST, _STAFF_ROLE_FIRST
from app.services.talk.textutil import _role_word

def _staff_clauses(rest: str) -> list[tuple[str, list[str]]]:
    rest = re.sub(r"^(?:who|that|she|he|they)\s+", "", (rest or "").strip(), flags=re.I)
    rest = re.sub(r"^can\s+(?:do\s+)?(?:her\s+|his\s+|their\s+)?", "", rest, flags=re.I)
    parts = re.split(
        r"\s*,\s*|\s+and\s+(?=(?:also\s+)?(?:be\s+notified|see|edit|view|notify)\b)",
        rest,
        flags=re.I,
    )
    clauses = []
    for part in parts:
        part = part.strip(" .")
        if not part:
            continue
        if re.search(r"\b(edit|change)\b", part, re.I):
            mode = "edit"
        elif re.search(r"\bnotif", part, re.I):
            mode = "notify"
        elif re.search(r"\b(see|view|read)\b", part, re.I):
            mode = "see"
        else:
            mode = ""
        place_text = re.sub(
            r"^(?:also\s+)?(?:be\s+notified|see|edit|view|notify|change)(?:\s+units?)?(?:\s+(?:on|at|for))?\s*",
            "",
            part,
            count=1,
            flags=re.I,
        )
        place_text = re.sub(r"\b(permissions?|access|units?)\b", " ", place_text, flags=re.I)
        hints = []
        for bit in re.split(r"\s*,\s*|\s+and\s+", place_text):
            hint = bit.strip(" .")
            if len(re.sub(r"[^a-z0-9]", "", hint)) >= 4:
                hints.append(hint)
        if hints:
            clauses.append((mode, hints))
    return clauses


def _staff_from_sentence(actor, text: str, key: str, source: str):
    raw = (text or "").strip().rstrip(".")
    named = _STAFF_NAME_FIRST.search(raw)
    role_first = _STAFF_ROLE_FIRST.search(raw)
    if named:
        username, role_word, tail_at = named.group(1), named.group(2), named.end()
    elif role_first:
        role_word, username, tail_at = role_first.group(1), role_first.group(2), role_first.end()
    else:
        return None
    from app.services.access import can_create_user, can_manage_user
    from app.services.people import find_user

    requested_role = _role_word(role_word)
    existing = find_user(username)
    if existing and not can_manage_user(actor, existing):
        return {"ok": False, "reply": "This login cannot manage that person."}
    if not existing and not can_create_user(actor, requested_role):
        return {"ok": False, "reply": "This login cannot create that role."}
    role = requested_role

    office = role_word.lower() == "office manager"
    title = "office manager" if office else "employee" if role == "field" else role.replace("_", " ")
    clauses = _staff_clauses(raw[tail_at:])
    from app.services.people import create_user
    from app.services.access import grant_from_words

    person = find_user(username)
    generated = ""
    if person:
        opened = f"{person.display_name or person.username} already has a login."
    else:
        try:
            person, generated = create_user(
                username=username,
                password=(PASSWORD.search(raw).group(1) if PASSWORD.search(raw) else ""),
                display_name=username.replace(".", " ").replace("_", " ").title(),
                role=role,
                email=(EMAIL.search(raw).group(0) if EMAIL.search(raw) else None),
                created_by=actor,
                can_see_reports=bool(re.search(r"\breports?\b", raw, re.I)),
                can_see_history=True,
                can_see_live_map=bool(re.search(r"\b(live map|the map)\b", raw, re.I)),
            )
        except ValueError as exc:
            return {"ok": False, "reply": str(exc)}
        opened = f"{person.display_name} is {title}."
    lines = [opened]
    for mode, hints in clauses:
        edit = True if mode == "edit" else False if mode == "see" else None
        notify = True if mode == "notify" else None
        for hint in hints:
            result = grant_from_words(actor, person.username, hint, see=True, edit=edit, notify=notify)
            if result.get("reply"):
                lines.append(result["reply"])
    if generated:
        lines.append(f"Sign-in is {person.username}. Temporary password: {generated}.")
    elif not clauses:
        lines.append("Say which properties, like edit units on Woodview.")
    db.session.commit()
    return {"ok": True, "reply": " ".join(lines)}


def _file_access(user, text: str, key: str, source: str):
    raw = (text or "").strip().rstrip(".")
    if re.search(r"\b(units?|make ready|occupied|washer|dryer)\b", raw, re.I) and not re.search(r"\b(pin|give|let|allow|notify)\b", raw, re.I):
        return None
    pin = re.search(r"^(?:please\s+)?(un)?pin\s+(.+)$", raw, re.I)
    if pin:
        from app.services.access import pin_property, role_of
        from app.services.records import fuzzy_properties

        matches = fuzzy_properties(pin.group(2))
        from app.services.access import can_see_property

        if role_of(user) not in ("owner", "admin"):

            matches = [prop for prop in matches if can_see_property(user, prop.id)]
        if len(matches) != 1:
            return {"ok": True, "reply": "Which property should I pin?"} if not matches else {"ok": True, "reply": "Which one?\n" + "\n".join(prop.name for prop in matches[:8])}
        reply = pin_property(user, matches[0], pin.group(1) is None)
        db.session.commit()
        return {"ok": True, "reply": reply}
    grant = re.search(
        r"\b(?:give|let|allow)\s+([a-z0-9][a-z0-9._-]{1,40})\s+(?:(see|edit|notify)\s+)?(?:units\s+at\s+|about\s+)?(.+)$",
        raw,
        re.I,
    )
    if not grant:
        grant = re.search(r"\b([a-z0-9][a-z0-9._-]{1,40})\s+can\s+(see|edit)\s+(.+)$", raw, re.I)
        if grant:
            username, mode, hint = grant.group(1), grant.group(2), grant.group(3)
        else:
            heard = re.search(r"\bnotify\s+([a-z0-9][a-z0-9._-]{1,40})\s+(?:about|when|on)\s+(.+)$", raw, re.I)
            if not heard:
                return None
            username, mode, hint = heard.group(1), "notify", heard.group(2)
    else:
        username, mode, hint = grant.group(1), (grant.group(2) or "see"), grant.group(3)
    from app.services.access import grant_from_words

    mode = (mode or "see").lower()
    result = grant_from_words(
        user,
        username,
        hint,
        see=True,
        edit=True if mode == "edit" else None,
        notify=True if mode == "notify" else None,
    )
    if result.get("ok"):
        db.session.commit()
    return result


def _person_to_add(user, text: str, key: str, source: str):
    staff = _staff_from_sentence(user, text, key, source)
    if staff:
        return staff
    named = AS_ROLE.search(text or "")
    role_first = ADD_USER.search(text or "")
    if named:
        return _offer_login(user, text, named.group(2), named.group(1), key, source)
    if role_first or GIVE_BOSS.search(text or ""):
        return _user_offer(user, text, role_first, key, source)
    return None


def _offer_login(user, text, role_word: str, username: str, key: str, source: str) -> dict:
    role = _role_word(role_word)
    email = EMAIL.search(text)
    password = PASSWORD.search(text)
    payload = {
        "username": username,
        "display_name": username.replace(".", " ").replace("_", " ").title(),
        "role": role,
        "email": email.group(0) if email else "",
        "password": password.group(1) if password else "",
        "can_see_reports": True,
        "can_see_history": True,
        "can_see_live_map": False,
    }
    from app.services.pending import commit_apply

    return commit_apply(user, "invite_viewer", payload, source, key)


def _user_offer(user, text, match, key, source) -> dict:
    if match:
        role_word = match.group(1).lower()
        username = match.group(2)
    else:
        role_word = "viewer"
        username = "boss"
    return _offer_login(user, text, role_word, username, key, source)


def _set_key_rank(user, label: str, order: int) -> dict:
    from app.services.providers import PROVIDERS

    if getattr(user, "role", "") != "owner":
        return {"ok": False, "reply": "Only the owner sets which key is 1st, 2nd, or 3rd."}
    rows = ApiCredential.query.filter_by(user_id=user.id).all()
    want = label.lower()
    match = None
    for row in rows:
        spec = PROVIDERS.get(row.provider) or {}
        names = {row.provider.lower(), (spec.get("label") or "").lower(), (row.last4 or "").lower()}
        if want in names or any(want in name for name in names if name):
            match = row
            break
    if not match:
        return {"ok": False, "reply": f"I don't have a key named {label}."}
    previous = match.use_order or 0
    for row in rows:
        if row.id != match.id and (row.use_order or 0) == order:
            row.use_order = previous or order + 1
    match.use_order = order
    match.active = True
    for row in rows:
        row.preferred = row.id == match.id and order == 1
    if order != 1:
        first = sorted(rows, key=lambda row: (row.use_order or 99, row.id))
        for row in first:
            row.preferred = False
        for row in first:
            if row.active and (row.use_order or 99) == min((item.use_order or 99) for item in first if item.active):
                row.preferred = True
                break
    db.session.commit()
    words = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}
    ordered = sorted((row for row in rows if row.active), key=lambda row: (row.use_order or 99, row.id))
    lineup = ", ".join(
        f"{(PROVIDERS.get(row.provider) or {}).get('label') or row.provider} is {words.get(row.use_order, 'later')}"
        for row in ordered
        if row.use_order
    )
    return {"ok": True, "reply": f"{(PROVIDERS.get(match.provider) or {}).get('label') or match.provider} is {words.get(order, str(order))}. {lineup}."}


def _key_rank_sentence(text: str):
    raw = (text or "").strip()
    if not re.search(r"\b(key|gemini|groq|openai|grok|claude|anthropic)\b", raw, re.I):
        return None
    named = re.search(
        r"\b(?:make|set|use)\s+(?:the\s+)?([a-z0-9][a-z0-9 ._-]{1,40}?)\s+(?:key\s+)?(?:as\s+|is\s+)?(1st|2nd|3rd|4th|first|second|third|fourth)\b",
        raw,
        re.I,
    )
    if named:
        return named.group(1).strip(), _ORDINALS[named.group(2).lower()]
    flipped = re.search(
        r"\b(1st|2nd|3rd|4th|first|second|third|fourth)\s+(?:key\s+)?(?:is|should be)\s+([a-z0-9][a-z0-9 ._-]{1,40})",
        raw,
        re.I,
    )
    if flipped:
        return flipped.group(2).strip(" ."), _ORDINALS[flipped.group(1).lower()]
    return None


def _settings_payload(text: str) -> dict:
    payload = {}
    named = re.search(r"(?:call yourself|assistant name)\s+(.+)$", text, re.I)
    if named:
        payload["assistant_name"] = named.group(1).strip(" .")
    company = re.search(r"company name\s+(.+)$", text, re.I)
    if company:
        payload["company_name"] = company.group(1).strip(" .")
    city = re.search(r"default city\s+(.+)$", text, re.I)
    if city:
        payload["default_city"] = city.group(1).strip(" .")
    home = re.search(r"home base\s+(.+)$", text, re.I)
    if home:
        payload["home_label"] = home.group(1).strip(" .")
    tone = re.search(r"my tone\s+(.+)$", text, re.I)
    if tone:
        payload["tone"] = tone.group(1).strip(" .")
    return payload
