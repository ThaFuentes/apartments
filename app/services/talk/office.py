"""Office sentences the pages already do.

The parser at the top does not touch the database. office_sentence stages a
confirm card, or answers a question, using the same services as the pages.
Exact sentences run before any model call. A pasted key or password is refused
here and is not stored.
"""
from __future__ import annotations

import re

_NAME = r"[a-z0-9][a-z0-9 .'_-]{0,60}"
_UNIT = r"[a-z0-9][a-z0-9-]{0,12}"
_IP = r"\d{1,3}(?:\.\d{1,3}){3}|[0-9a-f:]{2,45}"
_FP = r"[0-9a-f]{40}"
_ROLE = (
    r"regional property manager|regional maintenance manager|regional manager|"
    r"maintenance supervisor|maintenance manager|maintenance person|"
    r"assistant manager|property manager|office staff|office"
)
_EMAIL = r"[^@\s]+@[^@\s]+\.[^@\s]+"
_SECRET = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(?:github_pat_|ghp_|gho_|glpat-|sk-[A-Za-z0-9]|AIza[0-9A-Za-z_\-]{20,}|xai-[A-Za-z0-9]|AKIA[0-9A-Z]{16}|ya29\.)"
    r"|(?:api[ _-]?key|password|passwd|secret|bearer)\s*(?:is|=|:)\s*\S{6,}"
    r"|\btoken\s*(?:is|=|:)\s*\S{12,}"
    r"|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}",
    re.I,
)
_KEY_ASK = re.compile(
    r"\b(?:api[ _-]?keys?|gemini key|openai key|save (?:my |this |the )?key|add (?:my |an? )?api key)\b",
    re.I,
)
_BAN_ASK = (
    "A ban needs the whole sentence, so a stray word cannot ban anyone. "
    "Ban ip 203.0.113.8 for 24 hours because repeated login failures. "
    "Or ban device, the 40-character print, for 24 hours because the kiosk was shared."
)
_RESET_ASK = "Send a password reset to Jane. The link goes to their reset inbox and is not shown here."
_FACTOR_ASK = "Two-factor enrollment stays on its setup page. Chat cannot turn it on or off."


_PASSWORD_CHANGE = re.compile(
    r"\b(?:change|set|update)\s+my\s+password\b(?!\s*[- ]reset)"
    r"|\bnew password\b"
    r"|\bpassword\s+to\s+\S{6,}",
    re.I,
)
_FACTOR = re.compile(r"\b(?:two[- ]factor|2fa|authenticator)\b", re.I)
_THEME_ALIASES = {
    "paper": ("paper", "Warm paper"),
    "field night": ("night", "Field night"),
    "night": ("night", "Field night"),
    "office slate": ("slate", "Office slate"),
    "slate": ("slate", "Office slate"),
    "high contrast": ("contrast", "High contrast"),
    "contrast": ("contrast", "High contrast"),
    "desert": ("desert", "Desert"),
    "harbor": ("harbor", "Harbor"),
    "ink": ("ink", "Ink"),
    "grove": ("grove", "Grove"),
    "dusk": ("dusk", "Dusk"),
    "chalk": ("chalk", "Chalk"),
}
_THEMES = (
    r"field night|office slate|high contrast|paper|night|slate|contrast|"
    r"desert|harbor|ink|grove|dusk|chalk"
)
_LAYOUT_ALIASES = {
    "top bar": ("bar", "Top bar"),
    "side rail": ("rail", "Side rail"),
    "left rail": ("rail", "Side rail"),
    "split desk": ("split", "Split desk"),
    "three columns": ("split", "Split desk"),
    "right dock": ("dock", "Right dock"),
    "card board": ("board", "Card board"),
    "dense ledger": ("ledger", "Ledger"),
    "focus column": ("focus", "Focus column"),
    "reading column": ("focus", "Focus column"),
    "full canvas": ("canvas", "Full canvas"),
    "edge canvas": ("canvas", "Full canvas"),
    "rail": ("rail", "Side rail"),
    "split": ("split", "Split desk"),
    "dock": ("dock", "Right dock"),
    "board": ("board", "Card board"),
    "ledger": ("ledger", "Ledger"),
    "focus": ("focus", "Focus column"),
    "canvas": ("canvas", "Full canvas"),
    "bar": ("bar", "Top bar"),
}
_LAYOUTS = (
    r"side rail|left rail|split desk|three columns|right dock|card board|"
    r"dense ledger|focus column|reading column|full canvas|edge canvas|top bar|"
    r"rail|split|dock|board|ledger|focus|canvas|bar"
)


