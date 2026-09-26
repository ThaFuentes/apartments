"""Property names, streets, and edits from a sentence."""
from __future__ import annotations

import re

from app.services.pending import propose
from app.services.records import site_profile

from app.services.talk.outings import _close_questions, _slots_from_destination
from app.services.talk.phrases import ADD_SITE, ITS_AT, STATES, _ADDRESS_EDIT_VERB, _ADDRESS_LABEL, _NEW_SITE, _STREET
from app.services.talk.textutil import _clean_slot, _split_city, _split_state, _tidy_place
from app.services.talk.units import _board_payload, _file_unit_board

def _new_property_she_wants(text: str) -> dict | None:
    """The make/create/add sentence for a property: 'make me a property named
    woodview', with or without a city. Parses only; the caller asks or saves."""
    raw = (text or "").strip()
    if not raw or re.search(r"[.!?;]\s+\S", raw):
        return None
    if re.search(r"\b(?:delete|remove|rename|edit|update|change)\b", raw, re.I):
        return None
    match = _NEW_SITE.match(raw.rstrip(" .?!"))
    if not match:
        return None
    rest = (match.group(2) or "").strip(" .")
    rest = re.split(
        r"\s+(?:to|on|onto)\s+(?:my\s+)?(?:list|site|sites|propert(?:y|ies)|places|record|book)\b",
        rest,
        maxsplit=1,
        flags=re.I,
    )[0]
    rest = re.sub(r"\s+(?:please|thanks)$", "", rest, flags=re.I).strip(" .")
    city = region = ""
    tail = re.search(r"\s+(?:in|at|from|near)\s+(.+)$", rest, re.I)
    if tail:
        city, region = _split_state(tail.group(1).strip(" ."))
        rest = rest[: tail.start()].strip(" .")
    name = ""
    if rest:
        if city:
            # The city came from "in/at/from …", so the rest is her name whole:
            # "madison sq from lubbock" must not lose its "sq".
            name = rest
        else:
            slots = _slots_from_destination(rest)
            name = (slots.get("property_name") or slots.get("place") or "").strip()
            city = (slots.get("city") or "").strip()
            region = (slots.get("region") or "").strip()
    if name:
        from app.services.records import bare_property_name

        name = bare_property_name(name, city)
    generic = {
        "property", "properties", "prop", "place", "site", "apartment", "apartments",
        "complex", "community", "location", "it", "one", "new",
    }
    if name.lower() in generic:
        name = ""
    if not name and not city:
        return {"property_name": "", "city": "", "region": ""}
    return {"property_name": name, "city": city, "region": region}


def _file_new_property(user, text: str, key: str, source: str):
    parsed = _new_property_she_wants(text)
    if not parsed:
        return None
    from app.services.pending import commit_apply, propose

    name = parsed.get("property_name") or ""
    city = parsed.get("city") or ""
    region = parsed.get("region") or ""
    if name and not city:
        _close_questions(user)
        return propose(
            user,
            "upsert_property",
            {"property_name": name, "needs_answer": True, "waiting_for": "city"},
            f"What city is {name} in?",
            "low",
            key,
            key,
            source,
        )
    if not name:
        _close_questions(user)
        summary = f"What's the property's name in {city}?" if city else "What's the property's name?"
        return propose(
            user,
            "upsert_property",
            {"city": city, "region": region, "needs_answer": True, "waiting_for": "name"},
            summary,
            "low",
            key,
            key,
            source,
        )
    _close_questions(user)
    return commit_apply(
        user,
        "upsert_property",
        {"property_name": name, "city": city, "region": region},
        source,
        key,
    )


