"""What this system can do, in one place.

Two consumers: the model prompt (so a saved key can do virtually anything) and
local answers (so 'what can you do' works even with the keys off). The topic
modules in this folder each hold one slice of the list.
"""
from __future__ import annotations

from app.services.caps import money, office, people, places, reports, trips, units
from app.services.caps.schema import Capability, lines, match

TOPICS = (
    ("Trips and plans", trips.CAPS),
    ("Properties and addresses", places.CAPS),
    ("Units and gear", units.CAPS),
    ("Gas, food, and miles", money.CAPS),
    ("Questions and reports", reports.CAPS),
    ("People and permissions", people.CAPS),
    ("Office", office.CAPS),
)

ALL: list[Capability] = [cap for _topic, caps in TOPICS for cap in caps]

ROLE_WORDS = "owner, admin, regional manager, property manager, assistant manager, office, maintenance manager, maintenance person"

CONFIRM_RULES = (
    "Every change waits for her yes: your tool call stages a card showing the full "
    "before and after details, and she clicks Save or says yes to allow it. "
    "Never say something is saved or done before she confirmed it. "
    "Say 'Nothing changes until you save it' when you stage something important, "
    "especially people and permissions."
)


def rules() -> str:
    """The capability map for the model prompt: anything she can ask for."""
    out = ["What she can ask for. Match her sentence to one of these and call its tool:"]
    out.extend(lines(ALL))
    out.append(
        "Anything else: a question about her record is query_record. "
        "A how-to question gets one or two short sentences built from this list, no tool. "
        "Small talk gets one short line. "
        "Something this system cannot do: say so in one line and name the closest thing it can do. "
        "Never answer with silence or a bare apology."
    )
    out.append(f"Roles are: {ROLE_WORDS}.")
    out.append(CONFIRM_RULES)
    return "\n".join(out)


def help_reply(text: str, role: str | None = None, user=None) -> dict | None:
    """Local answer for 'what can you do' and 'how do I …', with the keys off.

    When a role is given, the reply is scoped to what that role can see and do.
    """
    low = (text or "").strip().lower()
    if not low:
        return None
    asking_for_menu = any(phrase in low for phrase in ("what can you do", "what do you do", "help me", "can you do", "your job", "commands"))
    how_to = low.startswith(("how do i", "how can i", "how does", "whats the way", "what's the way"))
    is_help = low == "help" or low.startswith("show me the help") or low.startswith("open help") or low.startswith("go to help")
    if not asking_for_menu and not how_to and not is_help:
        return None
    from app.services.caps.help_content import role_help_topics

    allowed = [cap for _label, caps in role_help_topics(role, user=user) for cap in caps] if role else ALL
    cap = match(allowed, low)
    if how_to and cap and cap.howto:
        return {"ok": True, "reply": cap.howto}
    if asking_for_menu or how_to or is_help:
        from app.services.talk.guide.menu import offline_menu

        reply = offline_menu(user)
        if is_help and low != "help":
            reply["help_page_url"] = "/help"
            return reply
        reply["guide_menu"] = True
        return reply
    return None
