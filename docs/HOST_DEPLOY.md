# Apt — GitHub + HostM deploy

Repo: `https://github.com/ThaFuentes/apartments`  
Host: `/home/ua882038/public_html/apt.poweredby.top`  
Branch: `main`

Operator protocol: paste a PAT in chat. See `AGENTS.md`. Never put a live PAT in this file.

The app uses MariaDB for the field record and for PoweredByTop `pbt_*` tables. There is no SQLite file to copy.

## Laptop push

```bash
cd /home/clarkkent/pyprojects/apt.poweredby.top
git push "https://x-access-token:YOUR_PAT@github.com/ThaFuentes/apartments.git" HEAD:main
```

## First-time HostM

cPanel: subdomain `apt.poweredby.top`, Python app on that document root, MariaDB database. `.env` stays on the host. `SITE_MODE=apt`. Cookie: `pbt_apt_session`.

```bash
mkdir -p /home/ua882038/public_html/apt.poweredby.top
cd /home/ua882038/public_html/apt.poweredby.top
git init
git fetch "https://x-access-token:YOUR_PAT@github.com/ThaFuentes/apartments.git" main
git checkout FETCH_HEAD -- .
mkdir -p tmp uploads logs
touch tmp/restart.txt
```

## Later pulls

```bash
cd /home/ua882038/public_html/apt.poweredby.top
git fetch "https://x-access-token:YOUR_PAT@github.com/ThaFuentes/apartments.git" main
git checkout FETCH_HEAD -- \
  AGENTS.md \
  README.md \
  main.py \
  dbconnector.py \
  requirements.txt \
  app/ \
  poweredbytop/ \
  docs/
mkdir -p tmp uploads logs
touch tmp/restart.txt
```

Do not overwrite `passenger_wsgi.py`, `.htaccess`, `.env`, or `uploads/`.

## Performance limits (2026-11-12 pool incident)

Two of the fixes are host settings, not code. The account shares one Passenger
pool, so one busy site starves the others.

### 1. Cap this app's worker children

cPanel > **Setup Python App** > Edit (apt.poweredby.top) > **Environment
variables** > Add variable `LSAPI_CHILDREN` = `3` > Save > Restart.

LiteSpeed's default for a Python app is 6, but HostM's shared pool tops out at
20 children (`Reached max children process limit: 20` in stderr.log), so the
caps must add up to less than that:

| App    | LSAPI_CHILDREN |
|--------|----------------|
| aegisx | 6              |
| family | 3              |
| apt    | 3              |
| ax     | 3              |
| ahos   | 3              |

Total across all poweredby.top Python apps must stay under 20 (HostM shared
limit). If you add an app, lower another cap. A server-wide default lives in
LiteSpeed WebAdmin > Server > App Server > Python WSGI Default Settings >
Environment. Never raise one app to the whole pool.

Verify on the host:

```bash
tr '\0' '\n' < /proc/<python pid>/environ | grep LSAPI_CHILDREN
```

### 2. Static files must not reach Python (`.htaccess` is host-owned)

Append this above the existing `BEGIN PBT DOCROOT FILE RULE` block. Do not
replace the file:

```apache
<IfModule mod_rewrite.c>
  RewriteEngine On
  RewriteCond %{DOCUMENT_ROOT}/app/static/$1 -f
  RewriteRule ^static/(.+)$ app/static/$1 [L]
</IfModule>
<IfModule mod_headers.c>
  <FilesMatch "\.(?:css|js|mjs|png|jpe?g|gif|webp|svg|ico|woff2?|ttf|otf|eot)$">
    Header set Cache-Control "public, max-age=604800"
  </FilesMatch>
</IfModule>
```

Check: `curl -sI https://apt.poweredby.top/static/css/apt.css` returns
`Cache-Control`, and opening a page leaves no `[STATIC-PY]` line in stderr.log.

### 3. The 5-second DB ceiling

`APT_DB_READ_TIMEOUT` (default 5) is a per-statement socket deadline, not a
page deadline. Keep 5. Raise it only if one of the routes below fails with a
read timeout after stderr.log has shown it as slow.

### 4. Routes that can exceed 5s (audited in code, not measured on the host)

- `GET /reports` - builds the weekly snapshot before the page renders
- `GET /reports/preview.csv`
- `GET /reports/<id>/pdf`
- `GET /reports/<id>/csv`
- `POST /reports/build`, `POST /reports/<id>/send` (email send)
- `GET /map`
- `POST /chat` - holds the worker while the model answers (not a DB wait)

Open each once after deploy and read stderr.log for `[REQ-SLOW]`. Set
`APT_SLOW_MS` to move the threshold (default 5000; `0` logs every request).

Bots and bearer-key callers follow the same one-write rule as a browser: a
second overlapping write waits up to `APT_LANE_WAIT_MS` (default 1500), then
gets `429` + `Retry-After: 2`. A bot client should send sequentially or honour
that header.

## Docroot deny

Apache serves a real file before Flask sees it. Append `docs/docroot-deny.htaccess` when the live file has no `BEGIN PBT DOCROOT FILE RULE` marker. Do not replace `.htaccess`. After `/stderr.log` and `/requirements.txt` return 403, move `stderr.log` and `logs/traffic_log.jsonl` out of `public_html`. Do not print them.
