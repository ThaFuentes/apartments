"""Questions about the record, and the reports."""
from app.services.caps.schema import Capability

CAPS = [
    Capability(
        tool="query_record",
        label="answer a question about her record",
        says=(
            "what did I do at woodview last week",
            "how much gas this week",
            "which units did I visit tuesday",
        ),
        howto="Just ask. Questions never change the record.",
    ),
    Capability(
        tool="draft_report",
        label="build a report",
        says=(
            "build the weekly report",
            "company report for the bosses",
            "property report for woodview",
        ),
        howto="Say weekly, company, or property report. It is staged and waits for your yes.",
    ),
    Capability(
        tool="send_report",
        label="send a saved report",
        says=(
            "send the report to the bosses",
        ),
        howto="Say send the report. It goes to the boss logins, and emails only the ones with an email.",
    ),
]
