"""Logins, roles, and who can see a property."""
from __future__ import annotations

import re

from app.builddb.builddb import db
from app.models import ApiCredential

from app.services.talk.phrases import ADD_USER, AS_ROLE, EMAIL, GIVE_BOSS, PASSWORD, _ORDINALS, _STAFF_NAME_FIRST, _STAFF_ROLE_FIRST
from app.services.talk.textutil import _role_word

# A spoken number: 432-555-0100, (432) 555 0100, +1 432.555.0100.
_SPOKEN_PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?(\d{3})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})(?!\d)")
_ROLE_WORDS = re.compile(
    r"\b(?:owner|admin|administrator|regional|manager|managers|property|assistant|office|maintenance|supervisor|person|employee|worker|boss|viewer|field|read-only|readonly|bot)\b",
    re.I,
)
_NAME_NOISE = re.compile(
    r"\b(?:add|invite|create|make|login|logins|user|person|new|as|the|a|an|named|name|called|and|is|for|her|his|their|him|them|number|phone|cell|email|mail|full|also|who|can|edit|see|notify|units?|at|on)\b",
    re.I,
)


def _phone_in(text: str) -> str:
    match = _SPOKEN_PHONE.search(text or "")
    if not match:
        return ""
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def _name_in(text: str, username: str = "") -> str:
    """The human name in her sentence, with the role, number, and email taken out."""
    blob = EMAIL.sub(" ", text or "")
    blob = _SPOKEN_PHONE.sub(" ", blob)
    blob = _NAME_NOISE.sub(" ", blob)
    blob = _ROLE_WORDS.sub(" ", blob)
    words = [
        word.strip(" .'-)")
        for word in re.split(r"[^A-Za-z.'-]+", blob)
        if len(word.strip(" .'-)")) > 1
    ]
    wanted = (username or "").strip().lower()
    words = [word for word in words if word.lower() != wanted]
    if not words or len(words) > 4:
        return ""
    return " ".join(word.capitalize() for word in words)


def _missing_person_details(payload: dict) -> list[str]:
    """A new login needs a real full name, a number, and an email before it is saved."""
    name = (payload.get("display_name") or "").strip()
    username = (payload.get("username") or "").strip()
    missing = []
    if not name or name.lower() == username.lower():
        missing.append("full name")
    if not (payload.get("phone") or "").strip():
        missing.append("phone")
    if not (payload.get("email") or "").strip():
        missing.append("email")
    return missing


def _hold_for_details(user, payload: dict, role: str, key: str, source: str):
    """Ask for the missing details instead of saving a half-filled login."""
    from app.services.pending import propose
    from app.services.providers import ROLE_LABELS

    missing = _missing_person_details(payload)
    if not missing:
        return None
    held = {
        **payload,
        "needs_answer": True,
        "waiting_for": "person_details",
        "asked": int(payload.get("asked") or 0) + 1,
    }
    who = payload.get("display_name") or payload.get("username") or "them"
    summary = (
        f"Add {who} as {ROLE_LABELS.get(role, role)}. I still need their {', '.join(missing)} "
        "before this is saved. Say 'no email' if they have none. Nothing changes until then."
    )
    return propose(user, "invite_viewer", held, summary, "low", key, key, source)


