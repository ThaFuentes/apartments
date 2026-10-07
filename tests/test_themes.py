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
base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
viewer_nav, _, after_viewer = base.partition("current_user.role != 'viewer'")
# The desk nav's viewer branch is the second role check. Both copies of the
# switch must exist: one for the phone header, one inside the desk bar.
if base.count('include "theme_switch.html"') < 2:
    raise SystemExit("phone and desktop both need the Look menu")
desk_nav = base.split('<nav class="desk-nav"', 1)[1].split("</nav>", 1)[0]
if 'include "theme_switch.html"' not in desk_nav:
    raise SystemExit("desktop bar is missing the Look menu")
if "for-phone" not in base:
    raise SystemExit("phone header is missing the Look menu")
js = (ROOT / "app" / "static" / "js" / "apt.js").read_text(encoding="utf-8")
guard = js.split("button.disabled = true", 1)[0]
if "event.submitter" not in guard or "data-apt-submitter" not in guard:
    raise SystemExit("theme button value is dropped when the submit guard disables it")
if viewer_nav and after_viewer and "theme_switch.html" not in desk_nav:
    raise SystemExit("viewers cannot open Look from the desk")
print("themes ok")
