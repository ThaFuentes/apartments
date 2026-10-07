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
layout_ids = [item["id"] for item in themes.LAYOUTS]
if len(layout_ids) < 8:
    raise SystemExit(f"wanted at least 8 layouts, got {len(layout_ids)}")
if len(set(layout_ids)) != len(layout_ids):
    raise SystemExit("duplicate layout ids")
if set(layout_ids) & set(ids):
    raise SystemExit("a layout id collides with a theme id")
desk_at = desk.find("@media (min-width: 900px)")
for item in themes.LAYOUTS:
    if themes.layout_by_id(item["id"])["label"] != item["label"]:
        raise SystemExit(f"layout lookup missed {item['id']}")
    if item["id"] == "bar":
        continue
    token = f'html[data-layout="{item["id"]}"]'
    if token not in desk:
        raise SystemExit(f"missing {token}")
    if desk.find(token) < desk_at:
        raise SystemExit(f"{token} is outside the desktop media query")
if themes.layout_by_id("nope") is not None:
    raise SystemExit("unknown layout should miss")
if 'html[data-layout="split"] body.ops:not(.gate)' not in desk:
    raise SystemExit("split desk must leave the sign-in page alone")
if "grid-template-columns: 15rem minmax(0, 1fr) 20rem" not in desk:
    raise SystemExit("split desk is missing the right-hand info column")
if "grid-template-columns: 16.5rem minmax(0, 1fr)" not in desk:
    raise SystemExit("side rail is missing")
if "grid-template-columns: minmax(0, 1fr) 18.5rem" not in desk:
    raise SystemExit("right dock is missing")
if "max-width: 44rem" not in desk:
    raise SystemExit("focus column is missing")
if "grid-template-columns: 7.25rem minmax(0, 1fr)" not in desk:
    raise SystemExit("full canvas is missing")
if "var(--glow)" not in desk or "var(--desk-a)" not in desk or "var(--desk-b)" not in desk:
    raise SystemExit("desk wash is still hardcoded")
if ".sec-body" not in desk or "#0c1117" not in desk:
    raise SystemExit("security console skin changed")
if "--shadow: none" not in css:
    raise SystemExit("contrast should drop the soft shadow")
if "import app" in office.split("def office_sentence", 1)[0]:
    raise SystemExit("office parser imported the app")
base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
viewer_nav, _, after_viewer = base.partition("current_user.role != 'viewer'")
# The desk nav's viewer branch is the second role check. Both copies of the
# switch must exist: one for the phone header, one inside the desk bar.
if base.count('include "theme_switch.html"') < 2:
    raise SystemExit("phone and desktop both need the Look menu")
if base.count('include "layout_switch.html"') < 2:
    raise SystemExit("phone and desktop both need the Arrange menu")
if 'data-layout="{{ desk_layout or \'bar\' }}"' not in base:
    raise SystemExit("the page is missing data-layout")
if 'class="desk-info"' not in base:
    raise SystemExit("property info is not in its own panel")
desk_nav = base.split('<nav class="desk-nav"', 1)[1].split("</nav>", 1)[0]
if 'include "theme_switch.html"' not in desk_nav:
    raise SystemExit("desktop bar is missing the Look menu")
if 'include "layout_switch.html"' not in desk_nav:
    raise SystemExit("desktop bar is missing the Arrange menu")
if "for-phone" not in base:
    raise SystemExit("phone header is missing the Look menu")
js = (ROOT / "app" / "static" / "js" / "apt.js").read_text(encoding="utf-8")
guard = js.split("button.disabled = true", 1)[0]
if "event.submitter" not in guard or "data-apt-submitter" not in guard:
    raise SystemExit("theme button value is dropped when the submit guard disables it")
if viewer_nav and after_viewer and "theme_switch.html" not in desk_nav:
    raise SystemExit("viewers cannot open Look from the desk")
print("themes ok")
