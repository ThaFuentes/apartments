# Threat-map queries. Imported only from threat_map.py (page-scoped).
from __future__ import annotations

import time
from typing import Any

from app.services import security_queries as q
from app.services.threat_geo import ensure_ip_geo_table, fill_missing_ips
from app.services.security_queries import _fmt_ts

WINDOWS = {
    "live": "4 HOUR",
    "4h": "4 HOUR",
    "24h": "1 DAY",
    "7d": "7 DAY",
    "30d": "30 DAY",
}

# All-time totals (no INTERVAL). Aliases accepted from the country drawer.
LIFETIME_WINDOWS = frozenset({"lifetime", "all", "history"})


def _norm_window(window: str) -> str:
    key = (window or "24h").strip().lower()
    if key in LIFETIME_WINDOWS:
        return "lifetime"
    if key in WINDOWS:
        return key
    return "24h"


def _window_sql(window: str) -> str:
    key = _norm_window(window)
    if key == "lifetime":
        return ""
    return WINDOWS.get(key, WINDOWS["24h"])


def _time_sql(window: str) -> str:
    """created_at filter, or 1=1 for lifetime / history."""
    interval = _window_sql(window)
    if not interval:
        return "1=1"
    return f"e.created_at >= NOW() - INTERVAL {interval}"


def _skip_sql() -> tuple[str, list]:
    """Drop pass/noise rows only. Every real threat type stays on the map."""
    types = list(q._SKIP_EVENT_TYPES)
    if types:
        ph = ",".join(["%s"] * len(types))
        clause = f"(e.event_type IS NULL OR e.event_type NOT IN ({ph}))"
        params = list(types)
    else:
        clause = "1=1"
        params = []
    return clause, params


def _family(raw: str | None) -> str:
    return q._canonical_attack_type(raw) or "other"


def _safe_username(raw: Any) -> str:
    s = "".join(ch for ch in str(raw or "") if ch.isprintable()).strip()
    return s[:40]


def _usernames_by_id(uids: set[int]) -> dict[int, str]:
    if not uids:
        return {}
    try:
        from app.models import User

        out: dict[int, str] = {}
        for u in User.query.filter(User.id.in_(list(uids))).all():
            name = _safe_username(getattr(u, "username", None))
            if name:
                out[int(u.id)] = name
        return out
    except Exception as exc:
        print(f"[threat-map] username lookup: {exc}")
        return {}


def _uids_by_ip(ips: list[str]) -> dict[str, int]:
    uniq = []
    seen: set[str] = set()
    for ip in ips:
        ip = (ip or "").strip()
        if not ip or ip in seen:
            continue
        seen.add(ip)
        uniq.append(ip)
    if not uniq:
        return {}
    return {}


def _fp_from_notes(notes: str | None) -> str:
    if not notes:
        return ""
    import re

    m = re.search(r"\bfp=([a-fA-F0-9]{10,40})\b", str(notes))
    return (m.group(1) if m else "")[:40]


def _uids_by_fp(fps: list[str]) -> dict[str, int]:
    """device_fp → registered user_id from pbt_device_prints (security DB)."""
    return q._user_ids_by_device_fp(fps)


def _unique_uids_by_ip(ips: list[str]) -> dict[str, int]:
    """IP → user_id only when exactly one registered user is on that IP."""
    by_ip = _uids_by_ip(ips)
    if not by_ip:
        return {}
    try:
        from app.models import User as UserIpSighting  # unused fallback

        counts: dict[str, set[int]] = {}
        rows = UserIpSighting.query.filter(
            UserIpSighting.ip_address.in_(list(by_ip.keys())[:200])
        ).all()
        for r in rows:
            ip = (r.ip_address or "").strip()
            uid = int(r.user_id or 0)
            if not ip or not uid:
                continue
            counts.setdefault(ip, set()).add(uid)
        return {ip: uid for ip, uid in by_ip.items() if len(counts.get(ip) or ()) == 1}
    except Exception:
        return by_ip


