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
    extra = dict(user.extra_data) if isinstance(getattr(user, "extra_data", None), dict) else {}
    extra["theme"] = found["id"]
    return _save_extra(user, extra, found["label"])


LAYOUTS = (
    {"id": "bar", "label": "Top bar", "line": "The menu stays across the top."},
    {"id": "rail", "label": "Side rail", "line": "A left panel for the menu. The work fills the rest."},
    {"id": "split", "label": "Split desk", "line": "Menu on the left. Work in the center. Property info on the right."},
    {"id": "dock", "label": "Right dock", "line": "Work on the left. Menu and property in a dock on the right."},
    {"id": "board", "label": "Card board", "line": "A short left index, and the work laid out as cards."},
    {"id": "ledger", "label": "Ledger", "line": "Dense rows, a left index, and almost no decoration."},
    {"id": "focus", "label": "Focus column", "line": "One centered column. The menu is a quiet line."},
    {"id": "canvas", "label": "Full canvas", "line": "Work runs edge to edge beside a slim index."},
)

LAYOUT_IDS = {item["id"] for item in LAYOUTS}
_LAYOUT_BY_ID = {item["id"]: item for item in LAYOUTS}


def layout_by_id(layout_id: str) -> dict | None:
    return _LAYOUT_BY_ID.get((layout_id or "").strip())


def save_layout(user, layout_id: str) -> str:
    """Store the account layout. Returns the label. Unknown ids raise ValueError."""
    found = layout_by_id(layout_id)
    if not found:
        raise ValueError("That layout is not on the list.")
    extra = dict(user.extra_data) if isinstance(getattr(user, "extra_data", None), dict) else {}
    extra["layout"] = found["id"]
    return _save_extra(user, extra, found["label"])


def _save_extra(user, extra: dict, label: str) -> str:
    from sqlalchemy.orm.attributes import flag_modified

    from app.builddb.builddb import db

    user.extra_data = extra
    try:
        flag_modified(user, "extra_data")
    except Exception:
        pass
    db.session.commit()
    return label