def _place_she_named(text: str) -> dict | None:
    """The property and city in her sentence, when she asked to add it."""
    raw = (text or "").strip()
    if re.search(r"^(what|which|when|where|who|how|did)\b", raw, re.I) or raw.endswith("?"):
        return None
    if not re.search(r"\b(add|create|save|put|look\s*up|lookup)\b", raw, re.I):
        return None
    tail = re.search(r"\bit(?:'s|s| is)\s+(.+)$", raw, re.I)
    blob = tail.group(1).strip(" .?") if tail else ""
    if not blob:
        cleaned = re.sub(r"^.*?\b(?:add|create|save|put)\s+", "", raw, count=1, flags=re.I)
        cleaned = re.split(r"\b(?:and|look\s*up|lookup|to my|please)\b", cleaned, maxsplit=1, flags=re.I)[0]
        blob = cleaned.strip(" .?")
    if not blob:
        return None
    # Not a new property: vendors, work orders, buildings, and anything aimed
    # at a unit belong to their own handlers — never to a new place.
    if re.search(r"\b(?:vendor|vendors|work\s+order|building)\b", blob, re.I):
        return None
    if re.search(r"\b(?:unit|apt|apartment)\s*#?\s*[0-9]", blob, re.I):
        return None
    if re.search(r"\bfrom\s+(?:unit\s*)?#?[0-9]", blob, re.I):
        return None
    slots = _slots_from_destination(blob)
    name = slots.get("property_name") or slots.get("place") or ""
    city = slots.get("city") or ""
    region = slots.get("region") or ""
    if name and not city:
        named = re.search(
            rf"\b({re.escape(name)})\s+([a-z][a-z .'-]{{2,40}}?)\s+(texas|tx|oklahoma|ok|new mexico|nm)\b",
            raw,
            re.I,
        )
        if named:
            city = _tidy_place(named.group(2))
            region = STATES.get(named.group(3).lower(), named.group(3).upper())
    from app.services.records import bare_property_name

    bare = bare_property_name(name, city)
    if city and not bare:
        if not re.match(r"^[a-z][a-z .'-]*$", city, re.I):
            return None
        return {"needs_name": True, "city": _tidy_place(city), "region": region}
    if not bare or not city:
        return None
    # A city is words, not a unit number, a range, or punctuation soup.
    if not re.match(r"^[a-z][a-z .'-]*$", city, re.I):
        return None
    generic = {"a", "the", "property", "properties", "place", "places", "site", "sites", "list", "address", "street", "name", "number", "it"}
    if bare.lower() in generic or city.lower() in generic:
        return None
    return {"property_name": bare, "city": city, "region": region}


def _given_property_address(text: str) -> dict | None:
    """She typed the street. Use that. Do not search the web for it."""
    raw = (text or "").strip().rstrip(".")
    if not re.search(r"\b(add|create|save|put|address|addy|property|properties)\b", raw, re.I):
        return None
    marker = re.search(r"\b(?:the\s+)?(?:address|addy)\s+is\s+", raw, re.I)
    if marker:
        head, tail = raw[: marker.start()], raw[marker.end() :]
    elif re.search(r"\b(address|addy)\b", raw, re.I):
        street = _STREET.search(raw)
        if not street:
            return None
        head, tail = raw[: street.start()], raw[street.start() :]
    else:
        return None
    street = _STREET.search(tail)
    if not street:
        return None
    from app.services.geo import state_name

    line = street.group(1).strip(" .,")
    line = re.sub(r"\b(TX|OK|NM|LA|AR|CO|KS)\b", lambda match: state_name(match.group(1)), line, flags=re.I)
    after = tail[street.end() :].strip(" .,")
    after = re.sub(r"^(?:in|at)\s+", "", after, flags=re.I)
    zip_code = ""
    zipped = re.search(r"\b(\d{5})(?:-\d{4})?\b", after)
    if zipped:
        zip_code = zipped.group(1)
        after = (after[: zipped.start()] + " " + after[zipped.end() :]).strip(" .,")
    city, region = _split_state(after)
    address = line
    if city:
        address = f"{line}, {city}" + (f", {region}" if region else "")
        if zip_code:
            address = f"{address} {zip_code}"
    name = re.sub(
        r"\b(please|can|you|add|create|save|put|edit|update|change|correct|this|the|a|an|property|properties|place|site|sites|name|called|named|is|its|it's|new|my|list|to)\b",
        " ",
        head,
        flags=re.I,
    )
    name = _tidy_place(_clean_slot(name))
    if not name or name.lower() in {"address", "addy", "street"}:
        return None
    return {"property_name": name, "city": city, "region": region, "address": address}