def _attach_usernames(rows: list[dict], *, keep_ip: bool = False) -> None:
    """Same identity the Events log shows: event user_id, then device, then unique IP."""
    uids: set[int] = set()
    fps: list[str] = []
    ips: list[str] = []
    for r in rows:
        try:
            uid = int(r.get("user_id") or 0)
        except (TypeError, ValueError):
            uid = 0
        r["user_id"] = uid or None
        if uid:
            uids.add(uid)
        fp = (r.get("device_fp") or "").strip() or _fp_from_notes(r.get("notes"))
        if fp:
            r["device_fp"] = fp
            fps.append(fp)
        ip = (r.get("ip") or "").strip()
        if ip:
            ips.append(ip)
    by_fp = _uids_by_fp(fps)
    by_ip = _unique_uids_by_ip(ips)
    for r in rows:
        uid = r.get("user_id")
        if not uid:
            uid = by_fp.get((r.get("device_fp") or "").strip()) or by_ip.get(
                (r.get("ip") or "").strip()
            )
            r["user_id"] = uid or None
        if uid:
            uids.add(int(uid))
    names = _usernames_by_id(uids)
    for r in rows:
        uid = r.get("user_id")
        r["username"] = names.get(int(uid)) if uid else None
        r.pop("notes", None)
        r.pop("device_fp", None)
        if not keep_ip:
            r.pop("ip", None)


def countries_for_window(window: str = "24h", *, fill: bool = True) -> dict[str, Any]:
    t0 = time.monotonic()
    window = _norm_window(window)
    time_sql = _time_sql(window)
    skip_sql, skip_params = _skip_sql()
    conn = q._sec()
    empty = {
        "countries": [],
        "unresolved_ips": 0,
        "private_events": 0,
        "filled": 0,
        "window": window,
        "q_ms": 0,
        "geo_ready": False,
    }
    if conn is None:
        return empty
    filled = 0
    try:
        cur = conn.cursor()
        ensure_ip_geo_table(conn)
        # Lifetime / live: don't walk the whole table just to fill geo.
        if fill and window not in ("live", "lifetime"):
            try:
                cur.execute(
                    f"""
                    SELECT DISTINCT e.ip
                    FROM pbt_security_events e
                    LEFT JOIN pbt_ip_geo g ON g.ip = e.ip
                    WHERE {time_sql}
                      AND e.ip IS NOT NULL AND e.ip != ''
                      AND {skip_sql}
                      AND (g.ip IS NULL OR g.resolved_at < NOW() - INTERVAL 90 DAY)
                    LIMIT 200
                    """,
                    skip_params,
                )
                missing = [(r.get("ip") if isinstance(r, dict) else r[0]) for r in (cur.fetchall() or [])]
                filled = fill_missing_ips(conn, missing, limit=200)
            except Exception as exc:
                print(f"[threat-map] fill missing: {exc}")
                try:
                    conn.rollback()
                except Exception:
                    pass

        cur.execute(
            f"""
            SELECT COALESCE(g.country_iso2, 'XX') AS cc,
                   e.event_type,
                   COUNT(*) AS c,
                   COUNT(DISTINCT e.ip) AS ips,
                   MAX(e.created_at) AS last_seen
            FROM pbt_security_events e
            LEFT JOIN pbt_ip_geo g ON g.ip = e.ip
            WHERE {time_sql}
              AND e.ip IS NOT NULL AND e.ip != ''
              AND {skip_sql}
            GROUP BY cc, e.event_type
            """,
            skip_params,
        )
        rows = list(cur.fetchall() or [])
        by_cc: dict[str, dict] = {}
        private = 0
        unresolved = 0
        for r in rows:
            cc = (r.get("cc") or "XX").upper()
            fam = _family(r.get("event_type"))
            if not fam:
                continue
            cnt = int(r.get("c") or 0)
            if cc == "ZZ":
                private += cnt
            if cc == "XX":
                unresolved += int(r.get("ips") or 0)
            bucket = by_cc.setdefault(
                cc,
                {
                    "iso2": cc,
                    "count": 0,
                    "unique_ips": 0,
                    "last_seen": None,
                    "families": {},
                    "top_family": "other",
                },
            )
            bucket["count"] += cnt
            bucket["unique_ips"] += int(r.get("ips") or 0)
            bucket["families"][fam] = bucket["families"].get(fam, 0) + cnt
            last = r.get("last_seen")
            if last and (bucket["last_seen"] is None or last > bucket["last_seen"]):
                bucket["last_seen"] = last
        countries = []
        for cc, b in by_cc.items():
            if b["families"]:
                b["top_family"] = max(b["families"], key=b["families"].get)
            if b["last_seen"] is not None:
                b["last_seen"] = _fmt_ts(b["last_seen"])
            countries.append(b)
        countries.sort(key=lambda x: x["count"], reverse=True)
        empty.update(
            {
                "countries": countries,
                "unresolved_ips": unresolved,
                "private_events": private,
                "filled": filled,
                "geo_ready": True,
                "q_ms": int((time.monotonic() - t0) * 1000),
            }
        )
        return empty
    except Exception as exc:
        print(f"[threat-map] countries_for_window: {exc}")
        empty["q_ms"] = int((time.monotonic() - t0) * 1000)
        return empty
    finally:
        q._close(conn)


