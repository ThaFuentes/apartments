# passenger_wsgi.py — apt.poweredby.top
# Loads .env WITHOUT requiring python-dotenv (avoids a blank 500 if dotenv is missing).
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _load_env_file(path):
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
                if key in ("SECRET_KEY", "MYSQL_PASSWORD", "PBT_TOKEN_SECRET", "APT_DATA_KEY") and val:
                    os.environ[key] = val
    except Exception:
        pass


_load_env_file(os.path.join(ROOT, ".env"))

try:
    from app import create_app

    app = create_app()
    application = app
    print("Passenger WSGI loaded successfully - apt.poweredby.top ready")
except Exception:
    err_path = os.path.join(ROOT, "tmp", "wsgi_error.log")
    try:
        os.makedirs(os.path.dirname(err_path), exist_ok=True)
        with open(err_path, "a", encoding="utf-8") as handle:
            handle.write("\n===== WSGI BOOT FAILURE =====\n")
            traceback.print_exc(file=handle)
            handle.write(
                "\nSECRET_KEY set: %s\nMYSQL_DATABASE: %s\n"
                % (
                    "yes" if os.environ.get("SECRET_KEY") else "NO",
                    os.environ.get("MYSQL_DATABASE", ""),
                )
            )
    except Exception:
        pass
    traceback.print_exc()
    raise