def _save_given_address(user, parsed: dict, key: str, source: str) -> dict:
    from app.services.pending import commit_apply
    from app.services.records import site_profile

    city = parsed.get("city") or ""
    region = parsed.get("region") or ""
    if not city:
        profile = site_profile()
        city = (profile.default_city if profile else "") or ""
        region = region or ((profile.default_region if profile else "") or "")
    if not city:
        return {"ok": True, "reply": f"I have {parsed['address']}. Which city is {parsed['property_name']} in?"}
    _close_questions(user)
    return commit_apply(
        user,
        "upsert_property",
        {
            "property_name": parsed["property_name"],
            "city": city,
            "region": region,
            "address": parsed["address"],
        },
        source,
        key,
    )


def _address_she_wants(text: str) -> dict | None:
    """A place and city she asked to look up on the web, not in her sites."""
    raw = (text or "").strip()
    if _given_property_address(raw):
        return None
    if not re.search(r"\b(address|google|online|look\s*up|lookup|search)\b", raw, re.I):
        return None
    if _place_she_named(raw):
        return None
    blob = ""
    found = re.search(r"\bfor\s+(?:the\s+)?(.+?)(?:\s+address|\s+find|\s+on\s+google|\s+online|\?|$)", raw, re.I)
    if found:
        blob = found.group(1)
    if not blob:
        found = re.search(r"\b(?:the\s+)?([a-z0-9][a-z0-9 .'-]{2,80}?)\s+address\b", raw, re.I)
        if found:
            blob = found.group(1)
    blob = re.sub(r"\b(the|online|not|my|site|google|please|whats|what's|what|is)\b", " ", blob or "", flags=re.I)
    blob = _clean_slot(blob)
    if not blob:
        return None
    slots = _slots_from_destination(blob)
    name = slots.get("property_name") or slots.get("place") or ""
    city = slots.get("city") or ""
    generic = {"a", "the", "property", "place", "site", "address", "street", "google", "online"}
    if not name or not city or name.lower() in generic or city.lower() in generic:
        return None
    return {"property_name": name, "city": city, "region": slots.get("region") or ""}


def _online_address(place: dict) -> dict:
    from app.services.geo import lookup_place

    name = place["property_name"]
    city = place["city"]
    found = lookup_place(name, city, place.get("region") or "")
    if not found or not found.get("address"):
        return {"ok": False, "reply": f"I searched online for {name} in {city} and didn't find a street address."}
    return {"ok": True, "reply": f"{name} in {city} is {found['address']}. That's from the web, not from your sites."}


def _typed_address(text: str) -> dict | None:
    raw = (text or "").strip().rstrip(".")
    if not re.search(r"\b(update|set|change|correct|address|addy)\b", raw, re.I):
        return None
    found = re.search(r"(\d{2,6}\s+[A-Za-z0-9.'#,\- ]{2,100})", raw)
    if not found:
        return None
    from app.services.geo import state_name

    address = re.sub(r"\b(TX|OK|NM|LA|AR|CO|KS)\b", lambda match: state_name(match.group(1)), found.group(1), flags=re.I)
    address = re.sub(r"\s+", " ", address).strip(" .,")
    head = raw[: found.start()]
    hint = re.sub(
        r"\b(please|update|set|change|correct|the|this|full|apartment|apartments|property|properties|with|address|addy|to|in|at|for)\b",
        " ",
        head,
        flags=re.I,
    )
    hint = _clean_slot(hint)
    if not hint:
        return None
    return {"property_name": hint, "address": address}


def _save_typed_address(user, parsed: dict, key: str, source: str) -> dict:
    from app.services.appliers import _property_match
    from app.services.pending import commit_apply

    prop, missing = _property_match({"property_name": parsed["property_name"]})
    if not prop:
        return {"ok": False, "reply": missing}
    return commit_apply(
        user,
        "update_property",
        {"property_id": prop.id, "address": parsed["address"]},
        source,
        key,
    )