def _answer_person_details(user, text: str, key: str, source: str):
    """She is answering the full name, number, and email question for a new login."""
    from app.models import PendingAction
    from app.services.records import dumps, loads

    row = (
        PendingAction.query.filter_by(user_id=user.id, tool="invite_viewer", status="needs_answer")
        .order_by(PendingAction.id.desc())
        .first()
    )
    if not row:
        return None
    payload = loads(row.payload_json)
    if payload.get("waiting_for") != "person_details":
        return None
    raw = (text or "").strip()
    if not raw:
        return {"ok": True, "pending": True, "reply": row.summary}
    low = raw.lower()
    # The question may have scrolled away, so accept either the details
    # themselves or an explicit "skip for now"; anything else is a new
    # command and the question will be re-asked below.
    skipping = bool(re.search(r"\b(?:skip|later|not now|never ?mind|forget it)\b", low))
    email = EMAIL.search(raw)
    phone = _phone_in(raw)
    # Her answer is the details themselves, so the real name may repeat the username.
    name = _name_in(raw, "")
    said_none = {
        "email": bool(re.search(r"\bno\s*(?:e-?mail)\b|\bwithout (?:an? )?e-?mail\b|\bno\s+address\b", low)),
        "phone": bool(re.search(r"\bno\s*(?:phone|number|cell|mobile)\b|\bwithout (?:a )?(?:phone|number)\b", low)),
    }
    if not (email or phone or name or skipping or any(said_none.values())):
        return None
    if email:
        payload["email"] = email.group(0)
        payload["no_email"] = False
    if phone:
        payload["phone"] = phone
        payload["no_phone"] = False
    if name:
        payload["display_name"] = name
    if said_none["email"]:
        payload["no_email"] = True
        payload["email"] = ""
    if said_none["phone"]:
        payload["no_phone"] = True
        payload["phone"] = ""
    if skipping:
        payload["no_email"] = True
        payload["no_phone"] = True
    missing = []
    shown = (payload.get("display_name") or "").strip()
    if not shown or shown.lower() == (payload.get("username") or "").strip().lower():
        missing.append("full name")
    if not (payload.get("phone") or "").strip() and not payload.get("no_phone"):
        missing.append("phone")
    if not (payload.get("email") or "").strip() and not payload.get("no_email"):
        missing.append("email")
    if missing:
        payload["asked"] = int(payload.get("asked") or 1) + 1
        row.payload_json = dumps(payload)
        row.summary = f"Still need their {', '.join(missing)} for {payload.get('username')}. 'no email' or 'no phone' is fine if they have none."
        row.status = "needs_answer"
        db.session.commit()
        return {"ok": True, "pending": True, "reply": row.summary}
    from app.services.talk.interpret import _commit_waiting

    # Her answer filled the card; the card still takes its own Save.
    return _commit_waiting(user, row, "invite_viewer", payload, key, source)

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
    deferred_grants = False

    office = requested_role == "office"
    title = "office manager" if office else "employee" if role == "field" else role.replace("_", " ")
    clauses = _staff_clauses(raw[tail_at:])
    from app.services.pending import request_apply

    person = find_user(username)
    named = person.display_name or person.username if person else username.replace(".", " ").replace("_", " ").title()
    lines = []
    if person:
        changed = request_apply(
            actor,
            "update_viewer",
            {"username": person.username, "role": role, "_say": f"Change {named} to {title}."},
            source,
            f"{key}:role",
            batch_key=key,
        )
        lines.append(changed.get("reply") or "")
    else:
        mail = EMAIL.search(raw)
        spoken_name = _name_in(raw, username)
        invite_payload = {
            "username": username,
            "display_name": spoken_name or named,
            "role": role,
            "email": mail.group(0) if mail else "",
            "phone": _phone_in(raw),
            "password": (PASSWORD.search(raw).group(1) if PASSWORD.search(raw) else ""),
            "can_see_reports": bool(re.search(r"\breports?\b", raw, re.I)),
            "can_see_history": True,
            "can_see_live_map": bool(re.search(r"\b(live map|the map)\b", raw, re.I)),
            "_say": f"Add {spoken_name or named} as {title}.",
        }
        if clauses:
            # The door clauses ride on the invite so they land when the person is
            # actually created, whether that is now or after the details arrive.
            invite_payload["_grants"] = [
                {
                    "property_name": hint,
                    "see": True,
                    "edit": True if mode == "edit" else False if mode == "see" else None,
                    "notify": True if mode == "notify" else None,
                }
                for mode, hints in clauses
                for hint in hints
            ]
            deferred_grants = True
        held = _hold_for_details(actor, invite_payload, role, f"{key}:invite", source)
        if held:
            lines.append(held.get("reply") or "")
            return {"ok": True, "reply": " ".join(bit for bit in lines if bit)}
        invited = request_apply(
            actor,
            "invite_viewer",
            invite_payload,
            source,
            f"{key}:invite",
            batch_key=key,
        )
        lines.append(invited.get("reply") or "")
    count = 0
    # Clauses that already ride on the invite must not be granted twice.
    for mode, hints in ([] if deferred_grants else clauses):
        edit = True if mode == "edit" else False if mode == "see" else None
        notify = True if mode == "notify" else None
        for hint in hints:
            count += 1
            granted = request_apply(
                actor,
                "grant_access",
                {"username": person.username if person else username, "property_name": hint, "see": True, "edit": edit, "notify": notify},
                source,
                f"{key}:grant:{count}",
                batch_key=key,
            )
            lines.append(granted.get("reply") or "")
    if not clauses and not deferred_grants:
        lines.append("Say which properties, like edit units on Woodview.")
    return {"ok": True, "reply": " ".join(bit for bit in lines if bit)}


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
    mode = (mode or "see").lower()
    from app.services.access import can_manage_property_people
    from app.services.parse import resolve_property

    verdict = resolve_property(hint, user=user)
    if verdict.get("state") == "ambiguous":
        return {"ok": True, "reply": "Which one?\n" + "\n".join(verdict.get("choices") or [])}
    if verdict.get("state") != "resolved":
        return {"ok": False, "reply": verdict.get("message") or f"Nothing in your scope matches {hint}."}
    prop = verdict["property"]
    if not can_manage_property_people(user, prop.id):
        return {"ok": False, "reply": "You do not manage people at that property."}
    from app.services.pending import request_apply

    return request_apply(
        user,
        "grant_access",
        {
            "username": username,
            "property_name": prop.name,
            "city": prop.city.name if prop.city else "",
            "see": True,
            "edit": True if mode == "edit" else None,
            "notify": True if mode == "notify" else None,
        },
        source,
        key,
    )


