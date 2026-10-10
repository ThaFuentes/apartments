"""What we say when chat is leading and nobody has started a job."""
from __future__ import annotations


def _offline(user) -> bool:
    from app.services.providers import keys_for

    return not keys_for(user)


def offline_menu(user) -> dict:
    """Plain greeting. It does not open a walk and it does not save anything."""
    head = "AI chat is offline. How can I help?" if _offline(user) else "How can I help?"
    reply = (
        f"{head}\n\n"
        "Say what you would like to do. Make ready, vendor, units, and equipment come first. "
        "More covers plans, places, money, reports, people, and office.\n\n"
        "I'll ask one question at a time. Nothing is saved until you say so."
    )
    return {"ok": True, "reply": reply}