def _named_place(text: str) -> dict | None:
    match = ITS_AT.match((text or "").strip().rstrip("."))
    if not match:
        return None
    slots = _slots_from_destination(match.group(1))
    name = slots.get("property_name") or slots.get("place") or ""
    city = slots.get("city") or ""
    if not name or not city:
        return None
    return {"property_name": name, "city": city, "region": slots.get("region") or ""}


def _site_to_add(text: str) -> dict | None:
    match = ADD_SITE.match((text or "").strip().rstrip("."))
    if not match:
        return None
    from app.services.records import bare_property_name

    city, region = _split_city(match.group(2))
    name = bare_property_name(match.group(1), city)
    if city and not name:
        return {"needs_name": True, "city": city, "region": region}
    if not name or not city:
        return None
    return {"property_name": name, "city": city, "region": region}


def _place_payload(place: str, arrive: bool = False) -> dict:
    words = [w for w in re.split(r"\s+", (place or "").strip(" .")) if w]
    profile = site_profile()
    payload = {"region": (profile.default_region if profile else "") or ""}
    if arrive:
        payload["arrive"] = True
    if len(words) >= 2:
        payload["city"] = words[-1]
        payload["property_name"] = " ".join(words[:-1])
    elif words:
        payload["property_name"] = words[0]
        payload["city"] = (profile.default_city if profile else "") or ""
    return payload


def _ask_property_address(user, hint: str) -> dict:
    """Treat an address request as an edit to a saved property, never a new property."""
    from app.services.parse import resolve_or_lines, resolve_property
    from app.services.records import property_place

    verdict = resolve_property(hint, user=user)
    if verdict["state"] == "resolved":
        prop = verdict["property"]
        return {"ok": True, "reply": f"What full street address should I save for {property_place(prop)}?"}
    if verdict["state"] == "ambiguous":
        _prop, question = resolve_or_lines(hint, user=user)
        return {"ok": True, "reply": question}
    return {
        "ok": False,
        "reply": f"I couldn't match {hint} to a saved property. Which saved property and city should get the address? I won't create a property from this request.",
    }


