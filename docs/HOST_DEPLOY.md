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
