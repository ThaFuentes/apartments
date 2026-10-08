# AI read this before you touch Apt chat

Grok leads this project. If you are Freebuff, Manicode, Luna, or any other agent, you are not the architect. Do not invert the chat order. Do not replace `app/services/talk/` with one giant script. Do not "finish" local chat by making it handle every sentence before the model is asked.

The operator said this in plain words: if there is an AI, have it take the sentence and run with it. If there is no AI, use local chat. Local chat is the backup for a missing key, a key that is down, a key that is out of quota, or a key that returns nothing. It is not a second brain that runs first.

## The order

`handle_message` in `app/services/talk/turn.py` calls `route`.

1. **Card answers stay local, and they stay first.** "Yes", "yes, save it", "no", and a bare answer such as "odessa" or a unit number close a card this app already opened. They are not a new job. Sending them to a model first burns a key and often returns prose without pressing the confirm button, so the card sits there forever. Leave these in front of the model.

2. **Then the saved keys.** `_from_model` calls `collect_tool_calls` in `app/services/providers.py`. A person who picked their own keys uses that list. Everyone else uses the owner's keys in `use_order`, then any key a teammate marked shared. The first real answer wins. A tool call is the action. The reply in the thread is what happened. Platform rules (no flirting, no sexual talk) are always in the prompt. A person can change the assistant's name and add notes. They cannot turn those rules off.

3. **Local chat is the backup.** `_local_fallback` runs only when there is no key, every key is cooling down, out of quota, errors, or returns nothing (`_from_model` returns `None` or `{"failed": True}`). Local chat must still file a trip, a plan, a property, a unit, an address, gear, and a record question. She has to be able to work with the keys off.

Do not move trip parsers, plan parsers, property parsers, gear parsers, or record questions back above `_from_model`. That was the bug. A clear sentence was finished locally and the model was never asked, including when a key was sitting right there.

Known field sentences are the exception, and they already run inside `_from_model` before a key is spent: office, a vendor on a make-ready, contractor check-in, and `_local_exact`. Inside that local path, a unit, a make-ready, and one trade are not a trip. The plan matcher runs only after the unit board, and only when the sentence is a trip or a day of stops. The word plan or schedule does not make unit work a trip. The model is for a sentence no path recognized, and it uses those same tools.

## What the model is for

When a key works, the model decides the action and calls the tool: plan, trip, property, unit, work, expense, address, question. `parse.py` is the checker on those tool arguments. It resolves a property, asks which city when two places share a name, and rejects a slot that would save the wrong site. It is not a second brain that replaces the model.

`parse_rules()` and `catalog_lines()` go into the model prompt so the model and the checker use the same address book.

## Address sentences

An address edit must not create a property.

- The model is still asked.
- If it returns `upsert_property` or `update_property` and her sentence already contains the street, the server keeps her street and the property name parsed from her sentence, then applies `update_property`. The model's glued-together name does not win.
- If it returns a place tool and she did not type a street, that call is dropped and local chat asks which street to save. It does not create a property from the address request.
- "What's the address for Brookview, find it on Google" is a lookup, not an edit. It is not an address-update target.

## File shape

Python files stay under 800 lines. Chat is the package `app/services/talk/`, one file per job:

| File | Job |
|---|---|
| `turn.py` | One message: card answer, then the model, then local backup |
| `model_calls.py` | What a key returned, and the address-tool repair |
| `interpret.py` | Local reading of a sentence once the keys have failed |
| `places.py` | Property names, streets, edits |
| `plans.py` | Plan sentences |
| `outings.py` | Trips, miles, arrivals |
| `units.py` | Units, buildings, gear |
| `records.py` | Dated work and record answers |
| `staff.py` | Logins and access |
| `phrases.py` | Words and patterns |
| `textutil.py` | Small text helpers |

`app/services/parse.py` stays the shared resolver. Do not fold it back into `turn.py`.

## Tests

`tests/test_apt.py` wipes the local apt MariaDB. Run it from this repo with `MYSQL_*`, `DATABASE_URI`, and `SECRET_KEY` unset.

A test for a sentence with no key, or with the key patched down, must still pass on local chat. A test that used to assert the model was never called for a normal sentence is wrong under this rule. The model is called. The safety checks are about the result: the right property, no extra property, the street she typed.

## Do not

- Do not call `write_file` on a chat module with an empty body. That deleted `talk.py` once.
- Do not `git pull` or checkout the whole tree to "restore" one file. Other uncommitted work is in this repo on purpose.
- Do not commit unless the operator asks or pastes a `github_pat_`.
