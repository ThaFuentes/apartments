# ===========================================================
# poweredbytop/security/sanitizer.py
# Input sanitization — strip control chars / nulls, length caps.
# Does NOT strip all quotes (breaks names/notes). XSS primary
# defense remains Jinja autoescape + CSP.
# ===========================================================
import re

from .helpers import safe_log

# Control chars except tab/newline
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Obvious script / event handlers when storing free text
_SCRIPTISH = re.compile(
    r"(?is)<\s*script|javascript\s*:|on\w+\s*=|<\s*iframe|<\s*object|<\s*embed"
)


def sanitize_text(text: str, max_length: int = 5000) -> str:
    if not text or not isinstance(text, str):
        return ""
    try:
        clean = _CTRL.sub("", text).replace("\x00", "").strip()
        if _SCRIPTISH.search(clean):
            clean = _SCRIPTISH.sub("", clean)
        return clean[:max_length]
    except Exception as e:
        safe_log(f"Sanitization failed: {str(e)[:100]}", level="warning")
        return ""


def sanitize_for_db(text: str, max_length: int = 10000) -> str:
    return sanitize_text(text, max_length=max_length)


def sanitize_html(dirty_html: str) -> str:
    """Strip to text-safe. Full HTML allowlists are out of scope; do not use |safe on this."""
    return sanitize_text(dirty_html or "", max_length=20000)
