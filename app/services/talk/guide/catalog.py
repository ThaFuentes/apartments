"""The index. A job that is not listed here does not exist."""
from __future__ import annotations

import importlib

from app.services.talk.guide.script import problems

# module, attribute. Imported only when a walk starts.
_LOADERS = (
    ("guide_menu", "app.services.talk.guide.sections.home", "MENU"),
    ("make_ready", "app.services.talk.guide.sections.ready", "MAKE_READY"),
    ("finish_vendor_trade", "app.services.talk.guide.sections.vendors", "FINISH"),
)


def load(job_id: str):
    for name, module, attr in _LOADERS:
        if name == job_id:
            job = getattr(importlib.import_module(module), attr)
            bad = problems(job)
            if bad:
                raise RuntimeError(f"{job_id} is not a complete guide: {', '.join(bad)}")
            return job
    return None


def all_jobs() -> list:
    return [load(name) for name, _module, _attr in _LOADERS]


def match(text: str):
    hits = [job for job in all_jobs() if job.matches(text)]
    if len(hits) == 1:
        return hits[0]
    return None
