# Production entry is passenger_wsgi.py. HostM / Passenger owns the socket.
# `python main.py` is laptop-only (start_local.sh). Default 8075.

import os
import sys

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from app import create_app

app = create_app()
application = app


def _dev_flag() -> bool:
    return os.getenv("DEBUG_MODE", "").strip().lower() in ("1", "true", "yes", "on")


if __name__ == "__main__":
    if not _dev_flag():
        sys.exit(
            "Refusing to bind a port. Production is passenger_wsgi.py "
            "(HostM chooses the socket). Laptop: DEBUG_MODE=true python main.py"
        )
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8075"))
    print(f"Apt field record  http://{host}:{port}")
    app.run(host=host, port=port, debug=True, use_reloader=False)
