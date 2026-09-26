"""Words and patterns the chat matches against."""
from __future__ import annotations

import re
CONFIRM_SAVE = {
    "yes, save it",
    "yes save it",
    "save it",
    "save",
    "confirm",
    "accept",
    "do it",
}

CONFIRM_YES = {"yes", "y", "yeah", "yep", "correct", "that's right", "thats right", "right"}

DISCARD = {"no", "discard", "cancel", "never mind", "nevermind", "don't save", "dont save"}

GOING = re.compile(
    r"\b(?:going|headed|heading)\s+to\s+(.+?)\s+(?:on\s+)?(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{4}-\d{2}-\d{2})(?:\s+for\s+(.+))?$",
    re.I,
)

ARRIVE = re.compile(
    r"^(?:i(?:'m| am)?\s+at|start(?:ing)?(?:\s+the)?\s+visit(?:\s+at)?)\s+(.+)$",
    re.I,
)

UNIT_JOB = re.compile(
    r"^(?:new\s+unit\s+)?(?:unit\s*)?#?\s*([a-z0-9][a-z0-9\-]{0,12})\s*[—–\-:]\s*(.+)$",
    re.I,
)

SKIP = re.compile(r"\b(?:skip|nobody home)\b", re.I)

NEXT_UNIT = re.compile(r"\bnext unit\b", re.I)

END_DAY = re.compile(r"\b(end the day|day is done|trip(?: is|'s)? done)\b", re.I)

END_VISIT = re.compile(r"\b(end (?:the )?visit|leaving|done here|done at this property)\b", re.I)

REPORT = re.compile(r"\b(company report|report for (?:my )?boss(?:es)?|boss report|weekly report|property report)\b", re.I)

SEND = re.compile(r"\b(send (?:the |this )?(?:weekly |company |boss )?report)\b", re.I)

QUESTION = re.compile(r"^(what|which|when|where|how many|show me|did we|in\s+.+\s+what)\b", re.I)

ADD_SITE = re.compile(
    r"^(?:please\s+)?(?:add|create|save|put)\s+(.+?)\s+(?:from|in|at)\s+(.+?)(?:\s+to\s+(?:my\s+)?(?:sites|site|properties|property|places|list))?$",
    re.I,
)

STATES = {
    "texas": "Texas",
    "tx": "Texas",
    "oklahoma": "Oklahoma",
    "ok": "Oklahoma",
    "new mexico": "New Mexico",
    "nm": "New Mexico",
    "louisiana": "Louisiana",
    "la": "Louisiana",
    "arkansas": "Arkansas",
    "ar": "Arkansas",
    "colorado": "Colorado",
    "co": "Colorado",
    "kansas": "Kansas",
    "ks": "Kansas",
}

ADD_USER = re.compile(
    r"\b(?:add|invite|give)\s+(?:my\s+)?(?:a\s+|an\s+)?(regional manager|property manager|assistant manager|maintenance manager|maintenance person|office manager|admin|employee|viewer|boss|user|field|owner|office|read-only|readonly)\s+([a-z0-9][a-z0-9._-]{1,40})",
    re.I,
)

AS_ROLE = re.compile(
    r"\b(?:add|invite)\s+(?:login\s+)?([a-z0-9][a-z0-9._-]{1,40})\s+as\s+(?:an?\s+)?(regional manager|property manager|assistant manager|maintenance manager|maintenance person|office manager|admin|employee|boss|owner|viewer|field|office)\b",
    re.I,
)

GIVE_BOSS = re.compile(r"\bgive my boss\b|\bread-only access\b", re.I)

DELETE = re.compile(r"\b(?:delete|remove)\s+(job|unit|expense|task|unit task|equipment)\s+(\d+)\b", re.I)

RESTORE = re.compile(r"\brestore\s+(job|unit|expense|task|unit task|equipment)\s+(\d+)\b", re.I)

UNIT_RECORD_REMOVE = re.compile(r"\b(?:delete|remove|restore)\s+unit\s*#?([a-z0-9-]+)\s+(?:at|in)\s+(.+)$", re.I)

MILES = re.compile(r"\b(\d{1,4}(?:\.\d)?)\s*miles\b", re.I)

ODO_ONLY = re.compile(r"^(?:odometer|odo)\s*#?\s*(\d{4,7})$", re.I)

DROVE = re.compile(r"\b(?:drove|driven|drive was|add)\s+(\d{1,4}(?:\.\d)?)\s*miles\b", re.I)

SETTINGS = re.compile(r"\b(?:call yourself|assistant name|company name|home base|default city|my tone)\b", re.I)

