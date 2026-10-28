"""Pipe help notes onto the home page, and read them back."""
from __future__ import annotations

from flask import abort, flash, jsonify, redirect, request
from flask_login import current_user

from app.routes.common import bp, login_required
from app.services.help_notes import active_notes, can_post_help, items_from_payload, parse_pipe_text, pipe_notes


def _public(row) -> dict:
    return {
        "slug": row.slug,
        "title": row.title,
        "body": row.body,
        "sort": row.sort_order,
    }


def _incoming():
    if request.form.get("title") or request.form.get("body"):
        item = {
            "title": request.form.get("title") or "",
            "body": request.form.get("body") or "",
            "slug": request.form.get("slug") or "",
        }
        if "active" in request.form:
            item["active"] = request.form.get("active")
        replace = (request.form.get("replace") or "").strip().lower() in {"1", "true", "yes"}
        return [item], replace, ""
    raw = (request.get_data(as_text=True) or "").lstrip("\ufeff")
    stripped = raw.lstrip()
    if request.is_json or stripped[:1] in "{[":
        data = request.get_json(silent=True, force=True)
        if data is None:
            return [], False, "That was not a help note."
        items, replace = items_from_payload(data)
        return items, replace, ""
    if stripped.startswith("#") or "\n#" in stripped:
        replace = (request.args.get("replace") or "").strip().lower() in {"1", "true", "yes"}
        return parse_pipe_text(raw), replace, ""
    return [], False, ""


@bp.route("/api/help", methods=["GET", "POST"])
@login_required
def help_notes_api():
    if request.method == "GET":
        return jsonify({"ok": True, "notes": [_public(row) for row in active_notes()]})
    if not can_post_help(current_user):
        abort(403)
    items, replace, error = _incoming()
    if error:
        return jsonify({"ok": False, "error": error}), 400
    result = pipe_notes(current_user, items, replace=replace)
    if request.form:
        flash(result.get("reply") or result.get("error") or "", "ok" if result.get("ok") else "warn")
        nxt = (request.form.get("next") or "/").strip()
        if nxt not in {"/", "/help"}:
            nxt = "/"
        return redirect(nxt)
    status = 200 if result.get("ok") else 400
    return jsonify(result), status