def _property_address_update_target(text: str) -> str:
    """Extract the saved-property side of a natural-language address request.

    Accept either ordering ("update the address for X" / "update X's address")
    without requiring a complete sentence template. A supplied street address is
    parsed separately; this function only identifies the existing property.
    """
    raw = re.sub(r"\s+", " ", (text or "")).strip().strip(" .!?;")
    if not raw or not re.search(r"\b(?:address|addy)\b", raw, re.I):
        return ""
    # "what's the address" and "find it on google" are lookups, not edits.
    lookup = re.search(r"\b(?:what(?:'s|s)?|where|google|online|search|look\s*up|lookup)\b", raw, re.I)
    editing = re.search(rf"\b{_ADDRESS_EDIT_VERB}\b", raw, re.I)
    if lookup and not editing:
        return ""

    target = ""

    # Property-first phrasing: "for X, update the address".
    match = re.search(
        rf"^for\s+(.+?)\s+(?:(?:can|could|would|will)\s+you\s+)?"
        rf"(?:please\s+)?{_ADDRESS_EDIT_VERB}\s+(?:(?:the|its|their)\s+)?{_ADDRESS_LABEL}\b",
        raw,
        re.I,
    )
    if match:
        target = match.group(1)

    # Field-first phrasing: "update the address for X" / "change address of X".
    if not target:
        match = re.search(rf"{_ADDRESS_LABEL}\s+(?:for|of|on|at)\s+(.+)$", raw, re.I)
        if not match:
            match = re.search(rf"{_ADDRESS_EDIT_VERB}\s+(?:the\s+)?{_ADDRESS_LABEL}\s+(?:for|of|on|at)\s+(.+)$", raw, re.I)
        if match:
            target = match.group(1)

    # Name-first phrasing: "update X address", "could you update X's address",
    # or "edit the address on X".
    if not target:
        match = re.search(
            rf"^\s*(?:(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?|(?:i\s+)?(?:want|need)\s+(?:to\s+)?)?"
            rf"{_ADDRESS_EDIT_VERB}\s+(?:the\s+|my\s+)?"
            rf"(?:{_ADDRESS_LABEL}\s+(?:for|of|on|at)\s+)?(.+?)(?:['’]s)?\s+{_ADDRESS_LABEL}\b",
            raw,
            re.I,
        )
        if match:
            target = match.group(1)

    if not target:
        return ""

    # A repeated label or typed street address introduces the value, not part
    # of the property name. Remove leading request verbs and joiners as needed.
    target = re.split(r"\s+(?:(?:with|and|to)\s+)?(?:the\s+)?(?:full\s+)?(?:address|addy)\s*[:=]", target, maxsplit=1, flags=re.I)[0]
    target = re.sub(r"^(?:(?:please|can|could|would|will|you|update|change|correct|set|edit|the|my)\s+)+", "", target, flags=re.I)
    street_start = re.search(r"\b\d{2,6}\s+[A-Za-z0-9.'#,\- ]{2,100}", target)
    if street_start:
        target = target[:street_start.start()]
        target = re.sub(r"\s+(?:to|at|with|and|address|addy)\s*$", "", target, flags=re.I)
    target = re.sub(r"\s+(?:with|and|to)\s+(?:the\s+)?(?:full\s+)?(?:address|addy)\b.*$", "", target, flags=re.I)
    target = re.sub(r"\s+(?:full\s+)?(?:address|addy)\s*[.!?]*\s*$", "", target, flags=re.I)
    target = re.sub(r"\s+too\s*$", "", target, flags=re.I)
    target = re.sub(r"^\s*(?:the|a|an)\s+", "", target, flags=re.I)
    return _tidy_place(target.strip(" .,:;!?"))


def _property_delete(text: str) -> dict | None:
    raw = (text or "").strip()
    if not re.search(r"\b(delete|remove|removed|removing)\b", raw, re.I):
        return None
    if re.search(r"\b(plan|plans|trip|trips|job|jobs|unit|units|expense|expenses|miles|work)\b", raw, re.I):
        return None
    delete_all = bool(re.search(r"\b(all|every)\b", raw, re.I))
    cleaned = re.sub(
        r"\b(please|delete|remove|removed|removing|the|my|our|this|that|all|every|property|properties|site|sites|place|places|apartment|apartments)\b",
        " ",
        raw,
        flags=re.I,
    )
    name = _clean_slot(cleaned)
    if not name or name.lower() in {"all", "every"}:
        return None
    return {"property_name": name, "delete_all": delete_all}


def _property_rename(text: str) -> dict | None:
    raw = (text or "").strip().rstrip(".")
    match = re.search(r"\brename\s+(.+?)\s+to\s+(.+)$", raw, re.I)
    if not match:
        match = re.search(r"\bchange\s+(?:the\s+)?address\s+(?:of|for)\s+(.+?)\s+to\s+(.+)$", raw, re.I)
        if not match:
            return None
        target = re.sub(r"^\s*(?:the\s+)?(?:property|prop|place|site|apartment|apt)\s+", "", match.group(1), flags=re.I)
        slots = _slots_from_destination(target)
        return {
            "match_name": slots.get("property_name") or slots.get("place") or target.strip(),
            "city": slots.get("city") or "",
            "address": match.group(2).strip(),
        }
    target = re.sub(r"^\s*(?:the\s+)?(?:property|prop|place|site|apartment|apt)\s+", "", match.group(1), flags=re.I)
    slots = _slots_from_destination(target)
    return {
        "match_name": slots.get("property_name") or slots.get("place") or target.strip(),
        "city": slots.get("city") or "",
        "new_name": _tidy_place(match.group(2)),
    }


