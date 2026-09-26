# AGENTS.md — apt.poweredby.top

Field record for one regional manager. She talks and snaps photos. The system keeps properties, visited units, jobs, miles, expenses, and company reports.

GitHub: `ThaFuentes/apartments`  
Host: `/home/ua882038/public_html/apt.poweredby.top`  
Branch: `main`

## Who leads, and how chat works

Grok leads this project. Another agent does not get to re-decide the architecture.

Read `AI_READ_THIS.md` before touching chat. Short version: a saved AI key answers first. Local chat is real, and it runs only after every key is missing, down, out of quota, or returns nothing. Do not put sentence parsers back in front of the model.

## What this product is

- MariaDB only. Product tables and PoweredByTop `pbt_*` tables use `MYSQL_*`. Do not add SQLite.
- Logins are username + password. Email is optional on every user, including bosses. Blank email is stored as NULL.
- Weekly reports and company reports are part of the product. Bosses read them in the app. Email sends only when that person has an address and SMTP is set.
- Chat can do the work. Screens are for review and one-thumb taps. Material writes wait for a yes.
- Track visited unit numbers only. Do not map floors, buildings, or odd/even layouts.
- `SITE_MODE=apt`. Session cookie `pbt_apt_session`.

## PAT in chat = push + HostM commands

If the operator pastes a `github_pat_…`, that is the deploy request.

1. Commit the intended files.
2. `git push` with a one-off URL. Never `git remote set-url`. Never write the token into a file.
3. Reply with HostM `git fetch` + checkout + `touch tmp/restart.txt`.

Do not overwrite host-only files: `passenger_wsgi.py`, `.htaccess`, `.env`, `uploads/`.

Laptop: `./start_local.sh` → http://127.0.0.1:8075  
MariaDB on the laptop: `127.0.0.1:3314`, database `apt`.

## HARD RULE: never force ASCII on logs

```python
# BANNED
str(msg).encode("ascii", "replace").decode("ascii")
```

UTF-8 only.

```python
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
print(f"[apt] init_security failed: {exc}", flush=True)
```

## Tests

```bash
cd /home/clarkkent/pyprojects/apt.poweredby.top
.venv/bin/python -m unittest tests.test_apt -v
```

Tests talk to MariaDB. They wipe apt product tables. Do not point `MYSQL_*` at a database you need to keep.
