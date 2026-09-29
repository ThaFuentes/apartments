"""One capability of the system: what she can ask for, and how to do it."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Capability:
    tool: str          # the tool that does it, or "" for talk-only
    label: str         # short name, e.g. "plan a trip"
    says: tuple[str, ...] = field(default_factory=tuple)  # how she words it
    howto: str = ""    # one line answering "how do I …"


def lines(caps: list[Capability]) -> list[str]:
    """One prompt line per capability: label, tool, example words."""
    out = []
    for cap in caps:
        says = "; ".join(cap.says[:3])
        tool = cap.tool or "answer in words"
        out.append(f"- {cap.label} -> {tool}. She says things like: {says}.")
    return out


def match(caps: list[Capability], text: str) -> Capability | None:
    """The capability whose words best fit her sentence."""
    low = (text or "").lower()
    best = None
    best_score = 0
    for cap in caps:
        score = 0
        for phrase in cap.says:
            words = [word for word in phrase.lower().split() if len(word) > 2]
            score = max(score, sum(1 for word in words if word in low))
        if score > best_score:
            best, best_score = cap, score
    return best if best_score >= 2 else None