_LIFE_CACHE: dict[str, Any] = {"at": 0.0, "n": None}


def _lifetime_event_count(cur, skip_sql: str, skip_params: list) -> int | None:
    """Cheap all-time total. Prefer pbt_attack_stats — never scan the events table here."""
    now = time.monotonic()
    cached = _LIFE_CACHE.get("n")
    if cached is not None and (now - float(_LIFE_CACHE.get("at") or 0)) < 180:
        return int(cached)
    try:
        cur.execute("SELECT COALESCE(SUM(total_attempts), 0) AS c FROM pbt_attack_stats")
        row = cur.fetchone() or {}
        n = int(row.get("c") or 0)
        if n > 0:
            _LIFE_CACHE["at"] = now
            _LIFE_CACHE["n"] = n
            return n
    except Exception as exc:
        print(f"[threat-map] lifetime total: {exc}")
    return cached if cached is not None else None


def summary_for_window(window: str = "24h") -> dict[str, Any]:
    t0 = time.monotonic()
    window = _norm_window(window)
    time_sql = _time_sql(window)
    skip_sql, skip_params = _skip_sql()
    conn = q._sec()
    out = {
        "window": window,
        "total": 0,
        "unique_ips": 0,
        "unique_countries": 0,
        "families": [],
        "private_events": 0,
        "unresolved_events": 0,
        "lifetime_total": None,
        "recent": [],
        "q_ms": 0,
    }
    if conn is None:
        return out
    try:
        cur = conn.cursor()
        out["lifetime_total"] = _lifetime_event_count(cur, skip_sql, skip_params)
        cur.execute(
            f"""
            SELECT COUNT(*) AS c,
                   COUNT(DISTINCT e.ip) AS ips,
                   COUNT(DISTINCT COALESCE(g.country_iso2, 'XX')) AS ccs
            FROM pbt_security_events e
            LEFT JOIN pbt_ip_geo g ON g.ip = e.ip
            WHERE {time_sql}
              AND e.ip IS NOT NULL AND e.ip != ''
              AND {skip_sql}
            """,
            skip_params,
        )
        row = cur.fetchone() or {}
        out["total"] = int(row.get("c") or 0)
        out["unique_ips"] = int(row.get("ips") or 0)
        out["unique_countries"] = int(row.get("ccs") or 0)
        cur.execute(
            f"""
            SELECT e.event_type, COUNT(*) AS c
            FROM pbt_security_events e
            WHERE {time_sql}
              AND {skip_sql}
            GROUP BY e.event_type
            """,
            skip_params,
        )
        fams: dict[str, int] = {}
        for r in cur.fetchall() or []:
            fam = _family(r.get("event_type"))
            if fam:
                fams[fam] = fams.get(fam, 0) + int(r.get("c") or 0)
        out["families"] = [
            {"family": k, "count": v}
            for k, v in sorted(fams.items(), key=lambda kv: kv[1], reverse=True)
            if v > 0
        ]
        cur.execute(
            f"""
            SELECT
              SUM(CASE WHEN g.country_iso2 = 'ZZ' THEN 1 ELSE 0 END) AS priv,
              SUM(CASE WHEN g.country_iso2 IS NULL OR g.country_iso2 = 'XX' THEN 1 ELSE 0 END) AS unk
            FROM pbt_security_events e
            LEFT JOIN pbt_ip_geo g ON g.ip = e.ip
            WHERE {time_sql}
              AND e.ip IS NOT NULL AND e.ip != ''
              AND {skip_sql}
            """,
            skip_params,
        )
        row = cur.fetchone() or {}
        out["private_events"] = int(row.get("priv") or 0)
        out["unresolved_events"] = int(row.get("unk") or 0)
        cur.execute(
            f"""
            SELECT e.id, e.event_type, e.ip, e.created_at, e.user_id,
                   e.device_fp, e.notes,
                   COALESCE(g.country_iso2, 'XX') AS cc
            FROM pbt_security_events e
            LEFT JOIN pbt_ip_geo g ON g.ip = e.ip
            WHERE {time_sql}
              AND e.ip IS NOT NULL AND e.ip != ''
              AND {skip_sql}
            ORDER BY e.id DESC
            LIMIT 24
            """,
            skip_params,
        )
        rec = []
        for r in cur.fetchall() or []:
            rec.append(
                {
                    "id": int(r.get("id") or 0),
                    "family": _family(r.get("event_type")),
                    "iso2": (r.get("cc") or "XX").upper(),
                    "at": _fmt_ts(r.get("created_at")),
                    "user_id": r.get("user_id"),
                    "ip": (r.get("ip") or "").strip(),
                    "device_fp": (r.get("device_fp") or "").strip(),
                    "notes": r.get("notes"),
                }
            )
        _attach_usernames(rec)
        out["recent"] = rec
        out["q_ms"] = int((time.monotonic() - t0) * 1000)
        return out
    except Exception as exc:
        print(f"[threat-map] summary_for_window: {exc}")
        out["q_ms"] = int((time.monotonic() - t0) * 1000)
        return out
    finally:
        q._close(conn)