def secret_leak(text: str) -> str:
    """Refuse a key, password, or two-factor sentence before it is stored or sent to a model."""
    raw = text or ""
    if _FACTOR.search(raw):
        return _FACTOR_ASK
    if _SECRET.search(raw) or _KEY_ASK.search(raw) or _PASSWORD_CHANGE.search(raw):
        return "That stays off chat. API keys and passwords are entered on Settings, and this message was not saved."
    return ""


def office_intent(text: str) -> dict | None:
    """Turn one office sentence into a kind and slots. None when it is not one."""
    raw = " ".join((text or "").strip().rstrip(".").split())
    raw = raw.replace("\u2019", "'").replace("\u2018", "'")
    if not raw:
        return None

    if re.search(r"\b(?:list|show)\s+regions\b", raw, re.I) or re.search(r"\bwhat regions\b", raw, re.I):
        return {"kind": "list_regions"}
    if re.fullmatch(r"(?:create|add|make)(?:\s+a)?\s+region", raw, re.I):
        return {"kind": "ask", "reply": "What should the region be called?"}

    made = re.fullmatch(rf"(?:create|add|make)\s+(?:a\s+)?region\s+(?:called\s+|named\s+)?({_NAME})", raw, re.I)
    if made:
        return {"kind": "manage_region", "action": "create", "name": _tidy(made.group(1))}

    renamed = re.fullmatch(rf"rename\s+region\s+({_NAME})\s+to\s+({_NAME})", raw, re.I)
    if renamed and renamed.group(1) and renamed.group(2):
        return {"kind": "manage_region", "action": "rename", "name": _tidy(renamed.group(1)), "new_name": _tidy(renamed.group(2))}

    deleted = re.fullmatch(rf"(?:delete|remove)\s+region\s+({_NAME})", raw, re.I)
    if deleted:
        return {"kind": "manage_region", "action": "delete", "name": _tidy(deleted.group(1))}

    city_add = re.fullmatch(rf"(?:add|put)\s+(?:the\s+)?(?:city\s+)?({_NAME})\s+(?:in|into|to)\s+region\s+({_NAME})", raw, re.I)
    if city_add:
        return {"kind": "manage_region", "action": "add_city", "city": _tidy(city_add.group(1)), "name": _tidy(city_add.group(2))}
    city_drop = re.fullmatch(rf"remove\s+(?:the\s+)?city\s+({_NAME})\s+from\s+region\s+({_NAME})", raw, re.I)
    if city_drop:
        return {"kind": "manage_region", "action": "remove_city", "city": _tidy(city_drop.group(1)), "name": _tidy(city_drop.group(2))}

    person_add = re.fullmatch(rf"assign\s+({_NAME})\s+to\s+region\s+({_NAME})", raw, re.I)
    if person_add:
        return {"kind": "manage_region", "action": "add_person", "person": _tidy(person_add.group(1)), "name": _tidy(person_add.group(2))}
    person_drop = re.fullmatch(rf"remove\s+({_NAME})\s+from\s+region\s+({_NAME})", raw, re.I)
    if person_drop:
        return {"kind": "manage_region", "action": "remove_person", "person": _tidy(person_drop.group(1)), "name": _tidy(person_drop.group(2))}

    allow = re.fullmatch(rf"(?:let|allow)\s+({_NAME})\s+choose\s+(?:their|her|his)\s+default property\s+in\s+region\s+({_NAME})", raw, re.I)
    if allow:
        return {"kind": "manage_region", "action": "allow_default", "person": _tidy(allow.group(1)), "name": _tidy(allow.group(2))}
    deny = re.fullmatch(rf"(?:do not|don't|dont)\s+let\s+({_NAME})\s+choose\s+(?:their|her|his)\s+default property\s+in\s+region\s+({_NAME})", raw, re.I)
    if deny:
        return {"kind": "manage_region", "action": "deny_default", "person": _tidy(deny.group(1)), "name": _tidy(deny.group(2))}

    shown = re.fullmatch(
        rf"(?:show|open|see|where(?:'s| is)|what(?:'s| is))\s+(?:the\s+)?(?:property\s+)?map(?:\s+(?:for|at|of)\s+({_NAME}))?",
        raw,
        re.I,
    )
    if shown:
        return {"kind": "show_map", "property": _tidy(shown.group(1) or "")}

    removed = re.fullmatch(rf"(?:remove|delete|clear)\s+(?:the\s+)?(?:property\s+)?map\s+(?:for|at|of|on)\s+({_NAME})", raw, re.I)
    if removed:
        return {"kind": "remove_map", "property": _tidy(removed.group(1))}

    photo = map_property_name(raw)
    if photo:
        return {"kind": "map_photo", "property": photo}

    back = re.fullmatch(
        rf"send\s+back\s+({_NAME}?)\s+on\s+(?:unit\s*)?#?({_UNIT})(?:\s+(?:at|in)\s+((?:(?!\b(?:because|since)\b)[a-z0-9 .'_-])+))?(?:\s*(?::|because|since)\s+(.+))?",
        raw,
        re.I,
    )
    if back:
        return {
            "kind": "send_back",
            "job": _tidy(back.group(1) or ""),
            "unit": back.group(2),
            "property": _tidy(back.group(3) or ""),
            "note": _tidy(back.group(4) or ""),
        }

    rent = re.fullmatch(
        rf"(?:mark|set)\s+(?:unit\s*)?#?({_UNIT})\s+(?:as\s+)?(ready to rent|rentable|not rentable)(?:\s+(?:at|in)\s+({_NAME}))?",
        raw,
        re.I,
    )
    if not rent:
        rent = re.fullmatch(
            rf"(?:unit\s*)?#?({_UNIT})\s+is\s+(ready to rent|rentable|not rentable)(?:\s+(?:at|in)\s+({_NAME}))?",
            raw,
            re.I,
        )
    if rent:
        return {"kind": "mark_rentable", "unit": rent.group(1), "on": (rent.group(2) or "").lower() != "not rentable", "property": _tidy(rent.group(3) or "")}

    moved = re.fullmatch(
        rf"(?:set\s+)?(?:the\s+)?move[- ]out(?:\s+date)?\s+(?:for\s+)?(?:unit\s*)?#?({_UNIT})(?:\s+(?:at|in)\s+({_NAME}))?\s+(?:is|on|to)\s+(.+)",
        raw,
        re.I,
    )
    if moved:
        return {"kind": "set_move_out", "unit": moved.group(1), "property": _tidy(moved.group(2) or ""), "day": _tidy(moved.group(3))}

    inventory = re.search(r"\b(?:put back|restore)\s+(?:the\s+)?inventory\b", raw, re.I)
    if inventory:
        person = r"[a-z][a-z0-9 .'_-]{0,40}?"
        who = re.search(rf"\b(?:by|that)\s+({person})(?:\s+(?:deleted|removed)\b|\s+for\b|$)", raw, re.I)
        if not who:
            who = re.search(rf"\binventory\s+(?:that\s+)?({person})\s+(?:deleted|removed)\b", raw, re.I)
        days = re.search(r"\b(\d{1,2})\s+days?\b", raw, re.I)
        return {
            "kind": "restore_inventory",
            "person": _tidy(who.group(1) if who else ""),
            "days": int(days.group(1)) if days else 30,
        }

    audit = re.fullmatch(r"reverse\s+audit\s+#?(\d+)", raw, re.I)
    if audit:
        return {"kind": "reverse_audit", "audit_id": int(audit.group(1))}

    unlock = re.fullmatch(rf"unlock\s+(?:the\s+)?login(?:\s+for)?\s+({_NAME})", raw, re.I)
    if not unlock:
        unlock = re.fullmatch(rf"unlock\s+([a-z0-9._-]{{2,40}})", raw, re.I)
    if unlock and _tidy(unlock.group(1)).lower() not in {"door", "gate", "unit"}:
        return {"kind": "unlock_login", "person": _tidy(unlock.group(1))}

    gone = re.fullmatch(rf"(?:remove|delete)\s+contractor\s+({_NAME})", raw, re.I)
    if gone:
        return {"kind": "remove_contractor", "name": _tidy(gone.group(1))}

    howto = re.fullmatch(
        rf"(?:how[- ]to|instructions)\s+for\s+(?:the\s+)?({_NAME}?)(?:\s+in\s+(?:unit\s*)?#?({_UNIT}))?(?:\s+(?:at|in)\s+({_NAME}?))?\s*(?::|is)\s+(.+)",
        raw,
        re.I,
    )
    if howto and howto.group(4):
        return {
            "kind": "save_how_to",
            "gear": _tidy(howto.group(1) or ""),
            "unit": howto.group(2) or "",
            "property": _tidy(howto.group(3) or ""),
            "text": _tidy(howto.group(4)),
        }

    title = re.fullmatch(rf"(?:please\s+)?add\s+(?:a\s+)?job title\s+({_NAME})\s+based on\s+({_ROLE})", raw, re.I)
    if title:
        return {"kind": "create_job_title", "label": _tidy(title.group(1)), "based_on": _tidy(title.group(2))}

    _place = r"(?:(?!\s+every\s)(?!\s+reminder\s)[a-z0-9 .'_-])+"
    gear = re.fullmatch(
        rf"(?:please\s+)?add\s+(?:a\s+|the\s+)?(?:property gear\s+|place gear\s+)?(.+?)(?:\s+named\s+({_NAME}))?\s+at\s+({_place})(?:\s+every\s+(\d{{1,4}})\s+days?)?(?:\s+reminder\s+(.+?))?(?:\s*:\s+(.+))?",
        raw,
        re.I,
    )
    if gear and not re.search(r"\b(?:region|unit)\b", gear.group(1) or "", re.I):
        return {
            "kind": "add_place_gear",
            "gear": _tidy(gear.group(1)),
            "brand": _tidy(gear.group(2) or ""),
            "property": _tidy(gear.group(3)),
            "days": int(gear.group(4)) if gear.group(4) else 0,
            "task": _tidy(gear.group(5) or ""),
            "text": _tidy(gear.group(6) or ""),
        }

    hat_at = re.fullmatch(rf"(?:please\s+)?give\s+({_NAME})\s+(?:a|an)\s+({_ROLE})\s+hat\s+at\s+({_NAME})", raw, re.I)
    if hat_at:
        return {"kind": "set_hat", "person": _tidy(hat_at.group(1)), "role": _tidy(hat_at.group(2)), "property": _tidy(hat_at.group(3)), "region": ""}
    hat_region = re.fullmatch(rf"(?:please\s+)?give\s+({_NAME})\s+(?:a|an)\s+({_ROLE})\s+hat\s+in\s+region\s+({_NAME})", raw, re.I)
    if hat_region:
        return {"kind": "set_hat", "person": _tidy(hat_region.group(1)), "role": _tidy(hat_region.group(2)), "property": "", "region": _tidy(hat_region.group(3))}
    hat_off = re.fullmatch(
        rf"(?:please\s+)?(?:remove|take)\s+(?:the\s+)?({_ROLE})\s+hat\s+(?:off|from)\s+({_NAME})\s+at\s+({_NAME})",
        raw,
        re.I,
    )
    if hat_off:
        return {"kind": "clear_hat", "role": _tidy(hat_off.group(1)), "person": _tidy(hat_off.group(2)), "property": _tidy(hat_off.group(3)), "region": ""}
    hat_off_region = re.fullmatch(
        rf"(?:please\s+)?(?:remove|take)\s+(?:the\s+)?({_ROLE})\s+hat\s+(?:off|from)\s+({_NAME})\s+in\s+region\s+({_NAME})",
        raw,
        re.I,
    )
    if hat_off_region:
        return {"kind": "clear_hat", "role": _tidy(hat_off_region.group(1)), "person": _tidy(hat_off_region.group(2)), "property": "", "region": _tidy(hat_off_region.group(3))}

    bot = re.fullmatch(rf"(?:please\s+)?(un)?mark\s+({_NAME})\s+as\s+a\s+bot", raw, re.I)
    if bot:
        return {"kind": "mark_bot", "person": _tidy(bot.group(2)), "on": not bool(bot.group(1))}

    watch = re.fullmatch(rf"(?:please\s+)?let\s+({_NAME})\s+watch\s+security", raw, re.I)
    if watch:
        return {"kind": "set_security_watch", "person": _tidy(watch.group(1)), "on": True}
    unwatch = re.fullmatch(rf"(?:please\s+)?stop\s+({_NAME})\s+(?:from\s+)?watching\s+security", raw, re.I)
    if unwatch:
        return {"kind": "set_security_watch", "person": _tidy(unwatch.group(1)), "on": False}

    inbox = re.fullmatch(rf"(?:please\s+)?set\s+my\s+password[- ]reset email\s+to\s+({_EMAIL})", raw, re.I)
    if inbox:
        return {"kind": "set_reset_email", "email": inbox.group(1)}
    if re.fullmatch(r"(?:please\s+)?(?:clear|remove)\s+my\s+password[- ]reset email", raw, re.I):
        return {"kind": "set_reset_email", "email": ""}
    if re.fullmatch(r"(?:please\s+)?password resets should use my login email", raw, re.I):
        return {"kind": "set_reset_email", "email": ""}

    reset = re.fullmatch(rf"(?:please\s+)?(?:send|email)\s+(?:a\s+)?password reset\s+to\s+({_NAME})", raw, re.I)
    if not reset:
        reset = re.fullmatch(rf"(?:please\s+)?send\s+({_NAME})\s+a\s+password reset", raw, re.I)
    if reset:
        return {"kind": "send_password_reset", "person": _tidy(reset.group(1))}

    test_mail = re.fullmatch(rf"(?:please\s+)?send\s+a\s+test\s+email\s+to\s+({_EMAIL})", raw, re.I)
    if test_mail:
        return {"kind": "send_test_email", "email": test_mail.group(1)}

    ban_ip = re.fullmatch(
        rf"(?:please\s+)?(?:temp\s+)?ban\s+ip\s+({_IP})\s+for\s+(\d{{1,3}})\s+hours?\s+because\s+(.+)",
        raw,
        re.I,
    )
    if ban_ip:
        return {"kind": "ban_ip", "ip": ban_ip.group(1), "hours": int(ban_ip.group(2)), "reason": _tidy(ban_ip.group(3))}
    lift_ip = re.fullmatch(rf"(?:please\s+)?(?:unban\s+ip|lift\s+the\s+ban\s+on\s+ip)\s+({_IP})", raw, re.I)
    if lift_ip:
        return {"kind": "unban_ip", "ip": lift_ip.group(1)}
    ban_device = re.fullmatch(
        rf"(?:please\s+)?(?:temp\s+)?ban\s+device\s+({_FP})\s+for\s+(\d{{1,3}})\s+hours?\s+because\s+(.+)",
        raw,
        re.I,
    )
    if ban_device:
        return {"kind": "ban_device", "device": ban_device.group(1).lower(), "hours": int(ban_device.group(2)), "reason": _tidy(ban_device.group(3))}
    lift_device = re.fullmatch(rf"(?:please\s+)?unban\s+device\s+({_FP})", raw, re.I)
    if lift_device:
        return {"kind": "unban_device", "device": lift_device.group(1).lower()}

    pinned = re.fullmatch(rf"(?:please\s+)?(un)?pin\s+(?:the\s+)?(?:property\s+)?({_NAME})", raw, re.I)
    if pinned and not re.search(r"\bunit\b", pinned.group(2) or "", re.I):
        return {"kind": "pin_property", "property": _tidy(pinned.group(2)), "on": not bool(pinned.group(1))}

    theme = re.fullmatch(
        rf"(?:please\s+)?(?:use|switch to|set)(?:\s+the)?\s+({_THEMES})(?:\s+theme)?",
        raw,
        re.I,
    )
    if not theme:
        theme = re.fullmatch(rf"(?:please\s+)?theme\s+({_THEMES})", raw, re.I)
    if theme:
        theme_id, label = _THEME_ALIASES[theme.group(1).lower()]
        return {"kind": "set_theme", "theme": theme_id, "label": label}

    layout = re.fullmatch(
        rf"(?:please\s+)?(?:use|switch to|set)(?:\s+the)?\s+({_LAYOUTS})(?:\s+layout)?",
        raw,
        re.I,
    )
    if not layout:
        layout = re.fullmatch(rf"(?:please\s+)?layout\s+({_LAYOUTS})", raw, re.I)
    if layout:
        layout_id, label = _LAYOUT_ALIASES[layout.group(1).lower()]
        return {"kind": "set_layout", "layout": layout_id, "label": label}

    if re.fullmatch(r"(?:please\s+)?(?:i(?:'m| am) driving|turn driving view on|driving view on)", raw, re.I):
        return {"kind": "drive", "on": True}
    if re.fullmatch(r"(?:please\s+)?(?:i(?:'m| am) not driving|i(?:'m| am) done driving|turn driving view off|driving view off)", raw, re.I):
        return {"kind": "drive", "on": False}

    if re.fullmatch(r"(?:please\s+)?(?:temp\s+)?(?:unban|ban)\b.*", raw, re.I) or re.search(r"\blift the ban\b", raw, re.I):
        return {"kind": "ask", "reply": _BAN_ASK}
    if re.search(r"\b(?:password reset|password-reset|reset (?:the |a |my )?password)\b", raw, re.I):
        return {"kind": "ask", "reply": _RESET_ASK}
    if re.search(r"\b(?:two[- ]factor|2fa)\b", raw, re.I):
        return {"kind": "ask", "reply": _FACTOR_ASK}
    return None


