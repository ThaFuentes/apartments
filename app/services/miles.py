"""Miles she traveled: gaps between odometer readings, plus miles she states."""
from __future__ import annotations

from app.builddb.builddb import db
from app.models import MilesEntry, OdometerReading, Trip
from app.services.clock import utcnow
from app.services.records import audit


def _entries(user_id: int):
    return MilesEntry.query.filter_by(user_id=user_id).order_by(MilesEntry.recorded_at.asc(), MilesEntry.id.asc())


def record_odometer(user, reading: int, *, note: str = "", trip_id: int | None = None, source: str = "human") -> dict:
    reading = int(reading)
    previous = (
        OdometerReading.query.filter_by(user_id=user.id)
        .order_by(OdometerReading.recorded_at.desc(), OdometerReading.id.desc())
        .first()
    )
    if previous and reading < previous.reading:
        return {
            "ok": False,
            "needs_answer": True,
            "reply": f"That odometer ({reading}) is lower than the last one ({previous.reading}). Say the reading again if the last one was wrong.",
        }
    row = OdometerReading(
        user_id=user.id,
        reading=reading,
        trip_id=trip_id,
        note=(note or "")[:200],
        recorded_at=utcnow(),
    )
    db.session.add(row)
    db.session.flush()
    miles = 0.0
    if previous and reading > previous.reading:
        miles = float(reading - previous.reading)
        db.session.add(
            MilesEntry(
                user_id=user.id,
                trip_id=trip_id,
                miles=miles,
                source="odometer",
                origin=str(previous.reading),
                destination=str(reading),
                note=f"Odometer {previous.reading} to {reading}",
                recorded_at=utcnow(),
            )
        )
    audit(
        user.id,
        source,
        "create",
        "odometer",
        row.id,
        {},
        {"reading": reading, "miles": miles},
    )
    if previous is None:
        reply = f"Odometer {reading} is the starting reading. The next one counts the miles."
    elif miles:
        reply = f"Odometer {reading}. That's {miles:.0f} miles since {previous.reading}."
    else:
        reply = f"Odometer {reading} matches the last reading. No new miles."
    return {"ok": True, "reply": reply, "miles": miles, "reading": reading}


def add_stated_miles(user, miles: float, *, note: str = "", trip_id: int | None = None, source: str = "human") -> dict:
    miles = float(miles)
    if miles <= 0:
        return {"ok": False, "reply": "Tell me how many miles, like: drove 86 miles."}
    if miles > 2000:
        return {"ok": False, "needs_answer": True, "reply": f"{miles:.0f} miles in one entry is a lot. Say it again if that's right."}
    row = MilesEntry(
        user_id=user.id,
        trip_id=trip_id,
        miles=miles,
        source="stated",
        note=(note or "Miles she logged")[:300],
        recorded_at=utcnow(),
    )
    db.session.add(row)
    db.session.flush()
    audit(user.id, source, "create", "miles", row.id, {}, {"miles": miles, "note": row.note})
    total = traveled_total(user.id)
    return {"ok": True, "reply": f"Logged {miles:.1f} miles. Traveled total is {total:.1f}.", "miles": miles, "total": total}


def set_trip_actual(user, trip: Trip, miles: float, source: str = "human") -> None:
    """Keep one traveled line per trip for the actual miles she typed on that trip."""
    miles = float(miles)
    row = MilesEntry.query.filter_by(user_id=user.id, trip_id=trip.id, source="trip").first()
    before = row.miles if row else None
    if row:
        row.miles = miles
        row.note = f"Actual miles on {trip.title}"[:300]
        row.recorded_at = utcnow()
    else:
        row = MilesEntry(
            user_id=user.id,
            trip_id=trip.id,
            miles=miles,
            source="trip",
            note=f"Actual miles on {trip.title}"[:300],
            recorded_at=utcnow(),
        )
        db.session.add(row)
        db.session.flush()
    audit(user.id, source, "update", "miles", row.id, {"miles": before}, {"miles": miles, "trip_id": trip.id})


def traveled_rows(user_id: int):
    rows = _entries(user_id).all()
    odo_days = set()
    for row in rows:
        if row.source == "odometer" and row.recorded_at:
            odo_days.add(row.recorded_at.date())
    counted = []
    for row in rows:
        if row.source == "trip" and row.recorded_at and row.recorded_at.date() in odo_days:
            continue
        counted.append(row)
    return rows, counted


def traveled_total(user_id: int) -> float:
    _all, counted = traveled_rows(user_id)
    return round(sum(float(row.miles or 0) for row in counted), 1)


def traveled_for_report(start, end) -> dict:
    rows = MilesEntry.query.order_by(MilesEntry.recorded_at.asc()).all()
    in_period = []
    for row in rows:
        day = row.recorded_at.date() if row.recorded_at else None
        if day and start <= day <= end:
            in_period.append(row)
    odo_days = {row.recorded_at.date() for row in in_period if row.source == "odometer" and row.recorded_at}
    counted = [
        row
        for row in in_period
        if not (row.source == "trip" and row.recorded_at and row.recorded_at.date() in odo_days)
    ]
    return {
        "total": round(sum(float(row.miles or 0) for row in counted), 1),
        "lines": [
            {
                "miles": row.miles,
                "source": row.source,
                "note": row.note,
                "origin": row.origin,
                "destination": row.destination,
            }
            for row in counted
        ],
    }
