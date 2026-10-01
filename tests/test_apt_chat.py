"""Apt integration tests: chat."""
from tests.apt_test_support import *  # noqa: F401,F403

class AptTest02(AptTestBase):
    def test_trip_sentence_files_place_purpose_and_miles(self):
        user = self.owner()
        asked = handle_message(user, "I'm going to Lubbock", idempotency_key="city")
        self.assertIn("Which property", asked["reply"])
        self.assertEqual(Trip.query.count(), 0)
        filed = handle_message(user, "Woodview for an AC install, 86 miles", idempotency_key="place")
        filed_words = (filed.get("reply") or "") + " " + self.save(user)
        self.assertIn("Woodview", filed_words)
        self.assertIn("Lubbock", filed_words)
        self.assertIn("AC install", filed_words)
        self.assertIn("86", filed_words)
        trip = Trip.query.one()
        self.assertEqual(float(trip.miles_estimate), 86.0)
        self.assertIn("AC install", trip.purpose)
        prop = Property.query.filter(db.func.lower(Property.name) == "woodview").one()
        self.assertEqual(prop.city.name, "Lubbock")
        updated = handle_message(
            user,
            "I'm going to Lubbock at Woodview for a blower motor with 90 miles",
            idempotency_key="update",
        )
        updated_words = (updated.get("reply") or "") + " " + self.save(user)
        self.assertIn("Updated", updated_words)
        self.assertEqual(Trip.query.count(), 1)
        self.assertEqual(float(Trip.query.one().miles_estimate), 90.0)
        self.assertIn("blower", Trip.query.one().purpose.lower())
        moved = handle_message(user, "Friday", idempotency_key="day")
        moved_words = (moved.get("reply") or "") + " " + self.save(user)
        self.assertIn("Friday", moved_words)
        self.assertEqual(Trip.query.one().starts_on.strftime("%A"), "Friday")
    def test_here_and_work_ask_the_next_question(self):
        user = self.owner()
        handle_message(user, "I'm going to Woodview Odessa Thursday for AC evals", idempotency_key="t")
        handle_message(user, "yes, save it", idempotency_key="t-yes")
        here = handle_message(user, "I'm here", idempotency_key="here")
        self.assertIn("Is this", here["reply"])
        self.assertIn("Woodview", here["reply"])
        handle_message(user, "yes", idempotency_key="yes-place")
        work = handle_message(user, "I replaced the compressor", idempotency_key="work")
        self.assertIn("Which unit", work["reply"])
        self.assertEqual(Job.query.count(), 0)
        done = handle_message(user, "12", idempotency_key="unit12")
        done_words = (done.get("reply") or "") + " " + self.save(user)
        self.assertEqual(Job.query.count(), 1)
        self.assertIn("12", done_words)
    def test_new_chat_clears_the_thread(self):
        user = self.owner()
        handle_message(user, "add park place from lubbock texas to my sites", idempotency_key="park")
        self.assertGreater(ChatMessage.query.filter_by(user_id=user.id).count(), 0)
        clear_chat(user)
        self.assertEqual(ChatMessage.query.filter_by(user_id=user.id).count(), 0)
    def test_a_place_lists_units_by_recent_work(self):
        from datetime import timedelta

        from app.models import Equipment, Job, Unit
        from app.services.browse import place_groups, unit_cards
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Madison Sq", "Lubbock", "TX", user.id)
        older = Unit(property_id=prop.id, unit_number="12", created_at=utcnow())
        newer = Unit(property_id=prop.id, unit_number="804", created_at=utcnow())
        db.session.add_all([older, newer])
        db.session.flush()
        db.session.add(
            Job(
                property_id=prop.id,
                unit_id=older.id,
                title="Changed the filter",
                created_at=utcnow() - timedelta(days=10),
            )
        )
        db.session.add(
            Job(
                property_id=prop.id,
                unit_id=newer.id,
                title="Replaced the compressor",
                created_at=utcnow(),
            )
        )
        db.session.add(
            Equipment(
                property_id=prop.id,
                unit_id=newer.id,
                kind="air conditioner",
                brand="Carrier",
                size_label="3 ton",
                model_number="24ACC",
                created_at=utcnow(),
            )
        )
        db.session.commit()
        recent = unit_cards(prop.id)
        self.assertEqual(recent["cards"][0]["unit"].unit_number, "804")
        self.assertIn("compressor", recent["cards"][0]["last_title"].lower())
        self.assertTrue(any("Carrier" in line for line in recent["cards"][0]["gear_lines"]))
        numbered = [card["unit"].unit_number for card in unit_cards(prop.id, sort="number")["cards"]]
        self.assertEqual(numbered, ["12", "804"])
        self.assertEqual(unit_cards(prop.id, query="804")["cards"][0]["unit"].unit_number, "804")
        places = place_groups()
        madison = places[0]["places"][0]
        self.assertEqual(madison["name"], "Madison Sq")
        self.assertEqual(madison["unit_count"], 2)
        self.assertIn("compressor", madison["last_title"].lower())
    def test_chat_deletes_a_plan_and_files_finished_miles(self):
        from app.models import MilesEntry, PlanItem
        from app.services.miles import traveled_total
        from app.services.pending import propose

        user = self.owner()
        handle_message(
            user,
            "I'm going to Madison Sq Lubbock Thursday for compressor installs",
            idempotency_key="go-madison",
        )
        self.save(user)
        self.assertGreater(PlanItem.query.filter(PlanItem.deleted_at.is_(None)).count(), 0)
        propose(
            user,
            "plan_trip",
            {"city": "Lubbock", "needs_answer": True, "waiting_for": "trip"},
            "Which property?",
            "low",
            "stuck",
            "stuck",
            "ai",
        )
        removed = handle_message(user, "delete the madison plan", idempotency_key="del-plan")
        removed_words = (removed.get("reply") or "") + " " + self.save(user)
        self.assertIn("Madison", removed_words)
        self.assertNotIn("Which property", removed_words)
        self.assertEqual(PlanItem.query.filter(PlanItem.deleted_at.is_(None), PlanItem.status.in_(("open", "partial"))).count(), 0)
        self.assertEqual(PendingAction.query.filter_by(status="needs_answer").count(), 0)
        handle_message(user, "I'm going to Woodview Odessa Friday for an eval", idempotency_key="go-wood")
        self.save(user)
        done = handle_message(user, "finished 90 miles", idempotency_key="fin-90")
        done_words = (done.get("reply") or "") + " " + self.save(user)
        self.assertIn("90", done_words)
        trip = Trip.query.filter(Trip.deleted_at.is_(None)).order_by(Trip.id.desc()).first()
        self.assertEqual(float(trip.miles_actual), 90.0)
        self.assertEqual(MilesEntry.query.filter_by(source="trip").one().miles, 90.0)
        added = handle_message(user, "add 12 miles", idempotency_key="add-12")
        self.assertTrue(added.get("ok"))
        self.save(user)
        self.assertGreaterEqual(traveled_total(user.id), 102.0)
    def test_place_lookup_keeps_the_city_she_named(self):
        from app.services.geo import place_from_hits

        rows = [
            {
                "lat": "32.0",
                "lon": "-102.0",
                "name": "Woodview Apartments",
                "display_name": "Woodview Apartments, Midland, Texas",
                "address": {"house_number": "1", "road": "Main St", "city": "Midland", "state": "Texas"},
            },
            {
                "lat": "31.88",
                "lon": "-102.36",
                "name": "Woodview Apartments",
                "display_name": "Woodview Apartments, 4101 East 42nd Street, Odessa, Texas",
                "address": {
                    "house_number": "4101",
                    "road": "East 42nd Street",
                    "city": "Odessa",
                    "state": "Texas",
                    "postcode": "79762",
                },
            },
        ]
        found = place_from_hits(rows, "Woodview", "Odessa", "TX")
        self.assertIn("4101 East 42nd Street", found["address"])
        self.assertIn("Odessa", found["address"])
        self.assertNotIn("Midland", found["address"])
        self.assertIsNone(place_from_hits(rows, "Woodview", "Lubbock", "TX"))
        from app.services.geo import address_from_listings

        page = (
            "Woodview Apartments 4330 N Grandview Ave, Odessa, TX 79762 listing. "
            "Woodview 4330 N Grandview Ave, Odessa, TX 79762 again. "
            "Other Place 10 Main St, Midland, TX 79701."
        )
        self.assertIn("4330 N Grandview Ave", address_from_listings(page, "Woodview", "Odessa", "TX"))
        self.assertNotIn("Midland", address_from_listings(page, "Woodview", "Odessa", "TX"))
        self.assertEqual(address_from_listings(page, "Woodview", "Lubbock", "TX"), "")
    def test_dated_work_and_appliance_questions(self):
        from datetime import timezone

        from app.models import Equipment
        from app.services.clock import zone

        user = self.owner()
        filed = handle_message(
            user,
            "on the 19th of march this year I replaced the compressor at woodview odessa unit 804",
            idempotency_key="mar19",
        )
        filed_words = (filed.get("reply") or "") + " " + self.save(user)
        self.assertIn("804", filed_words)
        self.assertIn("Woodview", filed_words)
        from app.services.clock import local_today

        year = local_today().year
        self.assertIn(f"{year}-03-19", filed_words)
        job = Job.query.one()
        local = job.created_at.replace(tzinfo=timezone.utc).astimezone(zone("America/Chicago"))
        self.assertEqual(local.date().isoformat(), f"{year}-03-19")
        self.assertEqual(Unit.query.one().unit_number, "804")
        prop = Property.query.filter(db.func.lower(Property.name) == "woodview").one()
        self.assertEqual(prop.city.name, "Odessa")
        placed = handle_message(
            user,
            "the whirlpool fridge is in unit 12 at madison sq lubbock",
            idempotency_key="fridge",
        )
        placed_words = (placed.get("reply") or "") + " " + self.save(user)
        self.assertIn("Whirlpool", placed_words)
        self.assertIn("12", placed_words)
        gear = Equipment.query.filter_by(kind="refrigerator").one()
        self.assertEqual(gear.brand, "Whirlpool")
        self.assertEqual(gear.unit.unit_number, "12")
        who = handle_message(user, "who has a whirlpool fridge", idempotency_key="who-fridge")
        self.assertIn("unit 12", who["reply"])
        self.assertIn("Madison Sq", who["reply"])
        newest = handle_message(user, "where did I put the most recent appliances", idempotency_key="newest")
        self.assertIn("Whirlpool", newest["reply"])
        self.assertIn("unit 12", newest["reply"])
    def test_model_answer_is_not_replaced(self):
        user = self.owner()
        db.session.add(
            ApiCredential(
                user_id=user.id,
                provider="gemini",
                secret_ciphertext=encrypt_text("AIza-test-key-value"),
                last4="alue",
                model_id="gemini-3.8-flash",
                created_at=utcnow(),
            )
        )
        db.session.commit()
        with patch(
            "app.services.providers.gemini_complete",
            return_value={
                "ok": True,
                "text": "You have two Woodview rows, ids 4 and 9, because the second add did not match the first name.",
                "calls": [],
            },
        ):
            heard = handle_message(
                user,
                "why did you make 2 different entries for the same property",
                idempotency_key="why-two",
            )
        self.assertIn("two Woodview rows", heard["reply"])
        self.assertNotIn("matching job", heard["reply"])
    def test_delete_property_from_the_sentence(self):
        user = self.owner()
        handle_message(user, "it's at woodview odessa texas", idempotency_key="add-w")
        self.save(user)
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 1)
        asked = handle_message(user, "delete woodview", idempotency_key="del-w")
        self.assertIn("Say yes", asked["reply"])
        self.assertIn("Woodview", asked["reply"])
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 1)
        removed = handle_message(user, "yes", idempotency_key="del-yes")
        self.assertIn("Removed", removed["reply"])
        self.assertIn("Woodview", removed["reply"])
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 0)
    def test_report_names_who_prepared_it(self):
        from app.services.reports import render_markdown

        text = render_markdown(
            {
                "title": "Weekly report",
                "company": "Acme",
                "author": "Maria Fuentes",
                "period": {"start": "2026-03-01", "end": "2026-03-07"},
                "totals": {},
                "expenses": {},
                "miles": {},
                "prior": {},
                "properties": [],
            }
        )
        self.assertIn("Prepared by Maria Fuentes.", text)
    def test_chat_uses_the_assistant_name(self):
        from app.services.providers import voice_brief
        from app.services.records import site_profile

        self.owner()
        profile = site_profile()
        profile.assistant_name = "Christopher"
        profile.tone = "talking to my boss"
        db.session.commit()
        heard = voice_brief()
        self.assertIn("Your name is Christopher.", heard)
        self.assertIn("talking to my boss", heard)
    def test_add_woodview_ignores_a_wrong_model_tool(self):
        from app.services.talk import _place_she_named

        said = "can you add woodview to my list and look up the address? ITs woodview odessa texas"
        place = _place_she_named(said)
        self.assertEqual(place["property_name"], "Woodview")
        self.assertEqual(place["city"], "Odessa")
        self.assertEqual(place["region"], "Texas")
        self.assertTrue(_place_she_named("i said add a property please pay attention") is None or _place_she_named("i said add a property please pay attention").get("needs_name"))
        user = self.owner()
        db.session.add(
            ApiCredential(
                user_id=user.id,
                provider="gemini",
                secret_ciphertext=encrypt_text("AIza-test-key-value"),
                last4="alue",
                model_id="gemini-3.8-flash",
                created_at=utcnow(),
            )
        )
        db.session.commit()
        hit = {
            "address": "4330 N Grandview Ave, Odessa, TX",
            "lat": 31.89,
            "lng": -102.35,
            "label": "Woodview",
        }
        with patch("app.services.providers.gemini_complete", return_value={"ok": False, "error": "down"}), patch(
            "app.services.geo.lookup_place", return_value=hit
        ):
            heard = handle_message(user, said, idempotency_key="add-wood")
            heard_words = (heard.get("reply") or "") + " " + self.save(user)
        self.assertIn("Woodview", heard_words)
        self.assertIn("4330 N Grandview", heard_words)
        self.assertNotIn("matching job", heard_words)
        self.assertNotIn("Madison", heard_words)
        prop = Property.query.filter(Property.deleted_at.is_(None)).one()
        self.assertEqual(prop.name, "Woodview")
        self.assertEqual(prop.city.name, "Odessa")
    def test_online_address_is_not_a_job_search(self):
        from app.services.talk import _address_she_wants

        asked = "whats the address for brookview odessa texas find it on google"
        place = _address_she_wants(asked)
        self.assertEqual(place["property_name"], "Brookview")
        self.assertEqual(place["city"], "Odessa")
        self.assertEqual(place["region"], "Texas")
        again = _address_she_wants("search online not my site for the brookview odessa address")
        self.assertEqual(again["property_name"], "Brookview")
        self.assertEqual(again["city"], "Odessa")
        user = self.owner()
        db.session.add(
            ApiCredential(
                user_id=user.id,
                provider="gemini",
                secret_ciphertext=encrypt_text("AIza-test-key-value"),
                last4="alue",
                model_id="gemini-3.8-flash",
                created_at=utcnow(),
            )
        )
        db.session.commit()
        hit = {"address": "5100 E Everglade Ave, Odessa, TX", "lat": 31.9, "lng": -102.3, "label": "Brookview"}
        with patch(
            "app.services.providers.gemini_complete",
            return_value={"ok": False, "error": "down"},
        ), patch("app.services.geo.lookup_place", return_value=hit):
            heard = handle_message(user, asked, idempotency_key="brook-web")
        self.assertIn("5100 E Everglade", heard["reply"])
        self.assertNotIn("matching job", heard["reply"])
        self.assertNotIn("current records", heard["reply"])
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 0)
    def test_remove_brookview_matches_without_the_full_name(self):
        user = self.owner()
        handle_message(user, "it's at brookview odessa texas", idempotency_key="add-b")
        self.save(user)
        db.session.add(
            ApiCredential(
                user_id=user.id,
                provider="gemini",
                secret_ciphertext=encrypt_text("AIza-test-key-value"),
                last4="alue",
                model_id="gemini-3.8-flash",
                created_at=utcnow(),
            )
        )
        db.session.commit()
        with patch(
            "app.services.providers.gemini_complete",
            return_value={"ok": False, "error": "down"},
        ):
            heard = handle_message(user, "please remove brookview", idempotency_key="rm-b")
            self.assertIn("Say yes", heard["reply"])
            self.assertIn("Brookview", heard["reply"])
            self.assertIn("Odessa", heard["reply"])
            self.assertIn("Texas", heard["reply"])
            self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 1)
            done = handle_message(user, "yes", idempotency_key="rm-yes")
            self.assertIn("Removed", done["reply"])
            self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 0)
            handle_message(user, "it's at brookview lubbock texas", idempotency_key="add-l")
            updated = handle_message(
                user,
                "update the lubbock apartment brookview with 3843 Penbrook St, Lubbock, TX",
                idempotency_key="addr-l",
            )
            applied = self.save(user)
        self.assertIn("Lubbock, Texas", applied)
        self.assertIn("3843 Penbrook", applied)
        self.assertNotIn(" TX", applied)
        saved = Property.query.filter(Property.deleted_at.is_(None)).one()
        self.assertIn("Texas", saved.address)
        self.assertNotIn("TX", saved.address)
