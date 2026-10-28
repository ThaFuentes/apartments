"""Every desk route has a gate, and every login is checked against it.

A new route fails this file until it is named in GATES. The walk then signs
in as each company role and opens that route.
"""
from __future__ import annotations

import json
from datetime import date

from tests.apt_test_support import APP, AptTestBase, db
from app.models import (
    Expense,
    Job,
    PropertyAccess,
    Region,
    RegionAccess,
    RegionCity,
    Report,
    Trip,
    TripProperty,
    Unit,
    User,
)
from app.services.access.management import create_custom_role
from app.services.clock import utcnow
from app.services.people import create_user
from app.services.records import ensure_property

PASSWORD = "field-pass-9"
AGENT = "Mozilla/5.0 AptTest"


def _drop_cached_user():
    """Flask-Login stores the user on the app context. Tests keep that context
    open, so a second client would still be the first person who signed in.
    """
    from flask import g

    g.pop("_login_user", None)


def _company_snapshot() -> dict:
    """Shape report.html and the PDF already read. A one-key stub 500s the page."""
    return {
        "kind": "weekly",
        "title": "Company week",
        "company": "Apartments",
        "author": "Alex",
        "audience": "company",
        "subject": "",
        "audience_line": "",
        "person_id": None,
        "property_ids": None,
        "people": [],
        "period": {"start": "2026-10-05", "end": "2026-10-11"},
        "cities": [],
        "properties": [],
        "miles": {"estimate": 0, "actual": None, "traveled": 0, "log": [], "readings": []},
        "expenses": {"gas": 0, "food": 0, "other": 0, "lines": []},
        "plan": [],
        "handoffs": [],
        "followups": [],
        "prior": {"completed": 0, "spend_cents": 0, "miles": 0},
        "totals": {
            "jobs_went": 0,
            "jobs_done": 0,
            "jobs_blocked": 0,
            "units": 0,
            "spend_cents": 0,
        },
    }

# Who may open the people desk, the region map, and the security console.
PEOPLE = {
    "owner",
    "admin",
    "regional_manager",
    "regional_property_manager",
    "maintenance_regional",
    "property_manager",
    "maintenance_supervisor",
    "maintenance_manager",
}
REGION_ADMIN = {"owner", "admin"}
READ_ONLY = {"viewer", "boss"}
# These logins see both properties. Everyone else is fenced to Cedar Ridge.
COMPANY = {"owner", "admin", "office", "leasing", "viewer", "boss"}


def _rows(*rules):
    return rules


GATES = {}


def _gate(name, rules):
    for rule in rules:
        GATES[rule] = name


