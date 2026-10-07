"""Desk themes. Importing this file does not open the database."""
from __future__ import annotations

THEMES = (
    {"id": "paper", "label": "Warm paper", "line": "Cream, sage, and cedar.", "color": "#f6efe6"},
    {"id": "night", "label": "Field night", "line": "Dark field colors for night walks.", "color": "#121614"},
    {"id": "slate", "label": "Office slate", "line": "Cool gray and navy.", "color": "#e7edf2"},
    {"id": "contrast", "label": "High contrast", "line": "Black, white, and hard edges.", "color": "#ffffff"},
    {"id": "desert", "label": "Desert", "line": "Sand, clay, and rust.", "color": "#f3e6d0"},
    {"id": "harbor", "label": "Harbor", "line": "Fog, navy, and brass.", "color": "#e4eef3"},
    {"id": "ink", "label": "Ink", "line": "Near-black with a copper line.", "color": "#0e1014"},
    {"id": "grove", "label": "Grove", "line": "Linen and forest.", "color": "#e7f0e4"},
    {"id": "dusk", "label": "Dusk", "line": "Plum evening.", "color": "#1b1520"},
    {"id": "chalk", "label": "Chalk", "line": "Bright board with a red mark.", "color": "#f4f1ea"},
)

THEME_IDS = {item["id"] for item in THEMES}
_BY_ID = {item["id"]: item for item in THEMES}


def theme_by_id(theme_id: str) -> dict | None:
    return _BY_ID.get((theme_id or "").strip())


def theme_color(theme_id: str) -> str:
    found = theme_by_id(theme_id)
    return found["color"] if found else _BY_ID["paper"]["color"]


def save_theme(user, theme_id: str) -> str:
    """Store the account theme. Returns the label. Unknown ids raise ValueError."""
    found = theme_by_id(theme_id)
    if not found:
        raise ValueError("That theme is not on the list.")
    from sqlalchemy.orm.attributes import flag_modified

    from app.builddb.builddb import db

    extra = dict(user.extra_data) if isinstance(getattr(user, "extra_data", None), dict) else {}
    extra["theme"] = found["id"]
    user.extra_data = extra
    try:
        flag_modified(user, "extra_data")
    except Exception:
        pass
    db.session.commit()
    return found["label"]
