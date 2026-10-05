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
    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        if os.getenv("DEBUG_MODE", "false").lower() in {"1", "true", "yes"}:
            secret_key = "apt-local-development-only-change-me"
        else:
            raise RuntimeError("SECRET_KEY must be configured before starting Apt outside DEBUG_MODE.")
    app.config["SECRET_KEY"] = secret_key
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
    app.config["SESSION_COOKIE_SAMESITE"] = "Strict"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SECURE"] = os.getenv(
        "SESSION_COOKIE_SECURE",
        "false" if os.getenv("DEBUG_MODE", "false").lower() in {"1", "true", "yes"} else "true",
    ).lower() in {"1", "true", "yes"}
    app.config["SESSION_COOKIE_PATH"] = "/"
    app.config["SESSION_COOKIE_DOMAIN"] = None
    app.config["SESSION_REFRESH_EACH_REQUEST"] = False

    db.init_app(app)
    init_db(app)

    def _local_moment(value):
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
        return value.replace(tzinfo=timezone.utc).astimezone(zone(g.apt_tz))

    @app.template_filter("chat_clock")
    def chat_clock(value):
        if not value:
            return ""
        return _local_moment(value).strftime("%I:%M %p").lstrip("0")

    @app.template_filter("full_state")
    def full_state(value):
        from app.services.geo import state_name

        return state_name(value or "")

    @app.template_filter("apt_when")
    def apt_when(value):
        if not value:
            return ""
        from datetime import date, datetime

        if isinstance(value, datetime):
            text = _local_moment(value).strftime("%b %d, %Y")
        elif isinstance(value, date):
            text = value.strftime("%b %d, %Y")
        else:
            return ""
        return text.replace(" 0", " ")

    @app.template_filter("apt_datetime")
    def apt_datetime(value):
        if not value:
            return ""
        local = _local_moment(value)
        return local.strftime("%b %d, %Y · %I:%M %p").replace(" 0", " ").replace("· 0", "· ")

    from app.guest_access import APT_CSP, open_guest_paths, strip_body_csrf_meta

    @app.after_request
    def _apt_response_tweaks(response):
        response = strip_body_csrf_meta(response)
        ctype = (response.content_type or "").lower()
        if "text/html" in ctype or "application/javascript" in ctype or "application/json" in ctype:
            response.headers["Content-Security-Policy"] = APT_CSP
        if "text/html" in ctype:
            # no-transform stops Cloudflare from injecting the Insights beacon.
            cc = response.headers.get("Cache-Control") or "private"
            if "no-transform" not in cc.lower():
                response.headers["Cache-Control"] = cc.rstrip(", ") + ", no-transform"
        if request.path.startswith("/static/") and request.args.get("v") and response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    open_guest_paths()
    try:
        from poweredbytop import init_security

        init_security(app)
    except Exception as exc:
        print(f"[apt] init_security failed (app still starts): {exc}", flush=True)

    # The wrapper's apply_secure_session_config stamps SESSION_COOKIE_SAMESITE
    # back to "Lax" after create_app() set it. Re-assert Strict here so the
    # Apt session cookie never travels on cross-site requests. Keep this AFTER
    # init_security — moving it above lets the wrapper overwrite it again.
    app.config["SESSION_COOKIE_SAMESITE"] = "Strict"

    login_manager.init_app(app)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    from app.routes.web import bp

    app.register_blueprint(bp)

    @app.before_request
    def _bot_setup_wall():
        if not getattr(current_user, "is_authenticated", False):
            return None
        if not bool(getattr(current_user, "is_bot", False)):
            return None
        from app.services.twofa import bot_setup_path_ok, bot_setup_remaining

        if not bot_setup_remaining(current_user):
            return None
        path = request.path or ""
        if bot_setup_path_ok(path):
            return None
        return redirect("/bot-setup")

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
        if (request.path or "").startswith("/security"):
            from app.services.security_ops import watches_security

            if watches_security(current_user):
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
        pm_n = 0
        ready_n = 0
        saved_property_id = None
        can_pick_property = False
        security_watch = False
        chat_lines = []
        chat_cards = []
        can_manage_people = False
        can_manage_regions = False
        property_choices = []
        header_property_id = None
        default_property_id = None
        if getattr(current_user, "is_authenticated", False):
            from app.models import Notice, PendingAction
            from app.services.records import open_shift
            from app.services.security_ops import watches_security
            from app.services.share import share_state

            security_watch = watches_security(current_user)

            shift = open_shift(current_user)
            if shift and shift.property:
                place = shift.property.name
                city = shift.property.city.name if shift.property.city else ""
                confirmed = bool(shift.confirmed)
                header_property_id = shift.property.id
            try:
                from app.services.access.management import can_choose_own_default_property
                from app.services.context import current_property, property_picker, remembered_property

                property_choices, default_property_id = property_picker(current_user)
                focus = remembered_property(current_user)
                saved_property_id = focus.id if focus else None
                can_pick_property = can_choose_own_default_property(current_user) and current_user.role != "viewer"
                here = current_property(current_user)
                if here and not place:
                    place = here.name
                    city = here.city.name if here.city else city
                    header_property_id = here.id
                    confirmed = True
            except Exception:
                db.session.rollback()
            if not city:
                from app.services.records import site_profile

                profile = site_profile()
                city = (profile.default_city if profile else "") or city
            from app.services.access.management import can_open_people_page, can_manage_regions as _can_manage_regions

            can_manage_people = can_open_people_page(current_user)
            can_manage_regions = _can_manage_regions(current_user)
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
            try:
                from app.services.ready import ready_open_count
                from app.services.upkeep import pm_count

                pm_n = pm_count(current_user)
                ready_n = ready_open_count(current_user)
            except Exception:
                db.session.rollback()
                pm_n = 0
                ready_n = 0
            if getattr(current_user, "role", "") != "viewer":
                from app.models import ChatMessage
                from app.services.records import loads as _loads

                from app.services.pending_cards import sweep_finished_cards

                sweep_finished_cards(current_user)
                chat_lines = (
                    ChatMessage.query.filter_by(user_id=current_user.id)
                    .order_by(ChatMessage.id.desc())
                    .limit(80)
                    .all()
                )
                chat_lines.reverse()
                cards = (
                    PendingAction.query.filter_by(user_id=current_user.id)
                    .filter(PendingAction.status.in_(("pending", "needs_answer")))
                    .order_by(PendingAction.id.desc())
                    .limit(30)
                    .all()
                )
                cards.reverse()
                open_heads = {
                    (row.summary or "").splitlines()[0].strip()
                    for row in cards
                    if (row.summary or "").strip()
                }
                chat_lines = [
                    msg
                    for msg in chat_lines
                    if not (
                        msg.role == "assistant"
                        and "Not saved yet" in (msg.body or "")
                        and not any(head and head in (msg.body or "") for head in open_heads)
                    )
                ]
                for row in cards:
                    payload = _loads(row.payload_json)
                    parts = [line.strip() for line in (row.summary or "").splitlines() if line.strip()]
                    place_line = ""
                    pid = payload.get("property_id")
                    name = (payload.get("property_name") or "").strip()
                    city_name = (payload.get("city") or "").strip()
                    if pid:
                        from app.models import Property as _Property
                        from app.services.records import property_place as _place

                        prop = db.session.get(_Property, int(pid))
                        if prop:
                            place_line = _place(prop)
                    if not place_line and name:
                        place_line = f"{name} in {city_name}" if city_name else name
                    if not place_line:
                        for change in payload.get("_changes") or []:
                            field = (change.get("field") or "").lower()
                            if field in {"property", "site"} and change.get("after"):
                                place_line = str(change.get("after"))
                                break
                    chat_cards.append(
                        {
                            "id": row.id,
                            "tool": row.tool,
                            "status": row.status,
                            "headline": parts[0] if parts else row.tool,
                            "note": parts[-1] if len(parts) > 1 else "",
                            "changes": payload.get("_changes") or [],
                            "waiting_for": payload.get("waiting_for") or "",
                            "payload": payload,
                            "place": place_line,
                        }
                    )
            else:
                chat_lines = []
        import secrets as _secrets

        from app.assets import ASSET_V

        chat_key = _secrets.token_hex(8)
        assistant_name = "Apt"
        try:
            from app.services.ai_voice import spoken_name

            who = current_user if getattr(current_user, "is_authenticated", False) else None
            assistant_name = spoken_name(who)
        except Exception:
            db.session.rollback()
            assistant_name = "Apt"
        return {
            "csrf_token": token,
            "SITE_MODE": "apt",
            "SITE_NAME": "Apartments",
            "header_city": city or "City",
            "header_property": place or "Property",
            "header_property_id": header_property_id or default_property_id,
            "default_property_id": default_property_id,
            "saved_property_id": saved_property_id,
            "can_pick_property": can_pick_property,
            "security_watch": security_watch,
            "header_confirmed": confirmed,
            "property_choices": property_choices,
            "share": share,
            "notices": notices,
            "pending_n": pending_n,
            "pm_n": pm_n,
            "ready_n": ready_n,
            "chat_lines": chat_lines,
            "chat_cards": chat_cards,
            "chat_key": chat_key,
            "can_manage_people": can_manage_people,
            "can_manage_regions": can_manage_regions,
            "assistant_name": assistant_name,
            "drive": bool(request.cookies.get("apt_drive") == "1"),
            "asset_v": ASSET_V,
        }

    @app.route("/healthz")
    def healthz():
        from sqlalchemy import text

        db.session.execute(text("SELECT 1"))
        return {"ok": True, "site": "apt", "db": "mariadb"}, 200

    @app.route("/robots.txt")
    def robots():
        from flask import Response

        return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")

    @app.errorhandler(404)
    def _missing(_e):
        from flask import render_template

        return render_template("error.html", code=404, message="That page is not in the record."), 404

    @app.errorhandler(403)
    def _denied(_e):
        from flask import render_template

        from app.guest_access import is_guest_ok

        if getattr(current_user, "is_authenticated", False):
            return render_template("error.html", code=403, message="That action is not allowed for this login."), 403
        if request.endpoint and not is_guest_ok(request.path or ""):
            from app.auth import return_path

            return redirect(url_for("desk.login", next=return_path()))
        return render_template("error.html", code=404, message="That page is not in the record."), 404

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