_gate("public_open", _rows("GET /healthz", "GET /robots.txt", "GET /sw.js", "GET /manifest.webmanifest", "GET /forgot"))
_gate("login_page", _rows("GET /login"))
_gate("public_redirect", _rows("GET /join/<token>", "GET /reset/<token>", "GET /2fa"))
_gate("home", _rows("GET /"))
_gate("session", _rows("GET /more", "GET /help", "GET /map", "GET /api/help"))
_gate(
    "history",
    _rows(
        "GET /ready",
        "GET /pm",
        "GET /inventory",
        "GET /contractors",
        "GET /places",
        "GET /trips",
        "GET /audit",
    ),
)
_gate("plan", _rows("GET /plan"))
_gate("money", _rows("GET /miles", "GET /expenses"))
_gate("settings", _rows("GET /settings"))
_gate("people", _rows("GET /users"))
_gate("regions", _rows("GET /regions"))
_gate(
    "security",
    _rows(
        "GET /security",
        "GET /security/console.css",
        "GET /security/assets/<path:name>",
        "GET /security/events",
        "GET /security/audit",
        "GET /security/threat-map",
        "GET /security/threat-map/summary",
        "GET /security/threat-map/countries",
        "GET /security/threat-map/recent",
        "GET /security/threat-map/replay",
        "GET /security/threat-map/live",
        "GET /security/threat-map/country/<iso2>",
    ),
)
_gate("bot", _rows("GET /bot-setup"))
_gate("property", _rows("GET /properties/<int:property_id>", "GET /api/pack/<int:property_id>"))
_gate("city", _rows("GET /places/<int:city_id>"))
_gate("unit", _rows("GET /units/<int:unit_id>"))
_gate("trip", _rows("GET /trips/<int:trip_id>"))
_gate(
    "report",
    _rows("GET /reports/<int:report_id>", "GET /reports/<int:report_id>/pdf", "GET /reports/<int:report_id>/csv"),
)
_gate("reports", _rows("GET /reports"))
_gate("preview", _rows("GET /reports/preview.csv"))
_gate("search", _rows("GET /api/places"))
_gate("media", _rows("GET /media/<int:media_id>"))
_gate("mapfile", _rows("GET /map/file/<int:property_id>"))
_gate(
    "post",
    _rows(
        "POST /login",
        "POST /logout",
        "POST /appearance",
        "POST /account/reset-email",
        "POST /join/<token>",
        "POST /2fa",
        "POST /bot-setup",
        "POST /api/bot/key",
        "POST /api/help",
        "POST /forgot",
        "POST /reset/<token>",
        "POST /sites",
        "POST /plan",
        "POST /log",
        "POST /chat",
        "POST /chat/new",
        "POST /review",
        "POST /pending/<int:pending_id>/confirm",
        "POST /pending/<int:pending_id>/discard",
        "POST /pending/<int:pending_id>/edit",
        "POST /property/default",
        "POST /property/confirm",
        "POST /miles",
        "POST /plan-items/<int:item_id>",
        "POST /trips/<int:trip_id>/miles",
        "POST /trips/<int:trip_id>/cards",
        "POST /properties/<int:property_id>",
        "POST /properties/<int:property_id>/delete",
        "POST /properties/<int:property_id>/units",
        "POST /units/<int:unit_id>",
        "POST /units/<int:unit_id>/delete",
        "POST /units/<int:unit_id>/equipment",
        "POST /units/<int:unit_id>/occupancy",
        "POST /units/<int:unit_id>/tasks",
        "POST /tasks/<int:task_id>/edit",
        "POST /tasks/<int:task_id>/done",
        "POST /tasks/<int:task_id>/send-back",
        "POST /tasks/<int:task_id>/delete",
        "POST /equipment/<int:gear_id>",
        "POST /records/<entity>/<int:record_id>/restore",
        "POST /equipment/<int:gear_id>/delete",
        "POST /drive",
        "POST /jobs/<int:job_id>/edit",
        "POST /units/<int:unit_id>/restore",
        "POST /jobs/<int:job_id>/delete",
        "POST /jobs/<int:job_id>/restore",
        "POST /settings",
        "POST /settings/assistant",
        "POST /settings/mail-test",
        "POST /settings/key",
        "POST /settings/key/<int:key_id>",
        "POST /settings/key/<int:key_id>/prefer",
        "POST /settings/key/<int:key_id>/delete",
        "POST /users",
        "POST /users/<int:user_id>/access",
        "POST /users/<int:user_id>/capabilities",
        "POST /users/<int:user_id>/default-policy/<int:region_id>",
        "POST /properties/<int:property_id>/pin",
        "POST /users/<int:user_id>",
        "POST /users/<int:user_id>/bot",
        "POST /users/<int:user_id>/security-watch",
        "POST /users/<int:user_id>/hat",
        "POST /users/<int:user_id>/hat/<int:hat_id>/delete",
        "POST /users/<int:user_id>/unlock",
        "POST /users/<int:user_id>/reset",
        "POST /roles",
        "POST /expenses",
        "POST /reports/build",
        "POST /reports/<int:report_id>",
        "POST /reports/<int:report_id>/send",
        "POST /media",
        "POST /units/<int:unit_id>/photo",
        "POST /map",
        "POST /map/remove",
        "POST /audit/<int:audit_id>/reverse",
        "POST /audit/restore-inventory",
        "POST /regions",
        "POST /regions/<int:region_id>",
        "POST /regions/<int:region_id>/cities",
        "POST /regions/<int:region_id>/people",
        "POST /regions/<int:region_id>/default-policies",
        "POST /regions/<int:region_id>/delete",
        "POST /security/audit/<int:audit_id>/reverse",
        "POST /security/ban",
        "POST /security/unban",
        "POST /security/device-ban",
        "POST /security/device-unban",
        "POST /ready/start",
        "POST /units/<int:unit_id>/ready",
        "POST /equipment/<int:equipment_id>/pm",
        "POST /pm/<int:pm_id>/done",
        "POST /jobs/<int:job_id>/parts",
        "POST /units/<int:unit_id>/rentable",
        "POST /units/<int:unit_id>/ready-by",
        "POST /units/<int:unit_id>/move-out",
        "POST /units/<int:unit_id>/ready-check",
        "POST /units/<int:unit_id>/hold",
        "POST /units/<int:unit_id>/come-back",
        "POST /units/<int:unit_id>/call",
        "POST /contractors",
        "POST /contractors/<int:contractor_id>",
        "POST /contractors/<int:contractor_id>/call",
        "POST /contractors/<int:contractor_id>/check-in",
        "POST /contractors/<int:contractor_id>/check-out",
        "POST /contractors/<int:contractor_id>/delete",
        "POST /equipment/<int:equipment_id>/how-to",
        "POST /pm/gear",
    ),
)


