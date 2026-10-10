"""Offline guide. Letters or plain words, and nothing is written until Save."""
from __future__ import annotations

import re
from unittest.mock import patch

from tests.apt_test_support import *  # noqa: F401,F403


def _letter(reply: str, snippet: str) -> str:
    for line in (reply or "").splitlines():
        found = re.match(r"^\s*([A-F])\.\s+(.*)$", line)
        if found and snippet.lower() in found.group(2).lower():
            return found.group(1).lower()
    raise AssertionError(f"{snippet!r} was not a choice in:\n{reply}")


class GuideHearingTests(AptTestBase):
    """Spelling and choice reading. These do not write a unit."""

    def test_trade_words_and_yes_no_skip(self):
        from app.services.talk.guide.hear import is_no, is_skip, is_yes, trade_guess

        self.assertEqual(trade_guess("trsh out").get("slug"), "trashout")
        self.assertEqual(trade_guess("trshout").get("slug"), "trashout")
        self.assertEqual(trade_guess("pant").get("slug"), "paint")
        self.assertTrue(is_yes("yeah"))
        self.assertTrue(is_no("no"))
        self.assertTrue(is_skip("idk"))
        self.assertFalse(is_yes("done"))
        self.assertFalse(is_yes("finished"))

    def test_close_vendor_names_are_not_guessed(self):
        from app.services.talk.guide.hear import close_names

        exact = close_names("fvs", ["FVS", "FVT"])
        self.assertEqual(exact.get("name"), "FVS")
        fuzzy = close_names("fv", ["FVS", "FVT"])
        self.assertFalse(fuzzy.get("name"))
        self.assertCountEqual(fuzzy.get("names") or [], ["FVS", "FVT"])

    def test_catalog_registers_the_walks_that_exist(self):
        from app.services.talk.guide.catalog import all_jobs, match
        from app.services.talk.guide.script import problems

        jobs = all_jobs()
        self.assertEqual([job.id for job in jobs], ["guide_menu", "make_ready", "finish_vendor_trade"])
        self.assertEqual([job.tool for job in jobs], ["guide_menu", "make_ready_unit", "finish_vendor_trade"])
        for job in jobs:
            self.assertEqual(problems(job), [])
        self.assertEqual(match("hi").id, "guide_menu")
        self.assertEqual(match("what would you like to do").id, "guide_menu")
        self.assertEqual(match("guide me through adding a vendor").id, "guide_menu")
        self.assertEqual(match("make ready").id, "make_ready")
        self.assertEqual(match("make redy").id, "make_ready")
        self.assertEqual(match("guide me in completing the vendor trash out").id, "finish_vendor_trade")
        self.assertEqual(match("the 403 trash out is done").id, "finish_vendor_trade")
        self.assertIsNone(match("plan paint for unit 210"))