def country_detail(iso2: str, window: str = "24h") -> dict[str, Any]:
    cc = (iso2 or "XX").strip().upper()[:2] or "XX"
    requested = _norm_window(window)
    time_sql = _time_sql(requested)
    skip_sql, skip_params = _skip_sql()
    conn = q._sec()
    out = {
        "iso2": cc,
        "window": requested,
        "count": 0,
        "unique_ips": 0,
        "families": [],
        "sample_ips": [],
        "users": [],
        "last_seen": None,
        "first_seen": None,
        "events_15m": None,
        "events_1h": None,
        "events_24h": None,
        "lifetime_total": None,
    }
    if conn is None:
        return out
    try:
        cur = conn.cursor()
        if cc == "XX":
            geo_clause = "(g.ip IS NULL OR g.country_iso2 = 'XX')"
        else:
            geo_clause = "g.country_iso2 = %s"
            skip_params = list(skip_params) + [cc]
        cur.execute(
            f"""
            SELECT e.event_type, COUNT(*) AS c, COUNT(DISTINCT e.ip) AS ips,
                   MAX(e.created_at) AS last_seen,
                   MIN(e.created_at) AS first_seen
            FROM pbt_security_events e
            LEFT JOIN pbt_ip_geo g ON g.ip = e.ip
            WHERE {time_sql}
              AND e.ip IS NOT NULL AND e.ip != ''
              AND {skip_sql}
              AND {geo_clause}
            GROUP BY e.event_type
            """,
            skip_params,
        )
        fams: dict[str, int] = {}
        last = None
        first = None
        ips = 0
        total = 0
        for r in cur.fetchall() or []:
            fam = _family(r.get("event_type"))
            if not fam:
                continue
            c = int(r.get("c") or 0)
            fams[fam] = fams.get(fam, 0) + c
            total += c
            ips += int(r.get("ips") or 0)
            if r.get("last_seen") and (last is None or r["last_seen"] > last):
                last = r["last_seen"]
            if r.get("first_seen") and (first is None or r["first_seen"] < first):
                first = r["first_seen"]
        out["count"] = total
        out["unique_ips"] = ips
        out["last_seen"] = _fmt_ts(last) if last else None
        out["first_seen"] = _fmt_ts(first) if first else None
        out["families"] = [
            {"family": k, "count": v}
            for k, v in sorted(fams.items(), key=lambda kv: kv[1], reverse=True)
            if v > 0
        ]
        if requested == "live":
            try:
                cur.execute(
                    f"""
                    SELECT
                      SUM(CASE WHEN e.created_at >= NOW() - INTERVAL 15 MINUTE THEN 1 ELSE 0 END) AS m15,
                      SUM(CASE WHEN e.created_at >= NOW() - INTERVAL 1 HOUR THEN 1 ELSE 0 END) AS h1,
                      COUNT(*) AS d1
                    FROM pbt_security_events e
                    LEFT JOIN pbt_ip_geo g ON g.ip = e.ip
                    WHERE e.created_at >= NOW() - INTERVAL 1 DAY
                      AND e.ip IS NOT NULL AND e.ip != ''
                      AND {skip_sql}
                      AND {geo_clause}
                    """,
                    skip_params,
                )
                prow = cur.fetchone() or {}
                out["events_15m"] = int(prow.get("m15") or 0)
                out["events_1h"] = int(prow.get("h1") or 0)
                out["events_24h"] = int(prow.get("d1") or 0)
            except Exception as exc:
                print(f"[threat-map] country live pulse: {exc}")
        cur.execute(
            f"""
            SELECT e.ip, e.event_type, e.created_at, e.user_id, e.device_fp, e.notes
            FROM pbt_security_events e
            LEFT JOIN pbt_ip_geo g ON g.ip = e.ip
            WHERE {time_sql}
              AND e.ip IS NOT NULL AND e.ip != ''
              AND {skip_sql}
              AND {geo_clause}
            ORDER BY e.id DESC
            LIMIT 12
            """,
            list(skip_params),
        )
        samples = []
        seen = set()
        for r in cur.fetchall() or []:
            ip = (r.get("ip") or "").strip()
            if not ip or ip in seen:
                continue
            seen.add(ip)
            parts = ip.split(":")
            if ":" in ip:
                mask = ":".join(parts[:3] + ["…"])
            else:
                octs = ip.split(".")
                mask = ".".join(octs[:2] + ["x", "x"]) if len(octs) == 4 else ip
            samples.append(
                {
                    "ip": mask,
                    "raw_ip": ip,
                    "user_id": r.get("user_id"),
                    "device_fp": (r.get("device_fp") or "").strip(),
                    "notes": r.get("notes"),
                    "family": _family(r.get("event_type")),
                    "at": _fmt_ts(r.get("created_at")),
                }
            )
        copies = [
            {
                "user_id": s.get("user_id"),
                "ip": s.get("raw_ip"),
                "device_fp": s.get("device_fp"),
                "notes": s.get("notes"),
            }
            for s in samples
        ]
        _attach_usernames(copies, keep_ip=True)
        for s, c in zip(samples, copies):
            s["username"] = c.get("username")
            s["user_id"] = c.get("user_id")
            s.pop("raw_ip", None)
        out["sample_ips"] = samples
        users = []
        seen_u = set()
        for s in samples:
            uid = s.get("user_id")
            name = s.get("username")
            key = uid or name
            if not key or key in seen_u:
                continue
            seen_u.add(key)
            users.append({"username": name, "user_id": uid})
        out["users"] = users
        return out
    except Exception as exc:
        print(f"[threat-map] country_detail: {exc}")
        return out
    finally:
        q._close(conn)