def _want(role, gate):
    """Status this login should get for a gate. Anonymous is role 'anon'."""
    if gate == "public_open":
        return 200
    if gate == "login_page":
        return 200 if role == "anon" else 302
    if gate in {"public_redirect", "bot"}:
        return 302
    if role == "anon":
        return 302
    if gate == "home":
        return 302 if role in READ_ONLY else 200
    if gate == "session" or gate == "history" or gate == "reports" or gate == "search":
        return 200
    if gate == "plan" or gate == "money" or gate == "settings":
        return 403 if role in READ_ONLY else 200
    if gate == "people":
        return 200 if role in PEOPLE else 403
    if gate == "regions":
        return 200 if role in REGION_ADMIN else 403
    if gate == "security":
        return 200 if role == "owner" else 403
    if gate in {"property", "city", "unit"}:
        return 404 if role == "loner" else 200
    if gate == "trip":
        return 200 if role in COMPANY else 404
    if gate == "report":
        return 200 if role in {"owner", "admin", "viewer", "boss"} else 404
    if gate == "preview":
        return 403 if role in READ_ONLY else 200
    if gate in {"media", "mapfile"}:
        return 404
    raise AssertionError(gate)


class RouteMatrixTests(AptTestBase):
    def setUp(self):
        super().setUp()
        self._orig_test_client = APP.test_client

        def test_client(*args, **kwargs):
            client = self._orig_test_client(*args, **kwargs)
            original = client.open

            def open_(*a, **k):
                _drop_cached_user()
                return original(*a, **k)

            client.open = open_
            return client

        APP.test_client = test_client

    def tearDown(self):
        APP.test_client = self._orig_test_client
        super().tearDown()

    def test_every_route_is_named_in_the_gate_table(self):
        missing = []
        for rule in APP.url_map.iter_rules():
            if rule.endpoint == "static":
                continue
            for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
                key = f"{method} {rule.rule}"
                if key not in GATES:
                    missing.append(key)
        self.assertEqual(missing, [])

    def _company(self):
        owner = self.owner()
        cedar = ensure_property("Cedar Ridge", "Midland", "Texas", owner.id)
        palm = ensure_property("Palm Grove", "Corpus Christi", "Texas", owner.id)
        db.session.flush()
        cedar_unit = Unit(property_id=cedar.id, unit_number="101", occupancy="make_ready", created_at=utcnow())
        palm_unit = Unit(property_id=palm.id, unit_number="9091", occupancy="make_ready", created_at=utcnow())
        db.session.add_all([cedar_unit, palm_unit])
        region = Region(name="Permian", created_by_id=owner.id, created_at=utcnow())
        db.session.add(region)
        db.session.flush()
        cedar.region_id = region.id
        db.session.add(RegionCity(region_id=region.id, city_id=cedar.city_id, created_at=utcnow()))
        seated = {
            "regional_manager": "rita",
            "regional_property_manager": "pat",
            "maintenance_regional": "moe",
            "property_manager": "pam",
            "assistant_manager": "amy",
            "office": "ivy",
            "maintenance_supervisor": "sid",
            "maintenance_manager": "meg",
            "maintenance_person": "ted",
            "viewer": "bossview",
        }
        people = {}
        for role, username in seated.items():
            person, _generated = create_user(
                username=username,
                password=PASSWORD,
                display_name=username,
                role=role,
                created_by=owner,
            )
            people[role] = person
        admin, _generated = create_user(
            username="ada", password=PASSWORD, display_name="Ada", role="admin", created_by=owner
        )
        people["admin"] = admin
        people["owner"] = owner
        for role in ("regional_manager", "regional_property_manager", "maintenance_regional"):
            db.session.add(RegionAccess(user_id=people[role].id, region_id=region.id, created_at=utcnow()))
        for role in ("property_manager", "assistant_manager", "maintenance_supervisor", "maintenance_manager", "maintenance_person"):
            db.session.add(
                PropertyAccess(
                    user_id=people[role].id,
                    property_id=cedar.id,
                    can_edit=True,
                    can_manage_people=role == "property_manager",
                    created_at=utcnow(),
                )
            )
        palm_tech, _generated = create_user(
            username="palmtech",
            password=PASSWORD,
            display_name="Palm Tech",
            role="maintenance_person",
            created_by=owner,
        )
        db.session.add(
            PropertyAccess(
                user_id=palm_tech.id,
                property_id=palm.id,
                can_edit=True,
                created_at=utcnow(),
            )
        )
        loner, _generated = create_user(
            username="loner", password=PASSWORD, display_name="Loner", role="maintenance_person", created_by=owner
        )
        people["loner"] = loner
        legacy_boss, _generated = create_user(
            username="legacyboss", password=PASSWORD, display_name="Legacy Boss", role="maintenance_person", created_by=owner
        )
        legacy_boss.role = "boss"
        people["boss"] = legacy_boss
        legacy_field, _generated = create_user(
            username="legacyfield", password=PASSWORD, display_name="Legacy Field", role="maintenance_person", created_by=owner
        )
        legacy_field.role = "field"
        people["field"] = legacy_field
        db.session.add(
            PropertyAccess(user_id=legacy_field.id, property_id=cedar.id, can_edit=True, created_at=utcnow())
        )
        create_custom_role(owner, "Leasing Agent", "office")
        leasing, _generated = create_user(
            username="lea", password=PASSWORD, display_name="Lea", role="leasing_agent", created_by=owner
        )
        people["leasing"] = leasing
        gone, _generated = create_user(
            username="gone",
            password=PASSWORD,
            display_name="Gone",
            role="maintenance_person",
            created_by=owner,
            active=False,
        )
        report = Report(
            kind="weekly",
            title="Company week",
            period_start=date(2026, 10, 5),
            period_end=date(2026, 10, 11),
            body_md="Company week packet",
            snapshot_json=json.dumps(_company_snapshot()),
            status="ready",
            created_by_id=owner.id,
            created_at=utcnow(),
        )
        db.session.add(report)
        db.session.add(
            Expense(
                user_id=owner.id,
                kind="gas",
                amount_cents=2500,
                merchant="Zephyr Fuel",
                note="",
                status="confirmed",
                source="human",
                created_by_id=owner.id,
                created_at=utcnow(),
            )
        )
        trip = Trip(
            title="Palm walk",
            status="staged",
            starts_on=date(2026, 10, 14),
            created_by_id=owner.id,
            created_at=utcnow(),
        )
        db.session.add(trip)
        db.session.flush()
        db.session.add(TripProperty(trip_id=trip.id, property_id=palm.id, sort_order=0))
        db.session.add(
            Job(
                property_id=palm.id,
                unit_id=palm_unit.id,
                title="Palm only valve",
                status="followup",
                created_by_id=owner.id,
                created_at=utcnow(),
            )
        )
        db.session.commit()
        return {
            "people": people,
            "cedar": cedar,
            "palm": palm,
            "cedar_unit": cedar_unit,
            "palm_unit": palm_unit,
            "region": region,
            "report": report,
            "trip": trip,
            "palm_tech": palm_tech,
            "gone": gone,
        }

    def _client(self, username, password):
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = AGENT
        signed = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
        self.assertIn(signed.status_code, (302, 303))
        return client

    def _gets(self, world):
        ids = {
            "property_id": world["cedar"].id,
            "unit_id": world["cedar_unit"].id,
            "city_id": world["cedar"].city_id,
            "report_id": world["report"].id,
            "trip_id": world["trip"].id,
            "token": "not-a-real-token",
            "name": "threat-map.css",
            "iso2": "US",
            "media_id": 999999,
        }
        adapter = APP.url_map.bind("localhost")
        found = []
        for rule in APP.url_map.iter_rules():
            if rule.endpoint == "static" or "GET" not in rule.methods:
                continue
            key = f"GET {rule.rule}"
            values = {arg: ids[arg] for arg in rule.arguments}
            path = adapter.build(rule.endpoint, values, method="GET")
            found.append((key, path, GATES[key]))
        return found

    def test_each_login_gets_the_right_status_on_every_get(self):
        world = self._company()
        paths = self._gets(world)
        logins = {
            "owner": ("alex", "field-pass"),
            "admin": ("ada", PASSWORD),
            "regional_manager": ("rita", PASSWORD),
            "regional_property_manager": ("pat", PASSWORD),
            "maintenance_regional": ("moe", PASSWORD),
            "property_manager": ("pam", PASSWORD),
            "assistant_manager": ("amy", PASSWORD),
            "office": ("ivy", PASSWORD),
            "maintenance_supervisor": ("sid", PASSWORD),
            "maintenance_manager": ("meg", PASSWORD),
            "maintenance_person": ("ted", PASSWORD),
            "viewer": ("bossview", PASSWORD),
            "boss": ("legacyboss", PASSWORD),
            "field": ("legacyfield", PASSWORD),
            "loner": ("loner", PASSWORD),
            "leasing": ("lea", PASSWORD),
        }
        bad = []
        canaries = ("Palm Grove", "9091", "Zephyr Fuel", "Palm walk", "Palm only valve")
        clients = {"anon": APP.test_client()}
        clients["anon"].environ_base["HTTP_USER_AGENT"] = AGENT
        for role, (username, password) in logins.items():
            clients[role] = self._client(username, password)
        for role, client in clients.items():
            scoped = role not in COMPANY and role != "anon"
            for key, path, gate in paths:
                page = client.get(path)
                want = _want(role, gate)
                if page.status_code != want:
                    bad.append(f"{role} {key} -> {path} got {page.status_code} want {want}")
                    continue
                if scoped and page.status_code == 200:
                    text = page.get_data(as_text=True)
                    leaked = [word for word in canaries if word in text]
                    if leaked:
                        bad.append(f"{role} {path} shows {leaked}")
        if bad:
            self.fail(f"{len(bad)} mismatches\n" + "\n".join(bad[:60]))

    def test_a_read_only_login_cannot_post_and_a_fence_holds(self):
        world = self._company()
        viewer = self._client("bossview", PASSWORD)
        boss = self._client("legacyboss", PASSWORD)
        with viewer.session_transaction() as sess:
            token = sess.get("csrf_token")
        with boss.session_transaction() as sess:
            boss_token = sess.get("csrf_token")
        adapter = APP.url_map.bind("localhost")
        ids = {
            "property_id": world["palm"].id,
            "unit_id": world["palm_unit"].id,
            "city_id": world["palm"].city_id,
            "report_id": world["report"].id,
            "trip_id": world["trip"].id,
            "user_id": world["people"]["owner"].id,
            "region_id": world["region"].id,
            "token": "not-a-real-token",
            "name": "threat-map.css",
            "iso2": "US",
            "entity": "unit",
            "pending_id": 999999,
            "task_id": 999999,
            "gear_id": 999999,
            "job_id": 999999,
            "key_id": 999999,
            "hat_id": 999999,
            "audit_id": 999999,
            "equipment_id": 999999,
            "pm_id": 999999,
            "contractor_id": 999999,
            "item_id": 999999,
            "record_id": 999999,
            "media_id": 999999,
        }
        skipped = {"POST /logout", "POST /appearance"}
        bad = []
        for rule in APP.url_map.iter_rules():
            if rule.endpoint == "static" or "POST" not in rule.methods:
                continue
            key = f"POST {rule.rule}"
            if key in skipped:
                continue
            values = {arg: ids[arg] for arg in rule.arguments}
            path = adapter.build(rule.endpoint, values, method="POST")
            for label, client, csrf in (("viewer", viewer, token), ("boss", boss, boss_token)):
                posted = client.post(path, data={"csrf_token": csrf, "idempotency_key": f"{label}-{rule.endpoint}"})
                if posted.status_code != 403:
                    bad.append(f"{label} {key} got {posted.status_code}")
        self.assertEqual(bad, [])

        pam = self._client("pam", PASSWORD)
        with pam.session_transaction() as sess:
            pam_token = sess.get("csrf_token")
        hidden = pam.get(f"/properties/{world['palm'].id}")
        self.assertEqual(hidden.status_code, 404)
        packed = pam.get(f"/api/pack/{world['palm'].id}")
        self.assertEqual(packed.status_code, 404)
        self.assertNotIn(b"9091", packed.data)
        search = pam.get("/api/places?q=Palm")
        self.assertEqual(search.get_json(), [])
        renamed = pam.post(
            f"/units/{world['palm_unit'].id}",
            data={"csrf_token": pam_token, "unit_number": "1"},
        )
        self.assertEqual(renamed.status_code, 404)
        removed = pam.post(
            f"/properties/{world['palm'].id}/delete",
            data={"csrf_token": pam_token, "idempotency_key": "no-palm"},
        )
        self.assertNotEqual(removed.status_code, 500)
        db.session.expire_all()
        palm = db.session.get(type(world["palm"]), world["palm"].id)
        unit = db.session.get(Unit, world["palm_unit"].id)
        self.assertIsNone(palm.deleted_at)
        self.assertEqual(unit.unit_number, "9091")
        page = pam.get("/users")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b"palmtech", page.data)
        self.assertIn(b"ted", page.data)
        office = self._client("ivy", PASSWORD)
        ledger = office.get("/expenses")
        self.assertEqual(ledger.status_code, 200)
        self.assertNotIn(b"Zephyr Fuel", ledger.data)
        owner = self._client("alex", "field-pass")
        self.assertIn(b"Zephyr Fuel", owner.get("/expenses").data)
        self.assertEqual(self._client("amy", PASSWORD).get("/users").status_code, 403)
        self.assertEqual(self._client("ada", PASSWORD).get("/security").status_code, 403)
        self.assertEqual(pam.get("/regions").status_code, 403)
        self.assertEqual(pam.get(f"/trips/{world['trip'].id}").status_code, 404)
        ted = self._client("ted", PASSWORD)
        with ted.session_transaction() as sess:
            ted_token = sess.get("csrf_token")
        denied = ted.post(
            f"/reports/{world['report'].id}/send",
            data={"csrf_token": ted_token, "idempotency_key": "no-send"},
        )
        self.assertEqual(denied.status_code, 403)
        closed = APP.test_client()
        closed.environ_base["HTTP_USER_AGENT"] = AGENT
        refused = closed.post("/login", data={"username": "gone", "password": PASSWORD})
        self.assertEqual(refused.status_code, 401)
        self.assertEqual(closed.get(f"/properties/{world['cedar'].id}").status_code, 302)