def _ask_remove(user, parsed: dict, key: str, source: str) -> dict:
    from app.services.pending import propose
    from app.services.records import fuzzy_properties, properties_like, property_place

    hint = (parsed.get("property_name") or "").strip()
    if parsed.get("delete_all"):
        matches = properties_like(hint)
        if not matches:
            return {"ok": False, "reply": f"No property named {hint}."}
        lines = "\n".join(property_place(prop) for prop in matches)
        if len(matches) == 1:
            summary = f"Remove {property_place(matches[0])}? Say yes."
            payload = {"property_id": matches[0].id}
        else:
            summary = f"Remove all {len(matches)} {hint} apartments?\n{lines}\nSay yes."
            payload = {"property_ids": [prop.id for prop in matches]}
        return propose(user, "delete_property", payload, summary, "material", key, key, source)
    matches = fuzzy_properties(hint)
    if not matches:
        return {"ok": False, "reply": f"No property named {hint}."}
    if len(matches) > 1:
        lines = "\n".join(property_place(prop) for prop in matches)
        return {
            "ok": True,
            "reply": f"There is more than one {hint}. Say the city, or say delete all {hint}.\n{lines}",
        }
    place = property_place(matches[0])
    return propose(
        user,
        "delete_property",
        {"property_id": matches[0].id},
        f"Remove {place}? Say yes.",
        "material",
        key,
        key,
        source,
    )


def _file_remove(user, text: str, key: str, source: str):
    parsed = _property_delete(text)
    if not parsed:
        return None
    _close_questions(user)
    return _ask_remove(user, parsed, key, source)


def _file_edit(user, text: str, key: str, source: str):
    """Edit a named record or property without guessing the target."""
    board_action = _board_payload(text)
    if board_action and board_action.get("action") in {"edit_record", "set_building", "set_unit_number"}:
        return _file_unit_board(user, text, key, source)
    typed_address = _typed_address(text) or {}
    address_target = _property_address_update_target(text)
    if not _is_edit(text) and not (address_target and typed_address.get("address")):
        return None
    if re.search(r"\b(office manager|employee|let|give|allow)\b", text or "", re.I):
        return None
    if re.search(r"\b(plan|trip)\b", text or "", re.I) and not re.search(r"\b(address|addy|property)\b", text or "", re.I):
        return None
    from app.services.pending import commit_apply
    from app.services.parse import resolve_or_lines, resolve_property
    from app.services.records import property_place

    parsed = _given_property_address(text) or {}
    typed = _typed_address(text) or {}
    if typed.get("address") and not parsed.get("address"):
        parsed = {**parsed, "address": typed["address"]}
    if address_target and (parsed.get("address") or typed.get("address")):
        # In "for Madison Sq can you update the address Address: 2201 ...",
        # the leading "for" introduces the existing target; it is not part of
        # the property name. Prefer this explicit edit target over generic slot
        # extraction, which can absorb the repeated word "Address".
        parsed["property_name"] = address_target
    hint = parsed.get("property_name") or typed.get("property_name") or ""
    if not hint:
        cleaned = re.sub(
            r"\b(please|edit|update|change|correct|the|this|my|property|properties|apartment|apartments|address|addy|name)\b",
            " ",
            text or "",
            flags=re.I,
        )
        hint = _tidy_place(_clean_slot(cleaned))
    if not hint:
        return {"ok": True, "reply": "Which property should I edit?"}
    verdict = resolve_property(hint, user=user)
    if verdict["state"] == "unknown":
        return {"ok": False, "reply": f"I don't have {hint} yet, so I didn't create one. Say add {hint} if it is new."}
    prop, ask = resolve_or_lines(hint, user=user)
    if not prop:
        return {"ok": True, "reply": ask}
    payload = {"property_id": prop.id}
    if parsed.get("address"):
        payload["address"] = parsed["address"]
    if parsed.get("city"):
        payload["city"] = parsed["city"]
        payload["region"] = parsed.get("region") or ""
    _close_questions(user)
    return commit_apply(user, "update_property", payload, source, key)


def _is_edit(text: str) -> bool:
    return bool(re.search(r"\b(edit|update|change|correct)\b", text or "", re.I))