def country_history_totals(iso2: str) -> dict[str, Any]:
    """Lifetime family totals for one country. Counts only + a few recent IPs."""
    cc = (iso2 or "XX").strip().upper()[:2] or "XX"
    skip_sql, skip_params = _skip_sql()
    out = {
        "iso2": cc,
        "window": "lifetime",
        "count": 0,
        "unique_ips": 0,
        "families": [],
        "sample_ips": [],
        "users": [],
        "last_seen": None,
        "first_seen": None,
        "lifetime_total": 0,
    }
    conn = q._sec()
    if conn is None:
        return out
    try:
        cur = conn.cursor()
        ensure_ip_geo_table(conn)
        if cc == "XX":
            geo_clause = "(g.ip IS NULL OR g.country_iso2 = 'XX')"
            params = list(skip_params)
            from_sql = """
                FROM pbt_security_events e
                LEFT JOIN pbt_ip_geo g ON g.ip = e.ip
            """
        else:
            geo_clause = "g.country_iso2 = %s"
            params = list(skip_params) + [cc]
            from_sql = """
                FROM pbt_ip_geo g
                INNER JOIN pbt_security_events e ON e.ip = g.ip
            """
        cur.execute(
            f"""
            SELECT e.event_type, COUNT(*) AS c, MAX(e.created_at) AS last_seen
            {from_sql}
            WHERE {skip_sql}
              AND e.ip IS NOT NULL AND e.ip != ''
              AND {geo_clause}
            GROUP BY e.event_type
            """,
            params,
        )
        fams: dict[str, int] = {}
        last = None
        total = 0
        for r in cur.fetchall() or []:
            fam = _family(r.get("event_type"))
            if not fam:
                continue
            c = int(r.get("c") or 0)
            fams[fam] = fams.get(fam, 0) + c
            total += c
            if r.get("last_seen") and (last is None or r["last_seen"] > last):
                last = r["last_seen"]
        out["count"] = total
        out["lifetime_total"] = total
        out["last_seen"] = _fmt_ts(last) if last else None
        out["families"] = [
            {"family": k, "count": v}
            for k, v in sorted(fams.items(), key=lambda kv: kv[1], reverse=True)
            if v > 0
        ]
        cur.execute(
            f"""
            SELECT e.ip, e.event_type, e.created_at
            {from_sql}
            WHERE {skip_sql}
              AND e.ip IS NOT NULL AND e.ip != ''
              AND {geo_clause}
            ORDER BY e.id DESC
            LIMIT 8
            """,
            params,
        )
        samples = []
        seen: set[str] = set()
        for r in cur.fetchall() or []:
            ip = (r.get("ip") or "").strip()
            if not ip or ip in seen:
                continue
            seen.add(ip)
            if ":" in ip:
                parts = ip.split(":")
                mask = ":".join(parts[:3] + ["…"])
            else:
                octs = ip.split(".")
                mask = ".".join(octs[:2] + ["x", "x"]) if len(octs) == 4 else ip
            samples.append(
                {
                    "ip": mask,
                    "family": _family(r.get("event_type")),
                    "at": _fmt_ts(r.get("created_at")),
                }
            )
        out["sample_ips"] = samples
        return out
    except Exception as exc:
        print(f"[threat-map] country_history_totals: {exc}")
        return out
    finally:
        q._close(conn)


