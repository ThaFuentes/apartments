import os

from flask import Flask, abort, jsonify, redirect, request, url_for
from flask_login import current_user
from werkzeug.middleware.proxy_fix import ProxyFix

from app.auth import login_manager
from app.builddb.builddb import db, init_db
from app.services.files import ensure_dirs


def create_app() -> Flask:
    from dotenv import load_dotenv

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(root, ".env"))
    ensure_dirs()

    from dbconnector import DATABASE_URI

    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["SITE_MODE"] = "apt"
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY") or "apt-dev-key"
    app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URI
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "pool_recycle": 50,
        "pool_size": 2,
        "max_overflow": 5,
        "pool_timeout": 45,
        "pool_reset_on_return": "rollback",
        "connect_args": {
            "charset": "utf8mb4",
            "connect_timeout": 20,
            "read_timeout": 45,
            "write_timeout": 45,
        },
    }
    app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("UPLOAD_MAX_BYTES") or str(16 * 1024 * 1024))
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.config["PREFERRED_URL_SCHEME"] = os.getenv("PREFERRED_URL_SCHEME") or "https"
    app.config["SESSION_COOKIE_NAME"] = "pbt_apt_session"
    app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 14
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    db.init_app(app)
    init_db(app)

    @app.template_filter("chat_clock")
    def chat_clock(value):
        if not value:
            return ""
        from datetime import timezone

        from flask import g

        from app.services.clock import zone

        if not getattr(g, "apt_tz", None):
            try:
                from app.services.records import site_profile

                profile = site_profile()
                g.apt_tz = (profile.timezone if profile else "") or "America/Chicago"
            except Exception:
                g.apt_tz = "America/Chicago"
        local = value.replace(tzinfo=timezone.utc).astimezone(zone(g.apt_tz))
        return local.strftime("%I:%M %p").lstrip("0")

    try:
        from poweredbytop import init_security

        init_security(app)
    except Exception as exc:
        print(f"[apt] init_security failed (app still starts): {exc}", flush=True)

    login_manager.init_app(app)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    from app.routes.web import bp

    app.register_blueprint(bp)

    @app.before_request
    def _viewer_cannot_mutate():
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return None
        if not getattr(current_user, "is_authenticated", False):
            return None
        if getattr(current_user, "role", "") != "viewer":
            return None
        if request.path in ("/logout",):
            return None
        if request.path.startswith("/api/") or "application/json" in (request.headers.get("Accept") or ""):
            return jsonify({"ok": False, "error": "Viewers cannot change the record."}), 403
        abort(403)

    @app.before_request
    def _no_offline_money():
        if request.headers.get("X-Apt-Offline-Queue") != "1":
            return None
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return None
        path = request.path or ""
        if path.startswith("/api/ping") or path.startswith("/api/expense") or path.endswith("/finalize"):
            return jsonify({"ok": False, "error": "That has to wait for a live connection."}), 409
        return None

    @app.before_request
    def _share_and_notices():
        if not getattr(current_user, "is_authenticated", False):
            return None
        try:
            from app.services.share import enforce_share

            enforce_share(current_user)
        except Exception:
            db.session.rollback()
        if request.method == "GET" and not request.path.startswith("/static/"):
            try:
                from app.services.notices import refresh_notices

                refresh_notices(current_user)
            except Exception:
                db.session.rollback()
        return None

    @app.context_processor
    def _inject():
        token = ""
        try:
            from poweredbytop.security.csrf import generate_csrf_token

            token = generate_csrf_token()
        except Exception:
            token = ""
        city = ""
        place = ""
        confirmed = False
        share = {"on": False, "viewers": 0, "fresh": False}
        notices = []
        pending_n = 0
        chat_lines = []
        if getattr(current_user, "is_authenticated", False):
            from app.models import Notice, PendingAction
            from app.services.records import open_shift
            from app.services.share import share_state

            shift = open_shift(current_user)
            if shift and shift.property:
                place = shift.property.name
                city = shift.property.city.name if shift.property.city else ""
                confirmed = bool(shift.confirmed)
            if not city:
                from app.services.records import site_profile

                profile = site_profile()
                city = (profile.default_city if profile else "") or city
            try:
                share = share_state(current_user)
            except Exception:
                db.session.rollback()
            notices = (
                Notice.query.filter_by(user_id=current_user.id, read_at=None)
                .order_by(Notice.id.desc())
                .limit(4)
                .all()
            )
            pending_n = PendingAction.query.filter_by(user_id=current_user.id, status="pending").count()
            if getattr(current_user, "role", "") != "viewer":
                from app.models import ChatMessage

                chat_lines = (
                    ChatMessage.query.filter_by(user_id=current_user.id)
                    .order_by(ChatMessage.id.desc())
                    .limit(80)
                    .all()
                )
                chat_lines.reverse()
            else:
                chat_lines = []
        import secrets as _secrets

        chat_key = _secrets.token_hex(8)
        return {
            "csrf_token": token,
            "SITE_MODE": "apt",
            "SITE_NAME": "Apt",
            "header_city": city or "City",
            "header_property": place or "Property",
            "header_confirmed": confirmed,
            "share": share,
            "notices": notices,
            "pending_n": pending_n,
            "chat_lines": chat_lines,
            "chat_key": chat_key,
            "drive": bool(request.cookies.get("apt_drive") == "1"),
        }

    @app.route("/healthz")
    def healthz():
        from sqlalchemy import text

        db.session.execute(text("SELECT 1"))
        return {"ok": True, "site": "apt", "db": "mariadb"}, 200

    @app.errorhandler(404)
    def _missing(_e):
        from flask import render_template

        return render_template("error.html", code=404, message="That page is not in the record."), 404

    @app.errorhandler(403)
    def _denied(_e):
        from flask import render_template

        return render_template("error.html", code=403, message="That action is not allowed for this login."), 403

    @app.route("/sw.js")
    def sw():
        from flask import send_from_directory

        resp = send_from_directory(app.static_folder, "sw.js")
        resp.headers["Content-Type"] = "application/javascript"
        resp.headers["Service-Worker-Allowed"] = "/"
        return resp

    @app.route("/manifest.webmanifest")
    def manifest():
        from flask import send_from_directory

        resp = send_from_directory(app.static_folder, "manifest.webmanifest")
        resp.headers["Content-Type"] = "application/manifest+json"
        return resp

    return app