class GuideFinishTests(AptTestBase):
    def _say(self, user, text):
        count = getattr(self, "_turns", 0) + 1
        self._turns = count
        return handle_message(user, text, idempotency_key=f"{self.id()}-{count}")

    def _woodview(self, user):
        from app.services.board import ensure_unit
        from app.services.contractors import assign_trade_vendor
        from app.services.records import ensure_property

        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        db.session.commit()
        units = {}
        for number in ("210", "403", "430"):
            unit, _status = ensure_unit(prop, number, user, "human")
            unit.occupancy = "make_ready"
            unit.rentable = False
            units[number] = unit
        assign_trade_vendor(user, units["403"], "trash out", "FVS", "human")
        db.session.commit()
        return prop, units

    def _task(self, unit, title="Trashout"):
        from app.models import UnitTask

        return UnitTask.query.filter_by(unit_id=unit.id, title=title).filter(UnitTask.deleted_at.is_(None)).one()

    def _open_guide(self, user):
        from app.services.records import loads

        row = (
            PendingAction.query.filter_by(user_id=user.id, tool="guide", status="needs_answer")
            .order_by(PendingAction.id.desc())
            .first()
        )
        self.assertIsNotNone(row)
        payload = loads(row.payload_json)
        self.assertNotIn(payload.get("waiting_for"), {"unit", "property", "city", "property_confirm", "default_confirm"})
        return row, payload

    def test_guide_me_asks_unit_then_trade_and_saves_on_a(self):
        from app.models import ContractorVisit

        user = self.owner()
        _prop, units = self._woodview(user)
        heard = self._say(user, "guide me in completing the vendor trash out")
        self.assertIn("Which unit?", heard["reply"])
        self.assertIn("Nothing is saved yet.", heard["reply"])
        self.assertNotEqual(self._task(units["403"]).status, "done")
        _row, payload = self._open_guide(user)
        self.assertEqual(payload.get("step"), "unit")

        heard = self._say(user, _letter(heard["reply"], "403 at Woodview"))
        self.assertIn("Which trade?", heard["reply"])
        self.assertIn("Trash out", heard["reply"])
        self.assertNotIn("Trashout", heard["reply"])

        heard = self._say(user, "trsh out")
        self.assertIn("Unit 403 trash out is already FVS.", heard["reply"])
        self.assertIn("Was that the vendor?", heard["reply"])

        heard = self._say(user, "yeah")
        self.assertIn("When did they start?", heard["reply"])
        heard = self._say(user, _letter(heard["reply"], "This morning"))
        self.assertIn("When did they finish?", heard["reply"])
        heard = self._say(user, "around 2")
        self.assertIn("Were they on time?", heard["reply"])
        self.assertIn("You can skip this.", heard["reply"])
        heard = self._say(user, "idk")
        self.assertIn("Nothing is saved yet.", heard["reply"])
        self.assertIn("FVS", heard["reply"])
        self.assertIn("A. Save", heard["reply"])
        self.assertNotEqual(self._task(units["403"]).status, "done")
        self.assertEqual(ContractorVisit.query.count(), 0)

        saved = self._say(user, "a")
        self.assertIn("Saved. 403 trash out by FVS is marked done.", saved["reply"])
        self.assertEqual(self._task(units["403"]).status, "done")
        visit = ContractorVisit.query.one()
        self.assertIsNotNone(visit.check_in)
        self.assertIsNotNone(visit.check_out)
        self.assertGreater(visit.check_out, visit.check_in)
        hours = (visit.check_out - visit.check_in).total_seconds() / 3600
        self.assertGreater(hours, 5)
        self.assertLess(hours, 7)
        self.assertFalse(visit.note)
        self.assertEqual(
            PendingAction.query.filter_by(user_id=user.id, tool="finish_vendor_trade", status="pending").count(),
            0,
        )

        again = self._say(user, "a")
        self.assertEqual(ContractorVisit.query.count(), 1)
        self.assertNotIn("marked done", (again.get("reply") or "").lower())

    def test_done_sentence_skips_unit_and_trade_and_the_button_saves_once(self):
        from app.models import ContractorVisit

        user = self.owner()
        _prop, units = self._woodview(user)
        heard = self._say(user, "the 403 trash out is done")
        self.assertNotIn("Which unit?", heard["reply"])
        self.assertNotIn("Which trade?", heard["reply"])
        self.assertIn("already FVS", heard["reply"])
        heard = self._say(user, "yeah")
        heard = self._say(user, _letter(heard["reply"], "This morning"))
        heard = self._say(user, "around 2")
        heard = self._say(user, "idk")
        self.assertIn("A. Save", heard["reply"])
        self.assertNotEqual(self._task(units["403"]).status, "done")
        saved = self.save(user)
        self.assertIn("Saved. 403 trash out by FVS is marked done.", saved)
        self.assertEqual(self._task(units["403"]).status, "done")
        visit = ContractorVisit.query.one()
        self.assertFalse(visit.note)
        self.assertEqual(ContractorVisit.query.count(), 1)

    def test_one_paragraph_reviews_without_asking_again(self):
        from app.models import ContractorVisit

        user = self.owner()
        _prop, units = self._woodview(user)
        heard = self._say(
            user,
            "yeah 403 trshout is done it was fvs they started this morning and finished at 2 and they were on time",
        )
        self.assertIn("Nothing is saved yet.", heard["reply"])
        self.assertIn("403 at Woodview", heard["reply"])
        self.assertIn("Trash out", heard["reply"])
        self.assertIn("FVS", heard["reply"])
        self.assertIn("On time", heard["reply"])
        self.assertNotIn("Which unit?", heard["reply"])
        self.assertNotIn("Which trade?", heard["reply"])
        self.assertNotEqual(self._task(units["403"]).status, "done")
        saved = self._say(user, "a")
        self.assertIn("marked done", saved["reply"])
        visit = ContractorVisit.query.one()
        self.assertEqual(visit.note, "On time")

    def test_a_short_prefix_shows_both_units(self):
        user = self.owner()
        self._woodview(user)
        self._say(user, "guide me in completing the vendor trash out")
        heard = self._say(user, "43")
        self.assertIn("won't guess", heard["reply"])
        self.assertIn("403", heard["reply"])
        self.assertIn("430", heard["reply"])
        _row, payload = self._open_guide(user)
        self.assertEqual(payload.get("step"), "unit")
        self.assertNotIn("unit_id", payload.get("filled") or {})

    def test_a_bare_number_is_a_letter_and_around_two_is_a_time(self):
        user = self.owner()
        self._woodview(user)
        heard = self._say(user, "guide me in completing the vendor trash out")
        heard = self._say(user, _letter(heard["reply"], "403 at Woodview"))
        heard = self._say(user, "trsh out")
        heard = self._say(user, "yeah")
        heard = self._say(user, _letter(heard["reply"], "This morning"))
        lunch = self._say(user, "2")
        self.assertIn("Around lunch", lunch["reply"])
        self.assertIn("Were they on time?", lunch["reply"])
        self.assertNotIn("about 2", lunch["reply"].lower())

        user = self.owner("alex2")
        self._woodview(user)
        heard = self._say(user, "guide me in completing the vendor trash out")
        heard = self._say(user, _letter(heard["reply"], "403 at Woodview"))
        heard = self._say(user, "trsh out")
        heard = self._say(user, "yeah")
        heard = self._say(user, _letter(heard["reply"], "This morning"))
        typed = self._say(user, "around 2")
        self.assertIn("about 2", typed["reply"].lower())
        self.assertIn("Were they on time?", typed["reply"])
        self.assertNotIn("Around lunch", typed["reply"])

    def test_two_misses_then_back_and_cancel_write_nothing(self):
        user = self.owner()
        _prop, units = self._woodview(user)
        heard = self._say(user, "guide me in completing the vendor trash out")
        heard = self._say(user, "purple")
        self.assertIn("didn't catch", heard["reply"].lower())
        heard = self._say(user, "purple")
        self.assertIn("Reply with just the letter.", heard["reply"])
        heard = self._say(user, _letter(heard["reply"], "403 at Woodview"))
        self.assertIn("Which trade?", heard["reply"])
        heard = self._say(user, "back")
        self.assertIn("Which unit?", heard["reply"])
        heard = self._say(user, "cancel")
        self.assertIn("Cancelled. Nothing was saved.", heard["reply"])
        self.assertNotEqual(self._task(units["403"]).status, "done")
        self.assertEqual(
            PendingAction.query.filter(
                PendingAction.user_id == user.id,
                PendingAction.status.in_(("needs_answer", "pending")),
            ).count(),
            0,
        )

    def test_someone_else_is_typed_and_not_saved_on_cancel(self):
        from app.models import Contractor

        user = self.owner()
        self._woodview(user)
        heard = self._say(user, "the 403 trash out is done")
        heard = self._say(user, _letter(heard["reply"], "Someone else"))
        self.assertIn("Type the vendor", heard["reply"])
        heard = self._say(user, "Ace Plumbing")
        self.assertIn("When did they start?", heard["reply"])
        self._say(user, "cancel")
        self.assertIsNone(Contractor.query.filter_by(name="Ace Plumbing").first())

    def test_occupied_unit_is_refused_on_save(self):
        user = self.owner()
        _prop, units = self._woodview(user)
        units["403"].occupancy = "occupied"
        db.session.commit()
        heard = self._say(
            user,
            "yeah 403 trshout is done it was fvs they started this morning and finished at 2 and they were on time",
        )
        self.assertIn("A. Save", heard["reply"])
        saved = self._say(user, "a")
        self.assertIn("vacant", saved["reply"].lower())
        self.assertNotEqual(self._task(units["403"]).status, "done")

    def test_plan_paint_is_not_a_guide_or_a_trip(self):
        from app.models import Trip

        user = self.owner()
        self._woodview(user)
        heard = self._say(user, "plan paint for unit 210")
        self.assertEqual(PendingAction.query.filter_by(tool="guide").count(), 0)
        self.assertEqual(Trip.query.filter(Trip.deleted_at.is_(None)).count(), 0)
        self.assertNotIn("where should i plan", (heard.get("reply") or "").lower())

    def test_a_finished_trade_sentence_stays_on_the_old_card(self):
        user = self.owner()
        self._woodview(user)
        self._say(user, "carpet is done on unit 210")
        self.assertEqual(PendingAction.query.filter_by(tool="guide").count(), 0)
        self.assertEqual(PendingAction.query.filter_by(tool="ready_check").count(), 1)

    def test_adding_a_vendor_opens_the_chooser(self):
        user = self.owner()
        heard = self._say(user, "guide me through adding a vendor")
        self.assertIn("What would you like to do?", heard["reply"])
        self.assertIn("Make ready", heard["reply"])
        self.assertIn("Vendor", heard["reply"])
        self.assertIn("Units", heard["reply"])
        self.assertIn("Equipment", heard["reply"])
        self.assertNotIn("Finish a vendor trade", heard["reply"])
        self.assertNotIn("Which unit?", heard["reply"])
        _row, payload = self._open_guide(user)
        self.assertEqual(payload.get("step"), "area")
        self.assertEqual(payload.get("job"), "guide_menu")

    def test_hi_opens_the_chooser_and_a_blank_message_does_not(self):
        user = self.owner()
        blank = handle_message(user, "   ", idempotency_key=f"{self.id()}-blank")
        self.assertIn("AI chat is offline. How can I help?", blank["reply"])
        self.assertEqual(ChatMessage.query.count(), 0)
        self.assertEqual(PendingAction.query.count(), 0)
        heard = self._say(user, "hi")
        self.assertIn("What would you like to do?", heard["reply"])
        self.assertIn("Make ready", heard["reply"])
        self.assertNotIn("AI chat is offline", heard["reply"])
        self.assertTrue((heard.get("proposal") or {}).get("payload", {}).get("choices"))
        _row, payload = self._open_guide(user)
        self.assertEqual(payload.get("step"), "area")

    def test_help_with_a_key_does_not_say_offline(self):
        from app.services.crypto import encrypt_text

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
        heard = self._say(user, "what can you do")
        self.assertNotIn("AI chat is offline", heard["reply"])
        self.assertIn("What would you like to do?", heard["reply"])
        self.assertNotIn("help_page_url", heard)
        opened = self._say(user, "open help")
        self.assertEqual(opened.get("help_page_url"), "/help")

    def test_a_viewer_cannot_start_the_walk(self):
        user = self.owner()
        self._woodview(user)
        boss, _generated = create_user(
            username="boss",
            password="field-pass",
            display_name="Boss",
            role="viewer",
            email="boss@example.com",
        )
        db.session.commit()
        heard = self._say(boss, "guide me in completing the vendor trash out")
        self.assertIn("can't change make-ready work", heard["reply"])
        self.assertEqual(PendingAction.query.filter_by(user_id=boss.id).count(), 0)

    def test_a_working_key_hears_a_new_sentence_before_the_guide(self):
        from app.services.crypto import encrypt_text

        user = self.owner()
        self._woodview(user)
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
        with patch("app.services.providers.collect_tool_calls", return_value={"text": "The model answered.", "calls": []}) as heard_model:
            heard = self._say(user, "guide me in completing the vendor trash out")
        self.assertEqual(heard_model.call_count, 1)
        self.assertEqual(heard["reply"], "The model answered.")
        self.assertEqual(PendingAction.query.filter_by(tool="guide").count(), 0)

        with patch("app.services.providers.collect_tool_calls", return_value=None) as missed:
            started = self._say(user, "guide me in completing the vendor trash out")
        self.assertEqual(missed.call_count, 1)
        self.assertIn("Which unit?", started["reply"])

    def test_an_open_guide_does_not_call_the_model(self):
        from app.services.crypto import encrypt_text

        user = self.owner()
        self._woodview(user)
        started = self._say(user, "guide me in completing the vendor trash out")
        self.assertIn("Which unit?", started["reply"])
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
        with patch("app.services.providers.collect_tool_calls", return_value={"text": "The model answered.", "calls": []}) as heard_model:
            heard = self._say(user, _letter(started["reply"], "403 at Woodview"))
        self.assertEqual(heard_model.call_count, 0)
        self.assertIn("Which trade?", heard["reply"])
        self.assertNotIn("The model answered.", heard["reply"])

    def test_yes_save_it_still_saves_the_open_confirm_card(self):
        user = self.owner()
        self._woodview(user)
        miles = self._say(user, "drove 12 miles")
        self.assertTrue(miles.get("pending") or "miles" in (miles.get("reply") or "").lower())
        guide = self._say(user, "guide me in completing the vendor trash out")
        self.assertIn("Which unit?", guide["reply"])
        saved = self._say(user, "yes, save it")
        self.assertNotIn("Which unit?", saved.get("reply") or "")
        self.assertEqual(PendingAction.query.filter_by(tool="log_miles", status="accepted").count(), 1)
        self.assertEqual(PendingAction.query.filter_by(tool="guide", status="needs_answer").count(), 1)

    def test_a_pasted_key_is_not_saved_into_the_walk(self):
        user = self.owner()
        self._woodview(user)
        self._say(user, "guide me in completing the vendor trash out")
        before = ChatMessage.query.count()
        leaked = handle_message(user, "AIzaSyA1234567890abcdefghij", idempotency_key=f"{self.id()}-leak")
        self.assertFalse(leaked.get("ok"))
        self.assertIn("not saved", leaked["reply"].lower())
        self.assertEqual(ChatMessage.query.count(), before)
        self.assertEqual(PendingAction.query.filter_by(tool="guide", status="needs_answer").count(), 1)
        self.assertEqual(ApiCredential.query.count(), 0)

    def test_menu_walks_an_existing_make_ready_into_the_trade(self):
        user = self.owner()
        self._woodview(user)
        heard = self._say(user, "what would you like to do")
        self.assertIn("What would you like to do?", heard["reply"])
        heard = self._say(user, _letter(heard["reply"], "Make ready"))
        self.assertIn("new make ready or an existing one", heard["reply"])
        heard = self._say(user, "exisiting")
        self.assertIn("Which unit?", heard["reply"])
        self.assertIn("210", heard["reply"])
        self.assertIn("403", heard["reply"])
        self.assertIn("430", heard["reply"])
        heard = self._say(user, "43")
        self.assertIn("won't guess", heard["reply"])
        self.assertIn("403", heard["reply"])
        self.assertIn("430", heard["reply"])
        _row, payload = self._open_guide(user)
        self.assertNotIn("unit_id", payload.get("filled") or {})
        heard = self._say(user, _letter(heard["reply"], "403 at Woodview"))
        self.assertIn("What should we do with unit 403?", heard["reply"])
        heard = self._say(user, _letter(heard["reply"], "Finish"))
        self.assertIn("Which trade?", heard["reply"])
        self.assertNotIn("Which unit?", heard["reply"])
        self.assertNotEqual(self._task(self._woodview_unit(user, "403")).status, "done")

    def test_areas_ask_one_follow_up_not_every_job(self):
        user = self.owner()
        self._woodview(user)
        heard = self._say(user, "what would you like to do")
        self.assertIn("Vendor", heard["reply"])
        self.assertIn("More", heard["reply"])
        self.assertNotIn("A. Plans", heard["reply"])
        self.assertNotIn("Complete", heard["reply"])
        more = self._say(user, _letter(heard["reply"], "More"))
        self.assertIn("What else?", more["reply"])
        self.assertIn("Plans", more["reply"])
        self.assertIn("People", more["reply"])
        self.assertIn("Office", more["reply"])
        self.assertNotIn("Make ready", more["reply"])
        self._say(user, "back")
        vendor = self._say(user, "vendor")
        self.assertIn("What about vendor?", vendor["reply"])
        self.assertIn("Complete", vendor["reply"])
        self.assertIn("Out", vendor["reply"])
        self.assertIn("Note", vendor["reply"])
        self.assertIn("Add", vendor["reply"])
        self.assertNotIn("Which unit?", vendor["reply"])
        done = self._say(user, _letter(vendor["reply"], "Complete"))
        self.assertIn("Which unit?", done["reply"])
        self._say(user, "cancel")
        gear = self._say(user, "what would you like to do")
        gear = self._say(user, _letter(gear["reply"], "Equipment"))
        self.assertIn("What about equipment?", gear["reply"])
        self.assertIn("Move", gear["reply"])
        self.assertIn("Swap", gear["reply"])
        self.assertIn("Replace", gear["reply"])
        asked = self._say(user, _letter(gear["reply"], "Move"))
        self.assertIn("What are you moving", asked["reply"])
        self.assertNotIn("A. Swap", asked["reply"])

    def _woodview_unit(self, user, number):
        from app.models import Unit

        return Unit.query.filter_by(unit_number=number).filter(Unit.deleted_at.is_(None)).one()

    def test_a_new_make_ready_is_not_saved_until_the_letter(self):
        from app.services.board import ensure_unit
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        db.session.commit()
        unit, _status = ensure_unit(prop, "105", user, "human")
        unit.occupancy = ""
        unit.rentable = True
        db.session.commit()
        heard = self._say(user, "make ready")
        self.assertIn("new make ready or an existing one", heard["reply"])
        heard = self._say(user, _letter(heard["reply"], "A new make ready"))
        self.assertIn("Which unit should become a make ready?", heard["reply"])
        self.assertIn("105", heard["reply"])
        heard = self._say(user, _letter(heard["reply"], "105"))
        self.assertIn("What should we do with unit 105?", heard["reply"])
        heard = self._say(user, _letter(heard["reply"], "Just mark it"))
        self.assertIn("Nothing is saved yet.", heard["reply"])
        self.assertIn("Tap Needs correction", heard["reply"])
        self.assertNotEqual(unit.occupancy, "make_ready")
        db.session.refresh(unit)
        self.assertNotEqual(unit.occupancy, "make_ready")
        saved = self._say(user, "a")
        self.assertIn("Saved. Unit 105 is a make ready.", saved["reply"])
        db.session.refresh(unit)
        self.assertEqual(unit.occupancy, "make_ready")
        self.assertFalse(unit.rentable)

    def test_add_one_trade_on_an_existing_make_ready(self):
        from app.models import UnitTask

        user = self.owner()
        _prop, units = self._woodview(user)
        heard = self._say(user, "make ready")
        heard = self._say(user, _letter(heard["reply"], "An existing make ready"))
        heard = self._say(user, _letter(heard["reply"], "210 at Woodview"))
        heard = self._say(user, _letter(heard["reply"], "Add work"))
        self.assertIn("Which work does it need?", heard["reply"])
        heard = self._say(user, "pant")
        self.assertIn("Nothing is saved yet.", heard["reply"])
        self.assertIn("add paint", heard["reply"])
        self.assertEqual(UnitTask.query.filter_by(unit_id=units["210"].id, title="Paint").count(), 0)
        saved = self._say(user, "a")
        self.assertIn("Saved. Unit 210 is a make ready and needs paint.", saved["reply"])
        self.assertEqual(UnitTask.query.filter_by(unit_id=units["210"].id, title="Paint").count(), 1)

    def test_vendor_needs_correction_reopens_that_line_only(self):
        from app.models import Contractor

        user = self.owner()
        _prop, units = self._woodview(user)
        heard = self._say(
            user,
            "yeah 403 trshout is done it was fvs they started this morning and finished at 2 and they were on time",
        )
        self.assertIn("A. Save", heard["reply"])
        self.assertIn("Tap Needs correction", heard["reply"])
        heard = self._say(user, "Vendor needs correction")
        self.assertIn("already FVS", heard["reply"])
        self.assertNotIn("Which unit?", heard["reply"])
        self.assertNotIn("When did they start?", heard["reply"])
        self.assertNotEqual(self._task(units["403"]).status, "done")
        heard = self._say(user, _letter(heard["reply"], "Someone else"))
        self.assertIn("Type the vendor", heard["reply"])
        heard = self._say(user, "Ace Plumbing")
        self.assertIn("A. Save", heard["reply"])
        self.assertIn("Ace Plumbing", heard["reply"])
        self.assertNotIn("When did they start?", heard["reply"])
        self.assertNotIn("Which unit?", heard["reply"])
        self._say(user, "cancel")
        self.assertIsNone(Contractor.query.filter_by(name="Ace Plumbing").first())
        self.assertNotEqual(self._task(units["403"]).status, "done")

    def test_the_choice_buttons_and_correction_buttons_render(self):
        user = self.owner()
        self._woodview(user)
        self._say(user, "what would you like to do")
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        page = client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"What would you like to do?", page.data)
        self.assertIn(b'data-answer="A"', page.data)
        self.assertIn(b"Make ready", page.data)
        self.assertIn(b"Vendor", page.data)
        self.assertIn(b"Units", page.data)
        self.assertIn(b"Equipment", page.data)
        self.assertNotIn(b"Finish a vendor trade", page.data)
        self.assertIn(b">Cancel</button>", page.data)
        self._say(
            user,
            "yeah 403 trshout is done it was fvs they started this morning and finished at 2 and they were on time",
        )
        review = client.get("/")
        self.assertEqual(review.status_code, 200)
        self.assertIn(b"Needs correction", review.data)
        self.assertIn(b'data-answer="Vendor needs correction"', review.data)
        self.assertIn(b"Save this item", review.data)
        self.assertIn(b"Don't save", review.data)
        self.assertNotEqual(self._task(self._woodview_unit(user, "403")).status, "done")