def map_property_name(text: str) -> str:
    """Property named when a chat photo is the site map. Empty when it is not."""
    raw = " ".join((text or "").strip().rstrip(".").split())
    found = re.fullmatch(
        rf"(?:this is|here(?:'s| is)|use this as|upload)\s+(?:the\s+)?(?:property\s+)?map\s+(?:for|of|at)\s+({_NAME})",
        raw,
        re.I,
    )
    return _tidy(found.group(1)) if found else ""


def _tidy(value: str) -> str:
    return " ".join((value or "").split()).strip(" .")


def office_sentence(user, text: str, key: str, source: str) -> dict | None:
    """Answer or stage one office sentence. None lets the rest of chat try."""
    intent = office_intent(text)
    if not intent:
        return None
    if intent.get("kind") == "map_photo":
        return {"ok": True, "reply": f"Send the picture in this chat and say this is the map for {intent.get('property')}."}
    if intent.get("kind") == "ask":
        return {"ok": True, "reply": intent.get("reply") or "Say a little more."}
    kind = intent["kind"]
    if kind == "show_map":
        return _show_map(user, intent, source, key)
    if kind == "list_regions":
        return _apply_read(user, "list_regions", {}, source, key)
    if kind == "send_back" and not intent.get("note"):
        return {"ok": True, "reply": "Say what still needs doing. Send back paint on unit 210 because the edges are rough."}
    if kind == "send_back" and not intent.get("job"):
        return {"ok": True, "reply": "Which item should I send back, and on which unit?"}
    if kind == "restore_inventory" and not intent.get("person"):
        return {"ok": True, "reply": "Who removed that inventory? Put back the inventory Jane deleted."}
    if kind == "save_how_to" and not intent.get("gear"):
        return {"ok": True, "reply": "Which piece of equipment are those instructions for?"}
    if kind == "drive":
        on = bool(intent.get("on"))
        return {
            "ok": True,
            "reply": "Driving view is on for 12 hours." if on else "Driving view is off.",
            "drive": "on" if on else "off",
        }
    if kind == "set_theme":
        return {
            "ok": True,
            "reply": f"{intent.get('label') or 'That theme'} is on.",
            "theme": intent.get("theme"),
        }
    if kind == "set_layout":
        return {
            "ok": True,
            "reply": f"{intent.get('label') or 'That layout'} is on.",
            "layout": intent.get("layout"),
        }
    if kind == "add_place_gear" and not intent.get("gear"):
        return {"ok": True, "reply": "What should I add, and at which property?"}
    tool, payload, say = _card(intent)
    if tool is None:
        return None
    payload["_say"] = say
    if tool in {"show_property_map", "list_regions"}:
        return _apply_read(user, tool, payload, source, key)
    from app.services.pending import request_apply

    return request_apply(user, tool, payload, source, key)


