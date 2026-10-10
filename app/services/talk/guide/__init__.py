"""Offline guide. Public entry is two names. Section files stay unloaded here."""
from __future__ import annotations


def start_guide(user, text: str, key: str, source: str):
    from app.services.talk.guide.engine import start_guide as _start

    return _start(user, text, key, source)


def answer_open_guide(user, text: str, key: str, source: str):
    from app.services.talk.guide.engine import answer_open_guide as _answer

    return _answer(user, text, key, source)