def replay_events(
    window: str = "24h",
    *,
    limit: int = 1500,
    after_id: int = 0,
) -> dict[str, Any]:
    """
    Time-ordered page of hits for play/replay. Caller walks after_id until has_more is false.
    Does not downsample — 30-day and lifetime play every row, in pages.
    """
    window = _norm_window(window)
    time_sql = _time_sql(window)
    skip_sql, skip_params = _skip_sql()
    page = max(50, min(int(limit or 1500), 2500))
    after_id = max(0, int(after_id or 0))
    conn = q._sec()
    out = {
        "events": [],
        "total": 0,
        "window": window,
        "after_id": after_id,
        "next_after_id": after_id,
        "has_more": False,
    }
    if conn is None:
        return out
    try:
        cur = conn.cursor()
        ensure_ip_geo_table(conn)
        cur.execute(
            f"""
            SELECT COUNT(*) AS c
            FROM pbt_security_events e
            WHERE {time_sql}
              AND e.ip IS NOT NULL AND e.ip != ''
              AND {skip_sql}
            """,
            skip_params,
        )
        out["total"] = int((cur.fetchone() or {}).get("c") or 0)
        params = list(skip_params) + [after_id, page]
        cur.execute(
            f"""
            SELECT e.id, e.event_type, e.ip, e.created_at, e.user_id,
                   e.device_fp, e.notes,
                   COALESCE(g.country_iso2, 'XX') AS cc
            FROM pbt_security_events e
            LEFT JOIN pbt_ip_geo g ON g.ip = e.ip
            WHERE {time_sql}
              AND e.ip IS NOT NULL AND e.ip != ''
              AND {skip_sql}
              AND e.id > %s
            ORDER BY e.id ASC
            LIMIT %s
            """,
            params,
        )
        raw = list(cur.fetchall() or [])
        if window != "lifetime":
            need = [
                r.get("ip")
                for r in raw
                if (r.get("cc") or "XX").upper() == "XX" and (r.get("ip") or "").strip()
            ]
            if need:
                fill_missing_ips(conn, need, limit=min(80, len(need)))
                ids = [int(r.get("id") or 0) for r in raw if r.get("id")]
                if ids:
                    ph = ",".join(["%s"] * len(ids))
                    cur.execute(
                        f"""
                        SELECT e.id, e.event_type, e.ip, e.created_at, e.user_id,
                               e.device_fp, e.notes,
                               COALESCE(g.country_iso2, 'XX') AS cc
                        FROM pbt_security_events e
                        LEFT JOIN pbt_ip_geo g ON g.ip = e.ip
                        WHERE e.id IN ({ph})
                        ORDER BY e.id ASC
                        """,
                        ids,
                    )
                    raw = list(cur.fetchall() or [])
        events = []
        for r in raw:
            events.append(
                {
                    "id": int(r.get("id") or 0),
                    "family": _family(r.get("event_type")),
                    "iso2": (r.get("cc") or "XX").upper(),
                    "at": _fmt_ts(r.get("created_at")),
                    "user_id": r.get("user_id"),
                    "ip": (r.get("ip") or "").strip(),
                    "device_fp": (r.get("device_fp") or "").strip(),
                    "notes": r.get("notes"),
                }
            )
        _attach_usernames(events)
        out["events"] = events
        if events:
            out["next_after_id"] = events[-1]["id"]
        out["has_more"] = len(events) >= page
        return out
    except Exception as exc:
        print(f"[threat-map] replay_events: {exc}")
        return out
    finally:
        q._close(conn)


