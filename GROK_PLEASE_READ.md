# Grok Please Read — parse/resolve layer rebuild

**Stop. The rule for chat is `AI_READ_THIS.md`.** Grok leads this project.
A saved key answers first. Local chat runs only after every key fails.
The notes below describe the shared property resolver. They do not mean local
parsers should run before the model, and they do not authorize another agent
to put that order back.

Written by **Freebuff** (the agent that did the resolver work). Read this before touching
`app/services/parse.py`, `app/services/talk/` (the chat package, one file per job, each under 800 lines), or `app/services/providers.py`.
Everything below describes work already in the working tree, uncommitted, on this branch.

---

## 1. What was asked for

Make the chat agent able to parse/add/remove/resolve **anything the user says** —
with **or without** an AI model. The app must work great with no keys at all, and the
model (Gemini/Groq/OpenAI/Grok/Claude BYOK) should use the same routes more coherently.
The owner's stated pain point: **wrong saves** — things saved to the wrong property,
unit, or with wrong slots. Agreed behavior for ambiguity: two properties named the
same → prompt in the chat with the options, or ask which city/state.

## 2. The design (read this twice)

One shared layer, two consumers. **Nothing writes without resolving here first.**

```
app/services/parse.py   ← NEW. The shared brain:
  - resolve_property(hint, city, region) -> {state: resolved|ambiguous|unknown, ...}
  - resolve_or_lines(hint, city) -> (Property | None, question_to_ask)
  - resolve_unit(prop, number)
  - validate_call(tool, args) -> {ok, args, errors}   # repairs model tool args
  - catalog_lines() / parse_rules()                    # the address book for prompts
  - answer_bare_reply(user, text)                      # "odessa" answers "Which Woodview?"
```

- **Local path** (no key, no quota, works offline): `talk.py` regex parsers extract
  slots, `resolve_property` names the exact saved property or asks a city-grouped
  question. First-match-wins guessing is gone at the disambiguation sites.
- **AI path**: the same address book goes into the model prompt (`providers.py`
  injects `parse_rules()` + `catalog_lines()`), and every `plan_trip` /
  `upsert_property` tool call passes through `_model_place_args()` (talk.py) →
  `validate_call()` + `resolve_property` before `commit_apply`. If a saved name is
  ambiguous, a model-selected city is discarded unless the user said that city;
  the chat asks for the missing choice before the trip/property write.

## 3. Files changed

| File | Change |
| --- | --- |
| `app/services/parse.py` | **NEW** (~430 lines). Address book, resolver, validator, bare-reply router. |
| `app/services/talk/` | Same chat behavior, split into a package. Entry is `turn.py` (`handle_message`, `route`). Place sentences are `places.py`, trips `outings.py`, plans `plans.py`, units `units.py`, model tools `model_calls.py`, record answers `records.py`. |
| `app/services/providers.py` | Imports from parse.py; `record_brief()` now leads with the city-grouped catalog; `collect_tool_calls` prompt includes `parse_rules()`. |
| `tests/test_apt.py` | 5 new tests (see §6). |
| `GROK_PLEASE_READ.md` | This file. |

Untouched on purpose: `appliers.py` writers, `pending.py` propose/confirm flow,
`records.py` helpers (still used elsewhere), models, templates.

## 4. The bare-reply answer router (the big UX win)

`route()` in talk.py now calls `answer_bare_reply(user, text)` **before anything
else**. If the newest `PendingAction` in `needs_answer` status is a place/unit
question and her message is a short bare answer, it fills the card and `_finish_bare`
completes it through the card's own flow.

A "bare answer" is, roughly: 1–4 words, no action verbs, not a confirmation
("yes, save it" is NEVER eaten — there are explicit guards: `CONFIRM_START` and
`ACTION_WORDS`). Digits are accepted only when the newest matching card explicitly
asks for a unit; odometer readings and numbers in place answers still go through the
normal flow. The router uses a resolution ladder: try her word as the property under
the card's known city → try it as the missing city for the name already on the card
→ try it standalone → only then fall back to the normal chat flow.

Example end-to-end (verified by test):
`plan woodview thursday for ac evals` → "Which Woodview Apartments? Say the city:
Lubbock, Odessa." → `odessa` → "That's a trip to Woodview Apartments in Odessa on
Thursday for ac evals." (trip saved, correct city).

## 5. Real bugs found and fixed along the way

1. **`_plan_slots` purpose-eats-destination (pre-existing wrong-save bug).**
   `plan woodview thursday for ac evals` matched `plan ... for X` with X = "ac evals"
   and built property **Ac** in city **Evals**. Fixed: a new `plan <rest>` pattern
   takes the whole tail as the destination, with `_tail_is_purpose()` refusing pure
   purposes ("for fixing the clogs", "to fix the clogs", "friday") but accepting
   destinations that carry a purpose suffix ("woodview odessa to replace an ac" —
   the downstream purpose split handles the suffix). The old fallback pattern is
   guarded the same way.
2. **Model glue ("woodview in odessa texas" pasted into `city`).** `_repair_place_words`
   splits glued slots against the saved city list; `validate_call` also rejects
   name/city values containing prepositions ("in", "at", "and", …) or more than 4 words.
3. **Dollars-as-cents from models**: `amount_cents` between 0 and 3000 is treated as
   dollars and multiplied (with an error note). Odometer strings coerce to ints.
4. **`_file_outing` near-miss duplicate creation**: if a card holds name+city and the
   pair doesn't resolve, the parser question flows instead of `ensure_property`
   guessing a near-miss name into a second property.

## 6. Regression tests

`tests/test_apt.py` covers the repaired cases:

- `test_local_ambiguous_plan_asks_for_city_and_bare_city_completes_it`
- `test_model_plan_cannot_pick_a_city_for_an_ambiguous_property`
- `test_property_resolution_reports_equal_matches_in_one_city`
- `test_bare_unit_answer_completes_open_unit_visit`
- `test_place_answer_for_unit_card_confirms_property_then_saves_without_name_error`

Verified on 2026-09-26 with `.venv/bin/python -m unittest tests.test_apt`:
**62 tests passed**. `compileall` also passed for the parser, chat flow, and test file.

## 7. Things I deliberately did NOT do (and why)

- Did not replace `fuzzy_properties` inside `records.py`/`access.py`/`board.py`
  call sites (screen-side code with different UX constraints). The four chat-side
  disambiguation sites use the new resolver; migrating the screens is a follow-up.
- Did not touch `appliers.py` (the writers) — the resolver sits strictly upstream.
- Did not "fix" `test_gas_lands_on_company_report` — it is pre-existing and touches
  `reports.period_for` semantics the owner should rule on (week starts Monday?).
- Did not commit anything. Tree is dirty on purpose; owner deploys via HostM
  workflow in AGENTS.md.

## 8. Suggested next steps for Grok

1. Run the suite yourself: `.venv/bin/python -m unittest tests.test_apt`.
2. Re-read `_tail_is_purpose` + the two `plan` destination patterns in `_plan_slots`
   (talk.py ~line 678) — that parser is the most delicate thing in this change.
3. Migrate `board.resolve_property` and `access.grant_from_words` to
   `parse.resolve_or_lines` when you touch screens next.
4. Consider making `chat_history` stop growing unbounded (pre-existing, unrelated).

— Freebuff 🤖