def _role_change(user, text: str, key: str, source: str):
    """'Change her permissions to regional manager' — a role change waits on a card."""
    raw = (text or "").strip().rstrip(".")
    from app.services.changes import normalize_role

    hit = re.search(r"\bfor\s+([A-Za-z][A-Za-z.'-]+(?:\s+[A-Za-z][A-Za-z.'-]+){0,3})\b.*?\b(?:to|as)\s+([a-z][a-z ]+)$", raw, re.I)
    if not hit:
        hit = re.search(
            r"\b(?:change|make|set|promote|demote)\b\s+(?:the\s+)?(.+?)\s+(?:to|as|into|the role of|a|an)\s+([a-z][a-z ]+)$",
            raw,
            re.I,
        )
    if not hit:
        return None
    role = normalize_role(hit.group(2))
    if not role:
        return None
    who = re.sub(r"\b(for|change|changes|make|makes|set|promote|demote|her|his|their|permissions?|access|role|login|the|to|as)\b", " ", hit.group(1), flags=re.I)
    who = re.sub(r"\s+", " ", who).strip(" ,.'")
    if not who or len(who.split()) > 4:
        return None
    from app.services.pending import request_apply

    payload = {"username": who, "role": role, "_say": ""}
    from app.services.changes import headline as change_headline

    payload["_say"] = change_headline("update_viewer", payload, [])
    return request_apply(user, "update_viewer", payload, source, key)


def _person_to_add(user, text: str, key: str, source: str):
    staff = _staff_from_sentence(user, text, key, source)
    if staff:
        return staff
    changed = _role_change(user, text, key, source)
    if changed:
        return changed
    named = AS_ROLE.search(text or "")
    role_first = ADD_USER.search(text or "")
    if named:
        return _offer_login(user, text, named.group(2), named.group(1), key, source)
    if role_first or GIVE_BOSS.search(text or ""):
        return _user_offer(user, text, role_first, key, source)
    return None


def _offer_login(user, text, role_word: str, username: str, key: str, source: str) -> dict:
    role = _role_word(role_word)
    from app.services.access import can_create_user
    from app.services.people import find_user

    if find_user(username):
        return {"ok": False, "reply": f"{username} already has a login."}
    if not can_create_user(user, role):
        return {"ok": False, "reply": "This login cannot create that role."}
    email = EMAIL.search(text)
    password = PASSWORD.search(text)
    payload = {
        "username": username,
        "display_name": _name_in(text, username) or username.replace(".", " ").replace("_", " ").title(),
        "role": role,
        "email": email.group(0) if email else "",
        "phone": _phone_in(text),
        "password": password.group(1) if password else "",
        "can_see_reports": True,
        "can_see_history": True,
        "can_see_live_map": False,
    }
    held = _hold_for_details(user, payload, role, key, source)
    if held:
        return held
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