def _card(intent: dict) -> tuple[str | None, dict, str]:
    kind = intent["kind"]
    if kind == "remove_map":
        return "remove_property_map", {"property_name": intent["property"]}, f"Remove the map for {intent['property']}."
    if kind == "manage_region":
        action = intent["action"]
        payload = {key: intent[key] for key in ("action", "name", "new_name", "city", "person") if intent.get(key)}
        says = {
            "create": f"Create region {intent.get('name')}.",
            "rename": f"Rename region {intent.get('name')} to {intent.get('new_name')}.",
            "delete": f"Remove region {intent.get('name')}.",
            "add_city": f"Add {intent.get('city')} to region {intent.get('name')}.",
            "remove_city": f"Remove {intent.get('city')} from region {intent.get('name')}.",
            "add_person": f"Assign {intent.get('person')} to region {intent.get('name')}.",
            "remove_person": f"Remove {intent.get('person')} from region {intent.get('name')}.",
            "allow_default": f"Let {intent.get('person')} choose a default property in region {intent.get('name')}.",
            "deny_default": f"Stop {intent.get('person')} from choosing a default property in region {intent.get('name')}.",
        }
        return "manage_region", payload, says.get(action, "Update that region.")
    if kind == "send_back":
        payload = {"job": intent["job"], "unit_number": intent["unit"], "note": intent["note"], "property_name": intent.get("property") or ""}
        where = f" at {intent['property']}" if intent.get("property") else ""
        return "send_back", payload, f"Send {intent['job']} back on unit {intent['unit']}{where}: {intent['note']}."
    if kind == "mark_rentable":
        payload = {"unit_number": intent["unit"], "rentable": bool(intent["on"]), "property_name": intent.get("property") or ""}
        word = "ready to rent" if intent["on"] else "not rentable"
        return "mark_rentable", payload, f"Mark unit {intent['unit']} {word}."
    if kind == "set_move_out":
        payload = {"unit_number": intent["unit"], "move_out_date": intent["day"], "property_name": intent.get("property") or ""}
        return "set_move_out", payload, f"Set the move-out date on unit {intent['unit']} to {intent['day']}."
    if kind == "restore_inventory":
        return "restore_inventory", {"person": intent["person"], "days": intent.get("days") or 30}, f"Put back inventory removed by {intent['person']}."
    if kind == "reverse_audit":
        return "reverse_audit", {"audit_id": intent["audit_id"]}, f"Reverse audit {intent['audit_id']}."
    if kind == "unlock_login":
        return "unlock_login", {"person": intent["person"]}, f"Unlock the login for {intent['person']}."
    if kind == "remove_contractor":
        return "remove_contractor", {"name": intent["name"]}, f"Remove contractor {intent['name']}."
    if kind == "save_how_to":
        payload = {"gear": intent["gear"], "unit_number": intent.get("unit") or "", "property_name": intent.get("property") or "", "how_to": intent["text"]}
        return "save_how_to", payload, f"Save instructions for the {intent['gear']}."
    if kind == "create_job_title":
        return "create_job_title", {"label": intent["label"], "based_on": intent["based_on"]}, f"Add the job title {intent['label']} based on {intent['based_on']}."
    if kind == "add_place_gear":
        payload = {
            "kind": intent["gear"],
            "brand": intent.get("brand") or "",
            "property_name": intent.get("property") or "",
            "how_to": intent.get("text") or "",
            "every_days": intent.get("days") or 0,
            "task": intent.get("task") or "",
        }
        return "add_place_gear", payload, f"Add {intent['gear']} at {intent.get('property') or 'that property'}."
    if kind == "set_hat":
        payload = {"person": intent["person"], "role": intent["role"], "property_name": intent.get("property") or "", "region": intent.get("region") or ""}
        where = f"at {intent['property']}" if intent.get("property") else f"in region {intent.get('region')}"
        return "set_hat", payload, f"Give {intent['person']} a {intent['role']} hat {where}."
    if kind == "clear_hat":
        payload = {"person": intent["person"], "role": intent["role"], "property_name": intent.get("property") or "", "region": intent.get("region") or ""}
        where = f"at {intent['property']}" if intent.get("property") else f"in region {intent.get('region')}"
        return "clear_hat", payload, f"Remove the {intent['role']} hat from {intent['person']} {where}."
    if kind == "mark_bot":
        word = "a bot login" if intent.get("on") else "a regular login"
        return "mark_bot", {"person": intent["person"], "on": bool(intent.get("on"))}, f"Mark {intent['person']} as {word}."
    if kind == "set_security_watch":
        word = "watch security" if intent.get("on") else "stop watching security"
        return "set_security_watch", {"person": intent["person"], "on": bool(intent.get("on"))}, f"Let {intent['person']} {word}."
    if kind == "set_reset_email":
        email = intent.get("email") or ""
        say = f"Set your password-reset email to {email}." if email else "Use your login email for password resets."
        return "set_reset_email", {"email": email}, say
    if kind == "send_password_reset":
        return "send_password_reset", {"person": intent["person"]}, f"Email a password reset to {intent['person']}. The link is not shown here."
    if kind == "send_test_email":
        return "send_test_email", {"to": intent["email"]}, f"Send a test email to {intent['email']}."
    if kind == "ban_ip":
        return "ban_ip", {"ip": intent["ip"], "hours": intent["hours"], "reason": intent["reason"]}, f"Temp ban {intent['ip']} for {intent['hours']} hours."
    if kind == "unban_ip":
        return "unban_ip", {"ip": intent["ip"]}, f"Lift the ban on {intent['ip']}."
    if kind == "ban_device":
        return "ban_device", {"device_fp": intent["device"], "hours": intent["hours"], "reason": intent["reason"]}, f"Temp ban that device for {intent['hours']} hours."
    if kind == "unban_device":
        return "unban_device", {"device_fp": intent["device"]}, "Lift the ban on that device."
    if kind == "pin_property":
        word = "Pin" if intent.get("on") else "Unpin"
        return "pin_property", {"property_name": intent["property"], "pinned": bool(intent.get("on"))}, f"{word} {intent['property']}."
    return None, {}, ""


def _apply_read(user, tool: str, payload: dict, source: str, key: str) -> dict:
    from app.services.pending import apply_now

    return apply_now(user, tool, payload, source, key)


def _show_map(user, intent: dict, source: str, key: str) -> dict:
    name = intent.get("property") or ""
    if not name:
        from app.services.context import remembered_property

        remembered = remembered_property(user)
        if remembered is None:
            return {"ok": True, "reply": "Which property? Show the map for Oakwood."}
        name = remembered.name
    return _apply_read(user, "show_property_map", {"property_name": name}, source, key)
