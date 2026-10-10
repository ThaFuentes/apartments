"""The shape every guided job must have. The engine is the only loop."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Step:
    """One question. ask looks at the record. take reads the reply."""

    id: str
    required: bool
    ask: Callable
    take: Callable
    clears: tuple = ()
    drops: tuple = ()


@dataclass(frozen=True)
class Job:
    """One walk. matches, absorb, and the steps live with the section. The engine does not."""

    id: str
    tool: str
    steps: tuple
    matches: Callable
    absorb: Callable
    allow: Callable
    review: Callable
    apply_payload: Callable
    changes: Callable


def problems(job: Job) -> list[str]:
    """Empty when this job is safe to register."""
    found = []
    if not job.id:
        found.append("missing id")
    if not job.tool:
        found.append("missing tool")
    elif len(job.tool) > 40:
        found.append("tool name is longer than the column")
    if not job.steps:
        found.append("no steps")
    seen = set()
    for step in job.steps:
        if not step.id:
            found.append("step missing id")
        elif step.id in seen:
            found.append(f"duplicate step {step.id}")
        seen.add(step.id)
        if not step.ask or not step.take:
            found.append(f"{step.id or '?'} missing ask or take")
    for name in ("matches", "absorb", "allow", "review", "apply_payload", "changes"):
        if not getattr(job, name, None):
            found.append(f"missing {name}")
    return found
