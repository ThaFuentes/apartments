"""Expense and report routes."""
from __future__ import annotations

import json

from flask import abort, flash, redirect, render_template, request
from flask_login import current_user

from app.builddb.builddb import db
from app.models import Expense, Report, User
from app.services.clock import money
from app.services.records import audit
from app.services.reports import build_snapshot, load_snapshot, render_pdf, signature_ok
from app.services.files import send_bytes
from app.services.pending import commit_apply
from app.routes.common import bp, login_required, _reports_ok, _key, _new_key


@bp.get("/expenses")
@login_required
def expenses():
    if current_user.role == "viewer":
        abort(403)
    rows = Expense.query.filter(Expense.deleted_at.is_(None)).order_by(Expense.id.desc()).limit(80).all()
    return render_template("expenses.html", expenses=rows, money=money, msg_key=_new_key())

@bp.post("/expenses")
@login_required
def expense_save():
    if current_user.role == "viewer":
        abort(403)
    if request.headers.get("X-Apt-Offline-Queue") == "1":
        return jsonify({"ok": False, "error": "Confirm the expense when you are online."}), 409
    from app.services.pending import commit_apply

    try:
        cents = int(round(float(request.form.get("amount") or "0") * 100))
    except ValueError:
        cents = 0
    payload = {
        "kind": request.form.get("kind") or "other",
        "amount_cents": cents,
        "merchant": request.form.get("merchant") or "",
        "note": request.form.get("note") or "",
        "fields_confirmed": True,
        "confidence": 1,
    }
    if request.form.get("odometer"):
        payload["odometer"] = int(request.form.get("odometer"))
        payload["gas_stop"] = payload["kind"] == "gas"
    result = commit_apply(current_user, "log_expense", payload, "human", _key() or _new_key())
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect("/expenses")

@bp.get("/reports")
@login_required
def reports():
    if not _reports_ok():
        abort(403)
    saved = Report.query.filter(Report.deleted_at.is_(None), Report.status.in_(("ready", "sent"))).order_by(Report.id.desc()).all()
    if current_user.role == "viewer":
        preview = None
    else:
        preview = build_snapshot(kind="weekly", author=current_user.label())
    return render_template("reports.html", saved=saved, preview=preview, money=money, msg_key=_new_key())

@bp.post("/reports/build")
@login_required
def reports_build():
    if current_user.role == "viewer":
        abort(403)
    from app.services.pending import commit_apply

    kind = request.form.get("kind") or "weekly"
    result = commit_apply(
        current_user,
        "draft_report",
        {"kind": kind, "force_new": request.form.get("force_new") == "1"},
        "human",
        _key() or f"report-{kind}-{_new_key()}",
    )
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    if result.get("report_id"):
        return redirect(f"/reports/{result['report_id']}")
    return redirect("/reports")

@bp.get("/reports/<int:report_id>")
@login_required
def report_detail(report_id):
    if not _reports_ok():
        abort(403)
    report = db.session.get(Report, report_id)
    if not report or report.deleted_at:
        abort(404)
    viewers = User.query.filter_by(role="viewer", active=True, can_see_reports=True).order_by(User.username.asc()).all()
    return render_template(
        "report.html",
        report=report,
        snapshot=load_snapshot(report),
        money=money,
        viewers=viewers,
        msg_key=_new_key(),
    )

@bp.post("/reports/<int:report_id>")
@login_required
def report_edit(report_id):
    if current_user.role == "viewer":
        abort(403)
    report = db.session.get(Report, report_id)
    if not report or report.status == "sent":
        flash("A sent report stays as the copy your bosses already have.", "warn")
        return redirect(f"/reports/{report_id}")
    body = request.form.get("body_md") or ""
    before = {"body": report.body_md[:80]}
    report.body_md = body
    snap = load_snapshot(report)
    snap["edited"] = True
    report.snapshot_json = json.dumps(snap)
    audit(current_user.id, "human", "update", "report", report.id, before, {"body": body[:80]})
    db.session.commit()
    flash("Report updated.", "ok")
    return redirect(f"/reports/{report_id}")

@bp.get("/reports/<int:report_id>/pdf")
def report_pdf(report_id):
    report = db.session.get(Report, report_id)
    if not report or report.deleted_at:
        abort(404)
    signed = signature_ok(report_id, request.args.get("exp"), request.args.get("sig"))
    if not signed:
        if not getattr(current_user, "is_authenticated", False) or not _reports_ok():
            abort(403)
    data = render_pdf(load_snapshot(report))
    name = f"apt-report-{report.id}.pdf"
    return send_bytes(data, "application/pdf", name, as_attachment=request.args.get("dl") == "1")

@bp.post("/reports/<int:report_id>/send")
@login_required
def report_send(report_id):
    if current_user.role != "owner":
        abort(403)
    from app.services.pending import commit_apply

    result = commit_apply(
        current_user,
        "send_report",
        {"report_id": report_id, "also": request.form.get("also") or ""},
        "human",
        _key() or f"send-{report_id}-{_new_key()}",
    )
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(f"/reports/{report_id}")
