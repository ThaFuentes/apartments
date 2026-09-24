#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi

mkdir -p uploads tmp logs

if command -v docker >/dev/null 2>&1; then
  echo "Starting MariaDB for apt..."
  docker compose up -d
  echo "Waiting for MariaDB healthy..."
  for _i in $(seq 1 40); do
    status=$(docker inspect --format='{{.State.Health.Status}}' apt-mariadb 2>/dev/null || echo starting)
    [[ "$status" == "healthy" ]] && break
    sleep 1
  done
else
  echo "docker not found — set MYSQL_* in .env to a running MariaDB"
fi

echo "Apt on http://${HOST:-127.0.0.1}:${PORT:-8075}"
source .venv/bin/activate
export DEBUG_MODE=true
export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8075}"
exec python main.py