AMOUNT = re.compile(r"\$\s*(\d{1,5}(?:\.\d{2})?)|(?<!\d)(\d{1,5}\.\d{2})(?!\d)")

ODO = re.compile(r"(?:odometer|odo)\s*#?\s*(\d{4,7})", re.I)

EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")

PASSWORD = re.compile(r"\bpassword\s+(\S+)", re.I)

_ASK_MARK = re.compile(
    r"\b(plan|schedule|work\s+order|add\s+units?|add\s+building|create\s+building|make\s+ready|occupied|address\s+is|add\s+(?:this\s+)?property|office\s+manager)\b",
    re.I,
)

_ORDINALS = {"1st": 1, "first": 1, "2nd": 2, "second": 2, "3rd": 3, "third": 3, "4th": 4, "fourth": 4}

_NEW_SITE = re.compile(
    r"^(?:(?:can|could|will|would)\s+(?:you\s+)?)?(?:please\s+)?"
    r"(?:(?:make|create|add|save|put|start|new)\s+(?:me\s+|us\s+)?"
    r"|(?:i\s+)?(?:want|need|would\s+like(?:\s+to\s+have)?)\s+)"
    r"(?:a|an|the)?\s*(?:new\s+)?"
    r"(property|prop|place|site|apartment(?:s)?|complex|community|location)"
    r"\s*(?:named|called|name)?\s*[:=]?\s*(.*)$",
    re.I,
)

_STREET = re.compile(
    r"\b(\d{1,6}\s+(?:[NSEW]\.?\s+)?[A-Za-z0-9.'#\-]+(?:\s+[A-Za-z0-9.'#\-]+){0,5}\s+(?:street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd|way|court|ct|circle|cir|parkway|pkwy|trail|trl|highway|hwy|place|pl)\.?)",
    re.I,
)

_ROLE_NAMES = r"regional manager|property manager|assistant manager|maintenance manager|maintenance person|office manager|administrator|admin|maintenance|employee|worker|office|boss|viewer|owner|field"

_STAFF_ROLE_FIRST = re.compile(
    rf"\b(?:create|add|make)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?({_ROLE_NAMES})\s+(?:named\s+|called\s+|user\s+)?([a-z][a-z0-9._-]{{1,40}})",
    re.I,
)

_STAFF_NAME_FIRST = re.compile(
    rf"\b(?:create|add|make)\s+(?:a\s+|an\s+)?(?:new\s+)?(?:user|login|person)\s+(?:named\s+|called\s+)?([a-z][a-z0-9._-]{{1,40}})\s+as\s+(?:an?\s+)?({_ROLE_NAMES})",
    re.I,
)

_UNIT_GEAR = re.compile(
    r"^(?P<head>.+?)\s+(?:to|in|on|for|into)?\s*(?:unit|apt|apartment)\s*#?\s*(?P<num>[0-9]{1,6}[a-z]?)\b"
    r"(?:\s*,?\s+(?:at|in)\s+(?P<place>[a-z][a-z0-9 .',&/-]{1,60}))?[.!?]?$",
    re.I,
)

_GEAR_VERB = re.compile(
    r"\b(?:add(?:ed|ing)?|install(?:ed|ing)?|replaced?|put|puts|swapped|mounted|set\s+up|hooked\s+up)\b",
    re.I,
)

DAY_WORD = r"today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{4}-\d{2}-\d{2}"

MILES_PHRASE = re.compile(r"\b(?:with|about|around|roughly)?\s*(\d{1,4}(?:\.\d)?)\s*miles\b", re.I)

WHEN_WORD = re.compile(rf"\b({DAY_WORD})\b", re.I)

HERE = re.compile(r"^(?:i(?:'m| am)\s+here|i(?:'ve| have)\s+arrived|arrived|i(?:'m| am)\s+there)$", re.I)

WORK_VERB = re.compile(
    r"\b(replaced|installed|fixed|repaired|changed|checked|inspected|cleaned|swapped|hooked up|put in|worked on)\b",
    re.I,
)

ITS_AT = re.compile(r"^(?:it(?:'s| is)|its)\s+at\s+(.+)$", re.I)

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

_MONTH_WORD = "|".join(_MONTHS)

_WORK_VERB = re.compile(
    r"\b(did|replaced|installed|fixed|repaired|changed|checked|inspected|cleaned|swapped|put in|worked on|completed)\b",
    re.I,
)

_ADDRESS_LABEL = r"(?:full\s+)?(?:address|addy)"

_ADDRESS_EDIT_VERB = r"(?:update|change|correct|set|edit)"
