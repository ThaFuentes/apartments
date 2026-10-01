"""Owner security console: threat map, events, product audit reversals."""
from __future__ import annotations

from datetime import timedelta

import os

from flask import current_app, flash, jsonify, redirect, render_template, request, send_from_directory
from flask_login import current_user

from app.builddb.builddb import db
from app.models import AuditLog, User
from app.routes.common import bp, owner_required
from app.services.clock import utcnow
from app.services.records import loads
from app.services.security_queries import list_bans, list_security_events, summary_stats


def _window():
    raw = (request.args.get("window") or "live").strip().lower()
    if raw in {"live", "24h", "7d", "30d", "lifetime", "4h"}:
        return raw
    return "live"


def _ensure_geo() -> None:
    from app.services.security_queries import _close, _sec
    from app.services.threat_geo import ensure_ip_geo_table

    conn = _sec()
    if conn is None:
        return
    try:
        ensure_ip_geo_table(conn)
    except Exception as exc:
        print(f"[apt-security] geo table: {exc}", flush=True)
    finally:
        _close(conn)


@bp.get("/security/console.css")
def security_console_css():
    css = render_template("security/console.css")
    resp = current_app.response_class(css, mimetype="text/css; charset=utf-8")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@bp.get("/security/assets/<path:name>")
def security_assets(name):
    root = os.path.join(current_app.static_folder, "security", "threat-map")
    return send_from_directory(root, name)


@bp.get("/security")
@owner_required
def security_home():
    stats = summary_stats()
    return render_template(
        "security/dashboard.html",
        stats=stats,
        events=list_security_events(16),
        bans=list_bans(12),
        page_title="Security",
    )


@bp.get("/security/events")
@owner_required
def security_events():
    return render_template(
        "security/events.html",
        events=list_security_events(120),
        page_title="Attack events",
    )


@bp.get("/security/audit")
@owner_required
def security_audit():
    days = 14
    try:
        days = max(1, min(180, int(request.args.get("days") or 14)))
    except ValueError:
        days = 14
    rows = (
        AuditLog.query.filter(AuditLog.created_at >= utcnow() - timedelta(days=days))
        .order_by(AuditLog.id.desc())
        .limit(250)
        .all()
    )
    entries = []
    for row in rows:
        actor = db.session.get(User, row.actor_id) if row.actor_id else None
        before = loads(row.before_json) if row.before_json else {}
        after = loads(row.after_json) if row.after_json else {}
        reversible = (row.action or "") in {"delete", "restore", "update", "remove"} and bool(row.entity_id)
        entries.append(
            {
                "id": row.id,
                "when": row.created_at,
                "who": actor.label() if actor else "system",
                "source": row.source,
                "action": row.action,
                "entity": row.entity,
                "entity_id": row.entity_id,
                "before": before,
                "after": after,
                "reversible": reversible,
            }
        )
    return render_template("security/audit.html", entries=entries, days=days, page_title="Full audit")


@bp.post("/security/audit/<int:audit_id>/reverse")
@owner_required
def security_reverse(audit_id):
    from app.services.reversals import reverse_audit

    result = reverse_audit(current_user, audit_id)
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect("/security/audit")


@bp.get("/security/threat-map")
@owner_required
def threat_map():
    _ensure_geo()
    return render_template("security/threat_map.html", page_title="Threat map", alert_url="")


def _json_window_payload(kind: str):
    from app.services import threat_queries as tq

    _ensure_geo()
    window = _window()
    if kind == "summary":
        data = tq.summary_for_window(window)
    elif kind == "countries":
        data = tq.countries_for_window(window, fill=True)
    elif kind == "recent":
        limit = 24
        try:
            limit = max(8, min(int(request.args.get("limit") or 24), 48))
        except ValueError:
            limit = 24
        data = tq.recent_events(window, limit=limit)
    elif kind == "replay":
        try:
            limit = int(request.args.get("limit") or 1500)
        except ValueError:
            limit = 1500
        try:
            after_id = int(request.args.get("after_id") or 0)
        except ValueError:
            after_id = 0
        data = tq.replay_events(window, limit=limit, after_id=after_id)
    else:
        data = {}
    data["ok"] = True
    return jsonify(data)


@bp.get("/security/threat-map/summary")
@owner_required
def threat_map_summary():
    return _json_window_payload("summary")


@bp.get("/security/threat-map/countries")
@owner_required
def threat_map_countries():
    return _json_window_payload("countries")


@bp.get("/security/threat-map/recent")
@owner_required
def threat_map_recent():
    resp = _json_window_payload("recent")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.get("/security/threat-map/replay")
@owner_required
def threat_map_replay():
    return _json_window_payload("replay")


@bp.get("/security/threat-map/live")
@owner_required
def threat_map_live():
    from app.services import threat_queries as tq

    _ensure_geo()
    try:
        since_id = int(request.args.get("since_id") or 0)
    except ValueError:
        since_id = 0
    data = tq.live_events(since_id=since_id, limit=80, arm=request.args.get("arm") == "1")
    data["ok"] = True
    resp = jsonify(data)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.get("/security/threat-map/country/<iso2>")
@owner_required
def threat_map_country(iso2):
    from app.services import threat_queries as tq

    window = _window()
    if window == "lifetime":
        data = tq.country_history_totals(iso2)
    else:
        data = tq.country_detail(iso2, window)
    data["ok"] = True
    return jsonify(data)
