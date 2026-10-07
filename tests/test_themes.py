"""Theme catalog checks. Does not start the app or MariaDB."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("apt_themes", ROOT / "app" / "services" / "themes.py")
themes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(themes)

css = (ROOT / "app" / "static" / "css" / "apt.css").read_text(encoding="utf-8")
desk = (ROOT / "app" / "static" / "css" / "apt-desk.css").read_text(encoding="utf-8")
office = (ROOT / "app" / "services" / "talk" / "office.py").read_text(encoding="utf-8")

ids = [item["id"] for item in themes.THEMES]
if len(ids) < 10:
    raise SystemExit(f"wanted at least 10 themes, got {len(ids)}")
if len(set(ids)) != len(ids):
    raise SystemExit("duplicate theme ids")
for item in themes.THEMES:
    token = f'html[data-theme="{item["id"]}"]'
    if token not in css:
        raise SystemExit(f"missing {token}")
    color = item["color"]
    if not color.startswith("#") or len(color) not in {4, 7}:
        raise SystemExit(f"bad color {item}")
    if themes.theme_by_id(item["id"])["label"] != item["label"]:
        raise SystemExit(f"lookup missed {item['id']}")
if themes.theme_by_id("nope") is not None:
    raise SystemExit("unknown theme should miss")
if "var(--glow)" not in desk or "var(--desk-a)" not in desk or "var(--desk-b)" not in desk:
    raise SystemExit("desk wash is still hardcoded")
if ".sec-body" not in desk or "#0c1117" not in desk:
    raise SystemExit("security console skin changed")
if "--shadow: none" not in css:
    raise SystemExit("contrast should drop the soft shadow")
if "import app" in office.split("def office_sentence", 1)[0]:
    raise SystemExit("office parser imported the app")
print("themes ok")