def recent_events(window: str = "24h", *, limit: int = 24) -> dict[str, Any]:
    """Newest hits for the Recent list. Always a real list, not animation-only."""
    window = _norm_window(window)
    if window == "lifetime":
        window = "24h"
    time_sql = _time_sql(window)
    skip_sql, skip_params = _skip_sql()
    conn = q._sec()
    out = {"events": [], "window": window}
    if conn is None:
        return out
    try:
        cur = conn.cursor()
        ensure_ip_geo_table(conn)
        cur.execute(
            f"""
            SELECT e.id, e.event_type, e.ip, e.created_at, e.user_id,
                   e.device_fp, e.notes,
                   COALESCE(g.country_iso2, 'XX') AS cc
            FROM pbt_security_events e
            LEFT JOIN pbt_ip_geo g ON g.ip = e.ip
            WHERE {time_sql}
              AND e.ip IS NOT NULL AND e.ip != ''
              AND {skip_sql}
            ORDER BY e.created_at DESC, e.id DESC
            LIMIT %s
            """,
            list(skip_params) + [max(1, min(int(limit), 48))],
        )
        events = []
        for r in cur.fetchall() or []:
            events.append(
                {
                    "id": int(r.get("id") or 0),
                    "family": _family(r.get("event_type")),
                    "iso2": (r.get("cc") or "XX").upper(),
                    "at": _fmt_ts(r.get("created_at")),
                    "user_id": r.get("user_id"),
                    "ip": (r.get("ip") or "").strip(),
                    "device_fp": (r.get("device_fp") or "").strip(),
                    "notes": r.get("notes"),
                }
            )
        _attach_usernames(events)
        out["events"] = events
        return out
    except Exception as exc:
        print(f"[threat-map] recent_events: {exc}")
        return out
    finally:
        q._close(conn)


def _live_pulse(cur, skip_sql: str, skip_params: list) -> dict[str, Any]:
    """How fresh the map feed is — same skip rules as the counters."""
    pulse = {
        "events_15m": 0,
        "events_1h": 0,
        "events_24h": 0,
        "last_event_at": None,
        "last_event_age_sec": None,
        "head_id": 0,
    }
    try:
        cur.execute("SELECT MAX(id) AS head_id FROM pbt_security_events")
        row = cur.fetchone() or {}
        pulse["head_id"] = int(row.get("head_id") or 0)
    except Exception:
        pass
    try:
        cur.execute(
            f"""
            SELECT
              SUM(CASE WHEN e.created_at >= NOW() - INTERVAL 15 MINUTE THEN 1 ELSE 0 END) AS m15,
              SUM(CASE WHEN e.created_at >= NOW() - INTERVAL 1 HOUR THEN 1 ELSE 0 END) AS h1,
              COUNT(*) AS d1,
              MAX(e.created_at) AS last_at
            FROM pbt_security_events e
            WHERE e.created_at >= NOW() - INTERVAL 1 DAY
              AND e.ip IS NOT NULL AND e.ip != ''
              AND {skip_sql}
            """,
            skip_params,
        )
        row = cur.fetchone() or {}
        pulse["events_15m"] = int(row.get("m15") or 0)
        pulse["events_1h"] = int(row.get("h1") or 0)
        pulse["events_24h"] = int(row.get("d1") or 0)
        last = row.get("last_at")
        if last:
            pulse["last_event_at"] = _fmt_ts(last)
            try:
                cur.execute(
                    "SELECT TIMESTAMPDIFF(SECOND, %s, NOW()) AS age",
                    (last,),
                )
                age_row = cur.fetchone() or {}
                age = age_row.get("age")
                if age is not None:
                    pulse["last_event_age_sec"] = max(0, int(age))
            except Exception:
                pulse["last_event_age_sec"] = None
    except Exception as exc:
        print(f"[threat-map] live pulse: {exc}")
    return pulse


