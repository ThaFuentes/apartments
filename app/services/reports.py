"""Weekly and company reports. A boss reads them in the app. Email is optional."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import timedelta

from app.builddb.builddb import db
from app.models import (
    AssistantProfile,
    Expense,
    Job,
    MileageLeg,
    Property,
    Report,
    PlanItem,
    Trip,
    TripProperty,
    Unit,
    UnitVisit,
    User,
)
from app.services.clock import local_today, money, utcnow, week_bounds

KINDS = ("weekly", "company", "property", "adhoc")


def _plan_lines(start, end) -> list[dict]:
    from app.services.plan import status_line

    rows = []
    items = PlanItem.query.filter(PlanItem.deleted_at.is_(None)).all()
    for item in items:
        trip = item.trip
        if not trip or not trip.starts_on or not (start <= trip.starts_on <= end):
            continue
        rows.append(
            {
                "property": item.property.name if item.property else "",
                "unit": item.unit_number or "",
                "title": item.title,
                "planned_qty": item.planned_qty,
                "done_qty": item.done_qty,
                "status": item.status,
                "result": status_line(item),
                "note": item.outcome_note or "",
            }
        )
    return rows


def owner_profile() -> AssistantProfile | None:
    owner = User.query.filter_by(role="owner").order_by(User.id.asc()).first()
    if not owner:
        return None
    return AssistantProfile.query.filter_by(user_id=owner.id).first()


def period_for(kind: str, starts_on=None, tz_name: str | None = None):
    today = starts_on or local_today(tz_name)
    start, end = week_bounds(today)
    return start, end


def _in_range(moment, start, end) -> bool:
    if moment is None:
        return False
    day = moment.date() if hasattr(moment, "date") else moment
    return start <= day <= end


def build_snapshot(
    *,
    kind: str = "weekly",
    starts_on=None,
    property_id: int | None = None,
    author: str = "",
) -> dict:
    profile = owner_profile()
    tz_name = profile.timezone if profile else None
    start, end = period_for(kind, starts_on, tz_name)
    prior_start = start - timedelta(days=7)
    prior_end = end - timedelta(days=7)
    company = (profile.company_name if profile else "") or "Company"
    kind = kind if kind in KINDS else "weekly"

    trips = Trip.query.filter(Trip.deleted_at.is_(None), Trip.starts_on.isnot(None)).all()
    trip_ids = {t.id for t in trips if t.starts_on and start <= t.starts_on <= end}
    prior_trip_ids = {t.id for t in trips if t.starts_on and prior_start <= t.starts_on <= prior_end}

    jobs = Job.query.filter(Job.deleted_at.is_(None)).all()
    expenses = Expense.query.filter(Expense.deleted_at.is_(None), Expense.status == "confirmed").all()
    visits = UnitVisit.query.all()

    def job_in(job: Job, ids, range_start, range_end) -> bool:
        if property_id and job.property_id != property_id:
            return False
        if job.trip_id and job.trip_id in ids:
            return True
        return _in_range(job.created_at, range_start, range_end) and (
            not ids or job.trip_id in ids or job.trip_id is None
        )

    # A job counts this week when its trip is this week or it was logged this week.
    def belongs(job: Job, range_start, range_end, ids) -> bool:
        if property_id and job.property_id != property_id:
            return False
        if job.trip_id and job.trip_id in ids:
            return True
        if job.trip_id and job.trip_id not in ids:
            return False
        return _in_range(job.created_at, range_start, range_end)

    week_jobs = [j for j in jobs if belongs(j, start, end, trip_ids)]
    prior_jobs = [j for j in jobs if belongs(j, prior_start, prior_end, prior_trip_ids)]

    def exp_in(row: Expense, range_start, range_end, ids) -> bool:
        if property_id and row.property_id and row.property_id != property_id:
            return False
        if row.trip_id and row.trip_id in ids:
            return True
        if row.trip_id and row.trip_id not in ids:
            return False
        return _in_range(row.confirmed_at or row.created_at, range_start, range_end)

    week_exp = [e for e in expenses if exp_in(e, start, end, trip_ids)]
    prior_exp = [e for e in expenses if exp_in(e, prior_start, prior_end, prior_trip_ids)]

    prop_ids = {j.property_id for j in week_jobs}
    prop_ids.update(
        v.property_id for v in visits if _in_range(v.started_at, start, end) and (not property_id or v.property_id == property_id)
    )
    for link in TripProperty.query.filter(TripProperty.trip_id.in_(trip_ids or {0})).all():
        if not property_id or link.property_id == property_id:
            prop_ids.add(link.property_id)

    properties = []
    cities = []
    for prop in Property.query.filter(Property.id.in_(prop_ids or {0}), Property.deleted_at.is_(None)).all():
        city_name = prop.city.name if prop.city else ""
        if city_name and city_name not in cities:
            cities.append(city_name)
        pjobs = [j for j in week_jobs if j.property_id == prop.id]
        unit_ids = {j.unit_id for j in pjobs if j.unit_id}
        unit_ids.update(
            v.unit_id
            for v in visits
            if v.property_id == prop.id and _in_range(v.started_at, start, end)
        )
        numbers = []
        for unit in Unit.query.filter(Unit.id.in_(unit_ids or {0}), Unit.deleted_at.is_(None)).all():
            if unit.unit_number not in numbers:
                numbers.append(unit.unit_number)
        job_rows = []
        for job in pjobs:
            number = ""
            if job.unit_id:
                unit = db.session.get(Unit, job.unit_id)
                number = unit.unit_number if unit else ""
            from app.models import Equipment
            from app.services.equipment import describe as describe_equipment

            plates = (
                Equipment.query.filter_by(job_id=job.id)
                .filter(Equipment.deleted_at.is_(None))
                .all()
            )
            plate_bits = []
            for plate in plates:
                label = describe_equipment(
                    {
                        "kind": plate.kind,
                        "brand": plate.brand,
                        "style": plate.style,
                        "color": plate.color,
                        "model": plate.model_number,
                        "serial": plate.serial_number,
                        "size": plate.size_label,
                    }
                )
                note = (plate.notes or "").strip()
                if note and note != label and label not in note:
                    bit = f"{label}. Note: {note}".strip(". ") if label else note
                else:
                    bit = note or label
                if bit and bit not in plate_bits:
                    plate_bits.append(bit)
            notes = job.detail or ""
            for bit in plate_bits:
                if bit not in notes:
                    notes = (notes + " " + bit).strip()
            job_rows.append(
                {
                    "id": job.id,
                    "unit": number,
                    "title": job.title,
                    "status": job.status,
                    "notes": notes,
                }
            )
        properties.append(
            {
                "id": prop.id,
                "name": prop.name,
                "city": city_name,
                "address": prop.address or "",
                "lat": prop.lat,
                "lng": prop.lng,
                "units": numbers,
                "jobs": job_rows,
                "went": len(pjobs),
                "completed": sum(1 for j in pjobs if j.status == "done"),
                "blocked": sum(1 for j in pjobs if j.status == "blocked"),
                "followup": sum(1 for j in pjobs if j.status == "followup"),
            }
        )

    def spend(rows, kind_name):
        return sum(int(e.amount_cents or 0) for e in rows if e.kind == kind_name)

    lines = []
    for exp in week_exp:
        lines.append(
            {
                "id": exp.id,
                "kind": exp.kind,
                "amount_cents": int(exp.amount_cents or 0),
                "merchant": exp.merchant or "",
                "odometer": exp.odometer,
                "note": exp.note or "",
                "when": (exp.confirmed_at or exp.created_at).date().isoformat()
                if (exp.confirmed_at or exp.created_at)
                else "",
            }
        )

    legs = []
    miles_est = 0.0
    miles_actual = 0.0
    readings = []
    for trip in trips:
        if trip.id not in trip_ids:
            continue
        if trip.miles_estimate:
            miles_est += float(trip.miles_estimate)
        if trip.miles_actual:
            miles_actual += float(trip.miles_actual)
        if trip.odometer_start is not None or trip.odometer_end is not None:
            gap = None
            if trip.odometer_start is not None and trip.odometer_end is not None:
                gap = int(trip.odometer_end) - int(trip.odometer_start)
            readings.append(
                {
                    "title": trip.title,
                    "start": trip.odometer_start,
                    "end": trip.odometer_end,
                    "miles": gap,
                }
            )
        for leg in MileageLeg.query.filter_by(trip_id=trip.id).all():
            legs.append(
                {
                    "origin": leg.origin,
                    "destination": leg.destination,
                    "miles": leg.miles,
                }
            )
    prior_miles = 0.0
    for trip in trips:
        if trip.id in prior_trip_ids and trip.miles_estimate:
            prior_miles += float(trip.miles_estimate)

    handoffs = []
    for trip in trips:
        if trip.id in trip_ids and (trip.handoff or "").strip():
            for line in trip.handoff.splitlines():
                bit = line.strip().lstrip("-").strip()
                if bit:
                    handoffs.append(bit)

    followups = []
    for job in jobs:
        if job.status not in ("followup", "blocked") or job.deleted_at:
            continue
        if property_id and job.property_id != property_id:
            continue
        unit = db.session.get(Unit, job.unit_id) if job.unit_id else None
        prop = db.session.get(Property, job.property_id)
        followups.append(
            {
                "job_id": job.id,
                "title": job.title,
                "status": job.status,
                "unit": unit.unit_number if unit else "",
                "property": prop.name if prop else "",
                "city": prop.city.name if prop and prop.city else "",
            }
        )

    from app.services.miles import traveled_for_report

    driven = traveled_for_report(start, end)
    snapshot_miles_traveled = driven["total"]
    snapshot_miles_log = driven["lines"]
    title_city = cities[0] if len(cities) == 1 else ", ".join(cities[:4])
    if kind == "company":
        title = f"Company report · {start.isoformat()} to {end.isoformat()}"
    elif kind == "property":
        title = f"Property report · {start.isoformat()} to {end.isoformat()}"
    elif kind == "adhoc":
        title = f"Field report · {start.isoformat()} to {end.isoformat()}"
    else:
        title = f"Weekly report · {start.isoformat()} to {end.isoformat()}"
    if title_city:
        title = f"{title} · {title_city}"

    snapshot = {
        "kind": kind,
        "title": title,
        "company": company,
        "author": author,
        "voice": (profile.report_voice if profile else "") or "plain, for a company reader",
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "cities": cities,
        "properties": properties,
        "miles": {
            "estimate": round(miles_est, 1),
            "actual": round(miles_actual, 1) if miles_actual else None,
            "legs": legs,
            "traveled": snapshot_miles_traveled,
            "log": snapshot_miles_log,
            "readings": readings,
        },
        "expenses": {
            "gas": spend(week_exp, "gas"),
            "food": spend(week_exp, "food"),
            "other": spend(week_exp, "other"),
            "lines": lines,
        },
        "plan": _plan_lines(start, end),
        "handoffs": handoffs[:12],
        "followups": followups,
        "prior": {
            "completed": sum(1 for j in prior_jobs if j.status == "done"),
            "spend_cents": sum(int(e.amount_cents or 0) for e in prior_exp),
            "miles": round(prior_miles, 1),
        },
        "totals": {
            "jobs_went": len(week_jobs),
            "jobs_done": sum(1 for j in week_jobs if j.status == "done"),
            "jobs_blocked": sum(1 for j in week_jobs if j.status == "blocked"),
            "units": len({u for p in properties for u in p["units"]}),
            "spend_cents": sum(int(e.amount_cents or 0) for e in week_exp),
        },
    }
    return snapshot


def chat_excerpt(markdown: str, limit: int = 1800) -> str:
    """Plain report text for the chat thread."""
    import re

    text = re.sub(r"^#{1,6}\s*", "", markdown or "", flags=re.M).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit("\n", 1)[0].rstrip()
    return (cut or text[:limit].rstrip()) + "\n…"


def render_markdown(snapshot: dict) -> str:
    period = snapshot.get("period") or {}
    totals = snapshot.get("totals") or {}
    expenses = snapshot.get("expenses") or {}
    miles = snapshot.get("miles") or {}
    prior = snapshot.get("prior") or {}
    lines = [
        f"# {snapshot.get('title') or 'Report'}",
        "",
        f"Prepared by {snapshot.get('author') or 'the field manager'}.",
        f"Prepared for {snapshot.get('company') or 'the company'}.",
        f"Period: {period.get('start')} through {period.get('end')}.",
    ]
    if snapshot.get("kind") == "company":
        lines.append("This packet is the company review copy: jobs, units, miles, and money.")
    lines += [
        "",
        "## Went for and completed",
        "",
        f"- Jobs on the week: {totals.get('jobs_went', 0)}",
        f"- Completed: {totals.get('jobs_done', 0)}",
        f"- Blocked: {totals.get('jobs_blocked', 0)}",
        f"- Units visited: {totals.get('units', 0)}",
        "",
        "## Properties and units",
        "",
    ]
    if not snapshot.get("properties"):
        lines.append("No property visits in this period.")
    for prop in snapshot.get("properties") or []:
        pin = ""
        if prop.get("lat") is not None and prop.get("lng") is not None:
            pin = f" Pin {prop['lat']:.5f}, {prop['lng']:.5f}."
        lines.append(
            f"### {prop.get('name')} · {prop.get('city')}"
        )
        lines.append(f"{prop.get('address') or 'Address not on file.'}{pin}")
        units = ", ".join(prop.get("units") or []) or "No unit numbers logged."
        lines.append(f"Units: {units}")
        lines.append(
            f"Went {prop.get('went', 0)}, completed {prop.get('completed', 0)}, "
            f"blocked {prop.get('blocked', 0)}, follow-up {prop.get('followup', 0)}."
        )
        for job in prop.get("jobs") or []:
            unit = f"{job.get('unit')} — " if job.get("unit") else ""
            note = f" — {job['notes']}" if job.get("notes") else ""
            lines.append(f"- {unit}{job.get('title')} ({job.get('status')}){note}")
        lines.append("")
    lines += ["## Plan and what happened", ""]
    if snapshot.get("plan"):
        for row in snapshot["plan"]:
            unit = f"unit {row.get('unit')}: " if row.get("unit") else ""
            lines.append(
                f"- {row.get('property')}: {unit}planned {row.get('planned_qty')} {row.get('title')}. {row.get('result')}"
            )
    else:
        lines.append("No plan lines in this period.")
    lines += ["", "## Miles", ""]
    lines.append(f"Estimate {miles.get('estimate') or 0} miles.")
    if miles.get("actual"):
        lines.append(f"Actual {miles.get('actual')} miles.")
    for row in miles.get("readings") or []:
        lines.append(
            f"- {row.get('title')}: starting {row.get('start') if row.get('start') is not None else '—'}, ending {row.get('end') if row.get('end') is not None else '—'}"
            + (f" ({row.get('miles')} miles)" if row.get("miles") is not None and row.get("miles") >= 0 else "")
        )
    lines.append(f"Miles driven {miles.get('traveled') or 0}.")
    for row in miles.get("log") or []:
        lines.append(f"- {row.get('miles')} mi · {row.get('note') or row.get('source')}")
    for leg in miles.get("legs") or []:
        lines.append(f"- {leg.get('origin')} → {leg.get('destination')}: {leg.get('miles')} mi")
    lines += [
        "",
        "## Expenses",
        "",
        f"- Gas: {money(expenses.get('gas'))}",
        f"- Food: {money(expenses.get('food'))}",
        f"- Other: {money(expenses.get('other'))}",
        f"- Total: {money(totals.get('spend_cents'))}",
        "",
    ]
    if expenses.get("lines"):
        for row in expenses["lines"]:
            odo = f", odometer {row['odometer']}" if row.get("odometer") else ""
            who = f" · {row['merchant']}" if row.get("merchant") else ""
            lines.append(f"- {row.get('kind')} {money(row.get('amount_cents'))}{who}{odo}")
    else:
        lines.append("No confirmed expenses in this period.")
    lines += ["", "## What matters", ""]
    if snapshot.get("handoffs"):
        for bit in snapshot["handoffs"]:
            lines.append(f"- {bit}")
    else:
        lines.append("No end-of-day handoff yet.")
    lines += ["", "## Follow-up", ""]
    if snapshot.get("followups"):
        for row in snapshot["followups"]:
            place = " ".join(x for x in (row.get("property"), row.get("city")) if x)
            unit = f"{row['unit']} — " if row.get("unit") else ""
            lines.append(f"- {place}: {unit}{row.get('title')} ({row.get('status')})")
    else:
        lines.append("Nothing waiting on a return visit.")
    lines += [
        "",
        "## Compared with last week",
        "",
        f"- Completed jobs: {totals.get('jobs_done', 0)} this week, {prior.get('completed', 0)} last week.",
        f"- Spend: {money(totals.get('spend_cents'))} this week, {money(prior.get('spend_cents'))} last week.",
        f"- Estimated miles: {miles.get('estimate') or 0} this week, {prior.get('miles') or 0} last week.",
        "",
    ]
    return "\n".join(lines).strip() + "\n"


def sign_report(report_id: int, ttl: int = 900) -> str:
    exp = int(utcnow().timestamp()) + int(ttl)
    secret = (os.getenv("SECRET_KEY") or "apt-dev").encode("utf-8")
    msg = f"{int(report_id)}.{exp}".encode("utf-8")
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    return f"/reports/{int(report_id)}/pdf?exp={exp}&sig={sig}"


def signature_ok(report_id: int, exp, sig) -> bool:
    try:
        exp_i = int(exp)
        report_id = int(report_id)
    except (TypeError, ValueError):
        return False
    if exp_i < int(utcnow().timestamp()):
        return False
    if not sig or len(str(sig)) < 32:
        return False
    secret = (os.getenv("SECRET_KEY") or "apt-dev").encode("utf-8")
    msg = f"{report_id}.{exp_i}".encode("utf-8")
    expected = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, str(sig))


def load_snapshot(report: Report) -> dict:
    try:
        data = json.loads(report.snapshot_json or "{}")
    except json.JSONDecodeError:
        data = {}
    if not data:
        data = {"title": report.title, "markdown": report.body_md}
    return data


def render_pdf(snapshot: dict) -> bytes:
    from io import BytesIO

    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    font = "Helvetica"
    for path, name in (
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVu"),
        ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "Liberation"),
    ):
        if os.path.isfile(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                font = name
                break
            except Exception:
                pass
    styles = ParagraphStyle(
        "body",
        fontName=font,
        fontSize=10,
        leading=14,
        textColor="#221c16",
    )
    title_style = ParagraphStyle(
        "title",
        parent=styles,
        fontName=font,
        fontSize=16,
        leading=20,
        spaceAfter=8,
    )
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
        title=snapshot.get("title") or "Report",
    )
    story = []
    md = render_markdown(snapshot)
    for block in md.split("\n"):
        text = (
            block.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        if text.startswith("# "):
            story.append(Paragraph(text[2:], title_style))
        elif text.startswith("## "):
            story.append(Spacer(1, 8))
            story.append(Paragraph(f"<b>{text[3:]}</b>", styles))
        elif text.startswith("### "):
            story.append(Paragraph(f"<b>{text[4:]}</b>", styles))
        elif text.strip():
            story.append(Paragraph(text, styles))
        else:
            story.append(Spacer(1, 6))
    doc.build(story)
    return buf.getvalue()