def live_events(
    *,
    since_id: int = 0,
    limit: int = 80,
    arm: bool = False,
    follow: bool = False,
) -> dict[str, Any]:
    skip_sql, skip_params = _skip_sql()
    conn = q._sec()
    out = {
        "events": [],
        "max_id": int(since_id or 0),
        "events_15m": 0,
        "events_1h": 0,
        "events_24h": 0,
        "last_event_at": None,
        "last_event_age_sec": None,
        "armed": False,
    }
    if conn is None:
        return out
    try:
        cur = conn.cursor()
        ensure_ip_geo_table(conn)
        pulse = _live_pulse(cur, skip_sql, skip_params)
        out.update(
            {
                "events_15m": pulse["events_15m"],
                "events_1h": pulse["events_1h"],
                "events_24h": pulse["events_24h"],
                "last_event_at": pulse["last_event_at"],
                "last_event_age_sec": pulse["last_event_age_sec"],
            }
        )
        head_id = int(pulse.get("head_id") or 0)
        # Arm pins the cursor at the table head and returns no backlog.
        # follow=1 is required after a quiet arm (max_id 0) so the next
        # real hit is not treated as another arm and swallowed.
        pinning = arm or int(since_id) < 0 or (int(since_id) <= 0 and not follow)
        if pinning:
            out["max_id"] = head_id
            out["armed"] = True
            return out

        cur.execute(
            f"""
            SELECT e.id, e.event_type, e.ip, e.created_at, e.user_id,
                   e.device_fp, e.notes,
                   COALESCE(g.country_iso2, 'XX') AS cc
            FROM pbt_security_events e
            LEFT JOIN pbt_ip_geo g ON g.ip = e.ip
            WHERE {skip_sql}
              AND e.id > %s
              AND e.ip IS NOT NULL AND e.ip != ''
            ORDER BY e.id ASC
            LIMIT %s
            """,
            list(skip_params) + [int(since_id), int(limit)],
        )
        rows = []
        max_id = int(since_id or 0)
        need_fill = []
        for r in cur.fetchall() or []:
            eid = int(r.get("id") or 0)
            if eid > max_id:
                max_id = eid
            cc = (r.get("cc") or "XX").upper()
            ip = (r.get("ip") or "").strip()
            if cc == "XX" and ip:
                need_fill.append(ip)
            rows.append(
                {
                    "id": eid,
                    "family": _family(r.get("event_type")),
                    "iso2": cc,
                    "at": _fmt_ts(r.get("created_at")),
                    "user_id": r.get("user_id"),
                    "ip": ip,
                    "device_fp": (r.get("device_fp") or "").strip(),
                    "notes": r.get("notes"),
                }
            )
        if need_fill:
            fill_missing_ips(conn, need_fill, limit=80)
            # re-stamp XX with just-resolved values
            for ev in rows:
                if ev["iso2"] != "XX":
                    continue
        # second pass for newly filled
        if need_fill:
            ph = ",".join(["%s"] * len(need_fill))
            cur.execute(
                f"SELECT ip, country_iso2 FROM pbt_ip_geo WHERE ip IN ({ph})",
                need_fill,
            )
            geo = {
                (r.get("ip") if isinstance(r, dict) else r[0]): (
                    r.get("country_iso2") if isinstance(r, dict) else r[1]
                )
                for r in (cur.fetchall() or [])
            }
            # we didn't keep ip on events for privacy — re-query those ids
            ids = [e["id"] for e in rows if e["iso2"] == "XX"]
            if ids:
                ph = ",".join(["%s"] * len(ids))
                cur.execute(
                    f"""
                    SELECT e.id, COALESCE(g.country_iso2, 'XX') AS cc
                    FROM pbt_security_events e
                    LEFT JOIN pbt_ip_geo g ON g.ip = e.ip
                    WHERE e.id IN ({ph})
                    """,
                    ids,
                )
                by_id = {int(r["id"]): (r.get("cc") or "XX").upper() for r in (cur.fetchall() or [])}
                for ev in rows:
                    if ev["id"] in by_id:
                        ev["iso2"] = by_id[ev["id"]]
        _attach_usernames(rows)
        out["events"] = rows
        out["max_id"] = max_id
        return out
    except Exception as exc:
        print(f"[threat-map] live_events: {exc}")
        return out
    finally:
        q._close(conn)
