# ================================================================
# poweredbytop/reputation/scorer.py
# Purpose: IP reputation - fair climb for good clients, real penalties for abuse
# ================================================================
from __future__ import annotations

import traceback
from datetime import datetime, timedelta

from poweredbytop.models.connect_db import get_security_db
from poweredbytop.utils.helpers import logger
from poweredbytop.config.settings import (
    REPUTATION_STAGGER_ENABLED,
    REPUTATION_STAGGER_MIN,
    REPUTATION_STAGGER_MAX,
    REPUTATION_STAGGER_SCORE_DIVISOR,
    REPUTATION_STAGGER_POSITIVE_DIVISOR,
)

REPUTATION_TABLE = "pbt_reputation"
INITIAL_REPUTATION = 80
MIN_SCORE = 0
MAX_SCORE = 1000

# Score floors for grades (temp_ban is active ban, not just a label)
GRADE_TRUSTED = 700
GRADE_GOOD = 400
GRADE_NORMAL = 150
GRADE_SUSPICIOUS = 55
# Below this -> automatic short temp ban (not forever)
# Only for weak / unknown IPs or hard attacks — not one bad login for known good clients.
AUTO_TEMP_BAN_SCORE = 35

# How hard each hit hurts (not extreme - repeat offenders still fall fast)
DEFAULT_BAD_SEVERITY = 2
REASON_SEVERITY = {
    "attack_path_probe": 5,
    "early_block_attack_path": 5,
    "known_attacker_ua": 5,
    "early_block_attacker_ua": 5,
    "bot_attempt": 3,
    "suspicious_ua": 3,
    "brute_force": 4,
    "brute_force_lock": 4,
    # Auth mistakes: light. One wrong password must not nuke a known-good IP.
    "failed_login": 1,
    "token_attack": 4,
    "invalid_token": 4,
    "ddos_attempts": 3,
    "rate_limit_exceeded": 2,
    "rate_limit": 2,
    "refresh_spam": 1,
    "rate_limited_request": 1,
    "reputation_block": 1,
    "low_reputation": 1,
    "n1_attack": 3,
    "n1_query_detected": 3,
    "csrf_failure": 1,
    "csrf": 1,
    "not_vetted": 1,
    "suspicious": 2,
    # Cross-tenant / IDOR: forging client_id, company_id, sponsor_id
    "tenant_id_tamper": 5,
    "cross_tenant_access": 5,
    "idor_attempt": 5,
}

# Soft events: never alone should temp-ban an established good IP
# (rate_limit / refresh spam = throttle, not "attack"; ddos stays hard)
SOFT_BAD_REASONS = frozenset(
    {
        "failed_login",
        "not_vetted",
        "csrf",
        "csrf_failure",
        "csrf_field_stale",
        "token_attack",
        "invalid_token",
        "session_bind_fail",
        "refresh_spam",
        "rate_limited_request",
        "rate_limit",
        "rate_limit_exceeded",
        "reputation_block",
        "low_reputation",
        "device_ban_soft_established",
        "ip_ban_soft_allow",
    }
)

# Logged-in product users: still LOG these events, never spend score on them.
# Field officers trip CSRF / expired tokens / cell IP binds while doing real work.
AUTH_NO_SCORE_REASONS = frozenset(
    {
        "csrf",
        "csrf_failure",
        "csrf_field_stale",
        "token_attack",
        "invalid_token",
        "session_bind_fail",
    }
)

# Align with pipeline (_is_established_client uses ~20). Old 50 meant
# a brand-new guard IP ate full penalties for the first 50 requests.
TRUST_POSITIVE_SHIELD = 15
TRUST_SCORE_SHIELD = 400  # grade "good" floor
# Healthy operating floor — good traffic pulls back toward this.
HEALTHY_FLOOR = 80

# Operational noise: throttle / log only. NEVER change score.
# These fire from GPS pings, dashboards, CSRF misses, and circular
# "you're already low" events — they were driving 80 → 50 on real users.
NO_SCORE_REASONS = frozenset(
    {
        "rate_limit",
        "rate_limit_exceeded",
        "rate_limited_request",
        "refresh_spam",
        "not_vetted",
        "low_reputation",
        "reputation_block",
        "csrf_field_stale",
    }
)

# Points subtracted per severity unit on each bad hit
BAD_POINTS_PER_SEVERITY = 9
# Soft events use a lower multiplier so thousands of goods >> one bad login
SOFT_BAD_POINTS_PER_SEVERITY = 2
# Cap soft penalty for established IPs (absolute points per event)
SOFT_PENALTY_CAP_ESTABLISHED = 2
# Points added on a full good update (slow climb above the floor)
GOOD_POINTS_PER_HIT = 2
# Registered / logged-in staff: good requests must outrun field noise
GOOD_POINTS_OPERATOR = 12
# Heal toward HEALTHY_FLOOR after soft / accidental hits
GOOD_HEAL_POINTS = 5
GOOD_HEAL_OPERATOR = 20
# Light good only bumps counters; full climb is staggered
GOOD_LIGHT_EVERY_N = 1


def ensure_reputation_table():
    db = get_security_db()
    if db is None:
        return
    try:
        cursor = db.cursor()
        cursor.execute(f"SHOW TABLES LIKE %s", (REPUTATION_TABLE,))
        if not cursor.fetchone():
            create_sql = f"""CREATE TABLE IF NOT EXISTS {REPUTATION_TABLE} (
                ip VARCHAR(45) PRIMARY KEY,
                score INT DEFAULT {INITIAL_REPUTATION},
                grade VARCHAR(20) DEFAULT 'suspicious',
                positive_requests INT DEFAULT 0,
                negative_points INT DEFAULT 0,
                ban_until DATETIME NULL,
                ban_reason TEXT NULL,
                ban_count INT DEFAULT 0,
                first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_bad_behavior DATETIME NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            cursor.execute(create_sql)
            db.commit()
            logger(f"[REPUTATION] Table {REPUTATION_TABLE} created successfully")
    except Exception as e:
        logger(f"[REPUTATION] ensure_reputation_table ERROR: {e}")
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass


def create_tables(cursor=None):
    ensure_reputation_table()


def _clamp_score(score: int) -> int:
    return max(MIN_SCORE, min(MAX_SCORE, int(score)))


def _is_authenticated_operator() -> bool:
    """Logged-in product user (guard / sponsor / owner / client) — not a scanner."""
    try:
        from flask import has_request_context
        from flask_login import current_user

        if not has_request_context():
            return False
        if not getattr(current_user, "is_authenticated", False):
            return False
        role = (getattr(current_user, "role", None) or "").strip().lower()
        return role in {
            "guard",
            "sponsor_admin",
            "security_company",
            "owner",
            "admin",
            "client",
            "clients",
            "client_admin",
            "employee",
            "staff",
        }
    except Exception:
        return False


def _get_grade(score: int, *, grade_override: str | None = None) -> str:
    if grade_override in ("perm_ban", "temp_ban", "trusted"):
        return grade_override
    if score >= GRADE_TRUSTED:
        return "trusted"
    if score >= GRADE_GOOD:
        return "good"
    if score >= GRADE_NORMAL:
        return "normal"
    if score >= GRADE_SUSPICIOUS:
        return "suspicious"
    return "temp_ban"


def severity_for_reason(reason: str | None, severity: int | None = None) -> int:
    """Resolve a useful severity; never ignore bad hits by defaulting to 0."""
    if severity is not None:
        try:
            sev = int(severity)
            if sev > 0:
                return max(1, min(sev, 10))
        except (TypeError, ValueError):
            pass
    key = (reason or "suspicious").strip().lower()
    if key in REASON_SEVERITY:
        return REASON_SEVERITY[key]
    # partial match
    for k, v in REASON_SEVERITY.items():
        if k in key or key in k:
            return v
    return DEFAULT_BAD_SEVERITY


def _calculate_stagger(score: int, positive_requests: int, negative_points: int) -> int:
    """How often we do a FULL good-behavior score climb."""
    if not REPUTATION_STAGGER_ENABLED:
        return 1
    net_positive = max(0, positive_requests - negative_points)
    stagger = 1 + (score // REPUTATION_STAGGER_SCORE_DIVISOR) + (
        net_positive // REPUTATION_STAGGER_POSITIVE_DIVISOR
    )
    return max(REPUTATION_STAGGER_MIN, min(REPUTATION_STAGGER_MAX, stagger))


def _row_to_info(row: dict | None, ip: str = "") -> dict:
    if not row:
        return {
            "ip": ip,
            "score": INITIAL_REPUTATION,
            "grade": "normal",
            "positive_requests": 0,
            "negative_points": 0,
            "ban_until": None,
            "ban_reason": None,
            "ban_count": 0,
            "is_banned": False,
        }
    until = row.get("ban_until")
    grade = (row.get("grade") or "normal").lower()
    now = datetime.now()
    active_ban = grade == "perm_ban" or (
        until is not None and until > now
    ) or (grade == "temp_ban" and until is not None and until > now)
    # expired temp ban still labeled temp_ban -> treat as not banned for climb
    if grade == "temp_ban" and until is not None and until <= now:
        active_ban = False
    return {
        "ip": row.get("ip") or ip,
        "score": int(row.get("score") if row.get("score") is not None else INITIAL_REPUTATION),
        "grade": grade,
        "positive_requests": int(row.get("positive_requests") or 0),
        "negative_points": int(row.get("negative_points") or 0),
        "ban_until": until,
        "ban_reason": row.get("ban_reason"),
        "ban_count": int(row.get("ban_count") or 0),
        "is_banned": bool(active_ban),
        "last_bad_behavior": row.get("last_bad_behavior"),
        "last_seen": row.get("last_seen"),
    }


def get_reputation_info(ip: str) -> dict:
    ensure_reputation_table()
    db = get_security_db()
    if db is None:
        return _row_to_info(None, ip)
    try:
        cursor = db.cursor()
        cursor.execute(
            f"""
            SELECT ip, score, grade, positive_requests, negative_points,
                   ban_until, ban_reason, ban_count, last_bad_behavior, last_seen
            FROM {REPUTATION_TABLE} WHERE ip = %s
            """,
            (ip,),
        )
        row = cursor.fetchone()
        return _row_to_info(row, ip)
    except Exception as e:
        logger(f"[REPUTATION] get_reputation_info ERROR: {e}")
        return _row_to_info(None, ip)
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass


def get_reputation_score(ip: str) -> int:
    return int(get_reputation_info(ip).get("score") or INITIAL_REPUTATION)


def get_reputation_grade(ip: str) -> str:
    return str(get_reputation_info(ip).get("grade") or "normal")


# ====================== LIGHT VERSION (No score recalculation) ======================
def record_good_behavior_light(ip: str) -> None:
    """Light version - only increments positive_requests. No score climb."""
    if not ip:
        return
    ensure_reputation_table()
    db = get_security_db()
    if db is None:
        return
    try:
        cursor = db.cursor()
        cursor.execute(
            f"""
            INSERT INTO {REPUTATION_TABLE} (ip, score, grade, positive_requests, last_seen)
            VALUES (%s, %s, 'normal', 1, NOW())
            ON DUPLICATE KEY UPDATE
                positive_requests = positive_requests + 1,
                last_seen = NOW()
            """,
            (ip, INITIAL_REPUTATION),
        )
        db.commit()
    except Exception as e:
        logger(f"[REPUTATION] record_good_behavior_light ERROR: {e}")
        if db:
            try:
                db.rollback()
            except Exception:
                pass
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass


# ====================== FULL VERSION (Heavy - with score climb) ======================
def record_good_behavior(ip: str, request_type: str = "page_view", full_update: bool = True) -> None:
    """
    Record good behavior.
    full_update=False -> light counter only.
    Does not raise score while actively banned. Slow climb after recent abuse.
    """
    if not ip:
        return
    if not full_update:
        record_good_behavior_light(ip)
        return

    ensure_reputation_table()
    db = get_security_db()
    if db is None:
        return
    try:
        cursor = db.cursor()
        cursor.execute(
            f"""
            INSERT INTO {REPUTATION_TABLE} (ip, score, grade, positive_requests, last_seen)
            VALUES (%s, %s, 'normal', 1, NOW())
            ON DUPLICATE KEY UPDATE
                positive_requests = positive_requests + 1,
                last_seen = NOW()
            """,
            (ip, INITIAL_REPUTATION),
        )

        cursor.execute(
            f"""
            SELECT score, grade, positive_requests, negative_points,
                   ban_until, ban_reason, last_bad_behavior
            FROM {REPUTATION_TABLE} WHERE ip = %s
            """,
            (ip,),
        )
        row = cursor.fetchone() or {}
        info = _row_to_info({**row, "ip": ip}, ip)

        # Never reward while hard-banned
        if info["is_banned"] or (info["grade"] or "") == "perm_ban":
            db.commit()
            logger(
                f"[REPUTATION] +GOOD skipped (banned) | IP={ip[:8]}... | grade={info['grade']}"
            )
            return

        positive = info["positive_requests"]
        negative = info["negative_points"]
        current_score = info["score"]
        operator = _is_authenticated_operator()

        # Recent HARD abuse -> slower climb. Soft / operational hits must not
        # freeze recovery (that was leaving guards stuck after rate-limits).
        climb = GOOD_POINTS_PER_HIT
        last_bad = info.get("last_bad_behavior")
        if last_bad is not None and not operator:
            try:
                age = datetime.now() - (
                    last_bad if last_bad.tzinfo is None else last_bad.replace(tzinfo=None)
                )
                if age < timedelta(minutes=15):
                    climb = 1
                elif age < timedelta(hours=1):
                    climb = max(1, GOOD_POINTS_PER_HIT // 2)
            except Exception:
                pass

        # Logged-in staff: bigger climb so GPS / checklists / dash work
        # outruns leftover CSRF / token noise from the same shift.
        if operator:
            climb = max(climb, GOOD_POINTS_OPERATOR)
            if current_score < 250:
                climb = max(climb, GOOD_HEAL_OPERATOR)
            if current_score < HEALTHY_FLOOR:
                climb = max(climb, 25)

        # Heal toward the healthy floor so scores can go UP again after noise
        if current_score < HEALTHY_FLOOR and (
            operator or positive >= TRUST_POSITIVE_SHIELD or current_score >= 50
        ):
            climb = max(climb, GOOD_HEAL_POINTS if not operator else GOOD_HEAL_OPERATOR)

        # Net-negative unknown IPs climb slower — not logged-in staff
        if negative > positive and not operator and current_score >= HEALTHY_FLOOR:
            climb = min(climb, 1)

        score = _clamp_score(current_score + climb)
        grade = _get_grade(score)
        if (info["grade"] or "").lower() == "trusted":
            grade = "trusted"

        # Clear expired temp ban labels. Operators also burn down old
        # negative_points so a night of field work raises the score again.
        neg_decay = 3 if operator else (1 if positive > negative else 0)
        cursor.execute(
            f"""
            UPDATE {REPUTATION_TABLE}
            SET score = %s, grade = %s, last_seen = NOW(),
                negative_points = GREATEST(0, COALESCE(negative_points, 0) - %s),
                ban_until = CASE
                    WHEN ban_until IS NOT NULL AND ban_until <= NOW() AND grade != 'perm_ban'
                    THEN NULL ELSE ban_until END,
                ban_reason = CASE
                    WHEN ban_until IS NOT NULL AND ban_until <= NOW() AND grade != 'perm_ban'
                    THEN NULL ELSE ban_reason END
            WHERE ip = %s AND (grade IS NULL OR grade != 'perm_ban')
            """,
            (score, grade, neg_decay, ip),
        )
        db.commit()
        logger(
            f"[REPUTATION] +GOOD (full) | IP={ip[:8]}... | +{climb} | "
            f"pos={positive} neg={negative} score={score} grade={grade}"
        )
    except Exception as e:
        logger(f"[REPUTATION] record_good_behavior ERROR: {e}")
        logger(traceback.format_exc())
        if db:
            try:
                db.rollback()
            except Exception:
                pass
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass


def record_bad_behavior(ip: str, reason: str = "suspicious", severity: int | None = None) -> dict:
    """
    Always apply a real penalty (default severity from reason map).
    Returns updated reputation info dict (or empty on failure).
    """
    if not ip:
        return {}
    reason_key = (reason or "").strip().lower()
    # Throttle / circular / session noise: do not touch score or last_bad
    if reason_key in NO_SCORE_REASONS:
        return get_reputation_info(ip)
    sev = severity_for_reason(reason, severity)
    soft_event = reason_key in SOFT_BAD_REASONS or sev <= 1
    operator = _is_authenticated_operator()
    ensure_reputation_table()
    db = get_security_db()
    if db is None:
        return {}
    try:
        cursor = db.cursor()
        cursor.execute(
            f"""
            SELECT score, grade, positive_requests, negative_points,
                   ban_until, ban_reason, ban_count
            FROM {REPUTATION_TABLE} WHERE ip = %s
            """,
            (ip,),
        )
        existing = cursor.fetchone() or {}
        current_score = int(
            existing.get("score") if existing.get("score") is not None else INITIAL_REPUTATION
        )
        current_grade = (existing.get("grade") or "normal").lower()
        positive = int(existing.get("positive_requests") or 0)
        established = (
            operator
            or positive >= TRUST_POSITIVE_SHIELD
            or current_score >= TRUST_SCORE_SHIELD
            or current_grade in ("trusted", "good")
            or (current_score >= HEALTHY_FLOOR and positive >= 5)
        )
        # Logged-in staff: CSRF / token / bind noise is still an event
        # (callers log it) but must not spend reputation.
        if operator and reason_key in AUTH_NO_SCORE_REASONS:
            logger(
                f"[REPUTATION] BAD skipped (operator no-score) | "
                f"IP={ip[:8]}... | reason={reason}"
            )
            return get_reputation_info(ip)
        # Logged-in staff / established IPs: soft events do not cost score
        # and must not stamp last_bad (that froze recovery for an hour).
        if soft_event and (operator or established):
            logger(
                f"[REPUTATION] BAD skipped (soft+{'operator' if operator else 'established'}) | "
                f"IP={ip[:8]}... | reason={reason}"
            )
            return get_reputation_info(ip)

        # Ensure row exists, then apply the hit
        cursor.execute(
            f"""
            INSERT INTO {REPUTATION_TABLE}
                (ip, score, grade, positive_requests, negative_points,
                 last_bad_behavior, last_seen)
            VALUES (%s, %s, 'suspicious', 0, %s, NOW(), NOW())
            ON DUPLICATE KEY UPDATE
                negative_points = negative_points + %s,
                last_bad_behavior = NOW(),
                last_seen = NOW()
            """,
            (ip, INITIAL_REPUTATION, sev, sev),
        )

        cursor.execute(
            f"""
            SELECT score, grade, positive_requests, negative_points,
                   ban_until, ban_reason, ban_count
            FROM {REPUTATION_TABLE} WHERE ip = %s
            """,
            (ip,),
        )
        row = cursor.fetchone() or {}
        positive = int(row.get("positive_requests") or 0)
        negative = int(row.get("negative_points") or 0)
        current_score = int(
            row.get("score") if row.get("score") is not None else INITIAL_REPUTATION
        )
        current_grade = (row.get("grade") or "normal").lower()

        # Never soften a permanent ban
        if current_grade == "perm_ban":
            db.commit()
            return get_reputation_info(ip)

        # Smart penalty: soft events hit lightly; established good IPs almost shrug them off
        if soft_event:
            penalty = min(sev * SOFT_BAD_POINTS_PER_SEVERITY, 3)
        else:
            penalty = sev * BAD_POINTS_PER_SEVERITY
            # Extra weight when this IP is already a repeat offender (hard abuse only)
            if negative >= 20:
                penalty += 8
            elif negative >= 10:
                penalty += 4
            # Still soften hard events slightly for highly trusted IPs (not honeypot-level)
            if established and sev <= 3 and positive >= 200:
                penalty = max(3, int(penalty * 0.5))

        score = _clamp_score(current_score - penalty)
        # Counter formula so mass abuse cannot sit at a high score
        # Soft events: do not let a tiny negative pile rewrite a long good history
        if soft_event and established:
            formula = INITIAL_REPUTATION + (positive * 3) - (negative * 2)
            score = _clamp_score(max(score, min(current_score - penalty, formula)))
            # Floor for established soft events — stay out of ban territory
            score = max(score, AUTO_TEMP_BAN_SCORE + 10)
        elif operator or established:
            # Positives matter more than leftover field noise.
            # Never formula-crush a boosted 300 down to 78 in one night.
            floor = INITIAL_REPUTATION + max(0, positive // 3)
            if operator:
                floor = max(floor, HEALTHY_FLOOR + 20)
            score = max(score, min(current_score, floor))
        else:
            formula = INITIAL_REPUTATION + (positive * 1) - (negative * 6)
            score = _clamp_score(min(score, formula + 20))

        ban_until = row.get("ban_until")
        ban_reason = row.get("ban_reason")
        grade = _get_grade(score)

        # Auto temp-ban only when it makes sense:
        # - hard attack severity, or
        # - weak / unknown IP with collapsed score
        # Never for a soft event on an established good IP (e.g. 1 failed login).
        allow_auto_temp = score < AUTO_TEMP_BAN_SCORE
        if allow_auto_temp and soft_event and established:
            allow_auto_temp = False
            score = max(score, AUTO_TEMP_BAN_SCORE + 10)
            grade = _get_grade(score)
        # Established / fleet operators: one hard probe must NOT auto-ban.
        # Owner hopping aegis↔aegisx↔ax is normal; only repeated hard abuse bans.
        if allow_auto_temp and established:
            min_neg = max(12, positive // 15) if positive else 12
            if negative < min_neg:
                allow_auto_temp = False
                score = max(score, AUTO_TEMP_BAN_SCORE + 5)
                grade = _get_grade(score)
        if allow_auto_temp and positive >= TRUST_POSITIVE_SHIELD and sev < 5:
            # Need repeated real abuse (high negative vs positive) to ban known goods
            if negative < max(15, positive // 20):
                allow_auto_temp = False
                score = max(score, AUTO_TEMP_BAN_SCORE + 5)
                grade = _get_grade(score)
        # Never auto-ban grade=trusted (console-trusted operator IP)
        if allow_auto_temp and current_grade == "trusted":
            allow_auto_temp = False
            score = max(score, AUTO_TEMP_BAN_SCORE + 15)
            grade = "trusted"

        if allow_auto_temp:
            # OFFICE-SAFE BAN POLICY:
            # Prefer banning the device fingerprint. Full IP temp-ban only when
            # the IP is weak/unknown OR hard multi-device abuse — never nuke a
            # known-good office NAT for one throwaway device.
            # Multi-domain fleet traffic is not multi-device abuse.
            grade = "temp_ban"
            hours = 1
            if sev >= 5:
                hours = 6
            elif sev >= 4:
                hours = 3
            elif sev >= 3:
                hours = 2
            if score <= 15:
                hours = max(hours, 12)
            elif score <= 25:
                hours = max(hours, 4)

            # ALWAYS prefer device-scoped enforcement (shared officer / office NAT).
            # IP grade may drop for console visibility, but we do NOT auto temp-ban
            # the whole IP — clean devices on that IP must keep working.
            try:
                from poweredbytop.security.device_print import (
                    ban_device,
                    build_device_fingerprint,
                )

                device_fp = build_device_fingerprint(ip).get("device_fp")
                if device_fp:
                    skip_reg = False
                    try:
                        from poweredbytop.security.device_print import linked_user_id_for_device

                        skip_reg = bool(linked_user_id_for_device(device_fp))
                    except Exception:
                        skip_reg = False
                    if skip_reg:
                        logger(
                            f"[REPUTATION] skip device ban — registered fp={(device_fp or '')[:12]}"
                        )
                    else:
                        ban_device(
                            device_fp,
                            reason=f"device-scoped: {reason or 'abuse'}",
                            hours=hours,
                            permanent=False,
                        )
                # Score the IP down for history, but leave it usable (no temp_ban grade)
                grade = _get_grade(max(score, AUTO_TEMP_BAN_SCORE + 10))
                score = max(score, AUTO_TEMP_BAN_SCORE + 5)
                logger(
                    f"[REPUTATION] DEVICE-ONLY auto ban | IP={ip[:8]}... | "
                    f"fp={(device_fp or '')[:12]} | no IP hard-ban"
                )
                cursor.execute(
                    f"""
                    UPDATE {REPUTATION_TABLE}
                    SET score=%s, grade=%s, last_seen=NOW()
                    WHERE ip=%s AND grade != 'perm_ban'
                    """,
                    (score, grade, ip),
                )
            except Exception as dev_err:
                # Last resort: score only — still do not IP-ban whole offices
                logger(f"[REPUTATION] device ban failed; score-only: {dev_err}")
                grade = _get_grade(max(score, AUTO_TEMP_BAN_SCORE + 5))
                cursor.execute(
                    f"""
                    UPDATE {REPUTATION_TABLE}
                    SET score=%s, grade=%s, last_seen=NOW()
                    WHERE ip=%s AND grade != 'perm_ban'
                    """,
                    (score, grade, ip),
                )
        else:
            # Keep an active temp ban's grade until it expires, but still drop score
            keep_temp = (
                current_grade == "temp_ban"
                and ban_until is not None
                and ban_until > datetime.now()
            )
            final_grade = "temp_ban" if keep_temp else grade
            cursor.execute(
                f"""
                UPDATE {REPUTATION_TABLE}
                SET score=%s, grade=%s, last_seen=NOW()
                WHERE ip=%s AND grade != 'perm_ban'
                """,
                (score, final_grade, ip),
            )

        db.commit()
        info = get_reputation_info(ip)
        logger(
            f"[REPUTATION] BAD | IP={ip[:8]}... | reason={reason} | sev={sev} | "
            f"-{penalty} -> score={info.get('score')} grade={info.get('grade')} "
            f"neg={info.get('negative_points')}"
        )
        return info
    except Exception as e:
        logger(f"[REPUTATION] record_bad_behavior ERROR: {e}")
        logger(traceback.format_exc())
        if db:
            try:
                db.rollback()
            except Exception:
                pass
        return {}
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass


def ban_ip(ip: str, reason: str, permanent: bool = False, hours: int = 1) -> None:
    ensure_reputation_table()
    db = get_security_db()
    if db is None:
        return
    try:
        cursor = db.cursor()
        cursor.execute(
            f"""
            INSERT INTO {REPUTATION_TABLE} (ip, score, grade, first_seen, last_seen)
            VALUES (%s, 0, 'suspicious', NOW(), NOW())
            ON DUPLICATE KEY UPDATE last_seen = NOW()
            """,
            (ip,),
        )
        if permanent:
            cursor.execute(
                f"""
                UPDATE {REPUTATION_TABLE}
                SET grade='perm_ban', ban_until=NULL, ban_reason=%s,
                    ban_count=ban_count+1, score=0, last_seen=NOW()
                WHERE ip=%s
                """,
                (reason, ip),
            )
        else:
            hours = max(1, min(int(hours or 1), 168))
            ban_until = datetime.now() + timedelta(hours=hours)
            cursor.execute(
                f"""
                UPDATE {REPUTATION_TABLE}
                SET grade='temp_ban', ban_until=%s, ban_reason=%s,
                    ban_count=ban_count+1, score=LEAST(score, 20), last_seen=NOW()
                WHERE ip=%s
                """,
                (ban_until, reason, ip),
            )
        db.commit()
        logger(f"[REPUTATION] BANNED | IP={ip} | reason={reason} | permanent={permanent}")
    except Exception as e:
        logger(f"[REPUTATION] ban_ip ERROR: {e}")
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass


def unban_ip(ip: str, restore_score: int = 100) -> bool:
    ensure_reputation_table()
    db = get_security_db()
    if db is None:
        return False
    try:
        cursor = db.cursor()
        cursor.execute(
            f"""
            UPDATE {REPUTATION_TABLE}
            SET grade='normal',
                ban_until=NULL,
                ban_reason=NULL,
                score=GREATEST(COALESCE(score, 0), %s),
                last_seen=NOW()
            WHERE ip=%s
            """,
            (int(restore_score), ip),
        )
        db.commit()
        logger(f"[REPUTATION] UNBANNED | IP={ip}")
        return cursor.rowcount > 0
    except Exception as e:
        logger(f"[REPUTATION] unban_ip ERROR: {e}")
        return False
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass


def trust_ip(ip: str, score: int = 250) -> None:
    ensure_reputation_table()
    db = get_security_db()
    if db is None:
        return
    score = max(100, min(int(score or 250), 900))
    try:
        cursor = db.cursor()
        cursor.execute(
            f"""
            INSERT INTO {REPUTATION_TABLE}
                (ip, score, grade, positive_requests, negative_points, first_seen, last_seen)
            VALUES (%s, %s, 'trusted', 5, 0, NOW(), NOW())
            ON DUPLICATE KEY UPDATE
                score = GREATEST(score, VALUES(score)),
                grade = 'trusted',
                ban_until = NULL,
                ban_reason = NULL,
                last_seen = NOW()
            """,
            (ip, score),
        )
        db.commit()
        logger(f"[REPUTATION] TRUSTED | IP={ip} | score>={score}")
    except Exception as e:
        logger(f"[REPUTATION] trust_ip ERROR: {e}")
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass


def is_fast_laned(ip: str) -> bool:
    return get_reputation_score(ip) >= GRADE_GOOD


def is_strict_check(ip: str) -> bool:
    return get_reputation_score(ip) < GRADE_NORMAL


def force_climb(ip: str, steps: int = 5) -> int:
    for _ in range(steps):
        record_good_behavior(ip, request_type="force_climb", full_update=True)
    final = get_reputation_score(ip)
    logger(f"[REPUTATION] FORCE_CLIMB | IP={ip[:8]}... | steps={steps} | final_score={final}")
    return final


def update_reputation_on_login_attempt(ip: str, username: str, success: bool = True) -> None:
    if success:
        record_good_behavior(ip, request_type="login_success", full_update=True)
        logger(f"[REPUTATION] LOGIN SUCCESS | IP={ip[:8]}... | user={username}")
    else:
        # Use REASON_SEVERITY (failed_login=1). Never force severity=3 — that
        # was banning known-good IPs after one typo.
        record_bad_behavior(ip, reason="failed_login")
        logger(f"[REPUTATION] LOGIN FAILED | IP={ip[:8]}... | user={username}")


def reconcile_ip_from_events(ip: str, *, max_events: int = 200) -> dict:
    """
    Recompute score from recent security events WITHOUT wiping good history.

    CRITICAL: never reset positive_requests to 0. An IP with thousands of good
    requests must not become temp-banned just because reconcile ran.
    Soft events (failed_login, rate_limit, etc.) cannot alone ban established IPs.
    """
    ip = (ip or "").strip()
    if not ip:
        return {}
    ensure_reputation_table()
    existing = get_reputation_info(ip)
    if (existing.get("grade") or "") == "perm_ban":
        return existing

    db = get_security_db()
    if db is None:
        return existing or {}
    try:
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT event_type, notes
            FROM pbt_security_events
            WHERE ip = %s
            ORDER BY COALESCE(timestamp, created_at) DESC, id DESC
            LIMIT %s
            """,
            (ip, int(max_events)),
        )
        rows = list(cursor.fetchall() or [])
    except Exception as e:
        logger(f"[REPUTATION] reconcile fetch ERROR: {e}")
        rows = []
    finally:
        try:
            db.close()
        except Exception:
            pass

    if not rows:
        return existing or get_reputation_info(ip)

    positive = int(existing.get("positive_requests") or 0)
    prev_score = int(
        existing.get("score") if existing.get("score") is not None else INITIAL_REPUTATION
    )
    existing_neg = int(existing.get("negative_points") or 0)

    soft_sev = 0
    hard_sev = 0
    soft_n = 0
    hard_n = 0
    for r in rows:
        et = (r.get("event_type") or "suspicious") if isinstance(r, dict) else "suspicious"
        key = str(et).strip().lower()
        sev = severity_for_reason(et)
        if key in SOFT_BAD_REASONS or sev <= 1:
            soft_sev += sev
            soft_n += 1
        else:
            hard_sev += sev
            hard_n += 1

    # Cap event mass so ancient logs don't erase a long good history
    soft_sev = min(soft_sev, 40)
    hard_sev = min(hard_sev, 60)
    soft_penalty = soft_sev * SOFT_BAD_POINTS_PER_SEVERITY
    hard_penalty = hard_sev * BAD_POINTS_PER_SEVERITY
    total_penalty = soft_penalty + hard_penalty

    established = (
        positive >= TRUST_POSITIVE_SHIELD
        or prev_score >= TRUST_SCORE_SHIELD
        or (existing.get("grade") or "").lower() in ("trusted", "good")
    )

    # Floor: keep most of the earned score when they have real positive history
    # 2400 positives should never land near ban territory from soft noise alone.
    if established:
        soft_penalty = min(soft_penalty, 25)
        if hard_n == 0:
            hard_penalty = 0
        total_penalty = soft_penalty + hard_penalty
        # Score = blend of previous trajectory and event penalty, with hard floor
        score = _clamp_score(prev_score - total_penalty)
        score = max(score, AUTO_TEMP_BAN_SCORE + 25)  # stay out of auto-ban band
        if positive >= 500:
            score = max(score, GRADE_SUSPICIOUS + 20)
        if positive >= 2000:
            score = max(score, GRADE_NORMAL)
    else:
        score = _clamp_score(INITIAL_REPUTATION - total_penalty)

    # Neg points: add event severity, never erase prior positives
    neg = max(existing_neg, min(soft_sev + hard_sev, 500))

    grade = _get_grade(score)
    ban_until = None
    ban_reason = None

    # Auto temp-ban only for hard abuse on weak/unknown IPs
    allow_temp = score < AUTO_TEMP_BAN_SCORE and hard_n > 0 and not established
    if allow_temp and positive >= TRUST_POSITIVE_SHIELD:
        allow_temp = False
        score = max(score, AUTO_TEMP_BAN_SCORE + 10)
        grade = _get_grade(score)
    if allow_temp:
        grade = "temp_ban"
        hours = 6 if score <= 20 else 2
        ban_until = datetime.now() + timedelta(hours=hours)
        ban_reason = f"Reconciled: {hard_n} hard / {soft_n} soft events"
    else:
        # Keep active ban only if still active; otherwise clear expired labels
        until = existing.get("ban_until")
        if (
            (existing.get("grade") or "") == "temp_ban"
            and until is not None
            and until > datetime.now()
            and hard_n > 0
            and not established
        ):
            grade = "temp_ban"
            ban_until = until
            ban_reason = existing.get("ban_reason")
        else:
            # Clear stale temp ban if reconcile says they're fine
            if (existing.get("grade") or "") == "temp_ban" and established:
                ban_until = None
                ban_reason = None

    db = get_security_db()
    if db is None:
        return get_reputation_info(ip)
    try:
        cursor = db.cursor()
        cursor.execute(
            f"""
            INSERT INTO {REPUTATION_TABLE}
                (ip, score, grade, positive_requests, negative_points,
                 ban_until, ban_reason, first_seen, last_seen, last_bad_behavior)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), NOW())
            ON DUPLICATE KEY UPDATE
                score = VALUES(score),
                grade = VALUES(grade),
                positive_requests = GREATEST(COALESCE(positive_requests, 0), VALUES(positive_requests)),
                negative_points = GREATEST(COALESCE(negative_points, 0), VALUES(negative_points)),
                ban_until = VALUES(ban_until),
                ban_reason = VALUES(ban_reason),
                last_bad_behavior = NOW(),
                last_seen = NOW()
            """,
            (
                ip,
                score,
                grade,
                positive,  # NEVER write 0 over real history
                neg,
                ban_until,
                ban_reason,
            ),
        )
        db.commit()
        logger(
            f"[REPUTATION] RECONCILED | IP={ip} | events={len(rows)} "
            f"soft={soft_n}/{soft_sev} hard={hard_n}/{hard_sev} "
            f"pos={positive} score={score} grade={grade}"
        )
    except Exception as e:
        logger(f"[REPUTATION] reconcile write ERROR: {e}")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        try:
            db.close()
        except Exception:
            pass
    return get_reputation_info(ip)


def reconcile_top_offenders(limit: int = 50) -> list[dict]:
    """Reconcile reputation for IPs with the most security events."""
    db = get_security_db()
    if db is None:
        return []
    ips = []
    try:
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT ip, COUNT(*) AS c
            FROM pbt_security_events
            WHERE ip IS NOT NULL AND ip != '' AND ip != '0.0.0.0'
            GROUP BY ip
            ORDER BY c DESC
            LIMIT %s
            """,
            (int(limit),),
        )
        ips = [r["ip"] for r in (cursor.fetchall() or []) if r.get("ip")]
    except Exception as e:
        logger(f"[REPUTATION] reconcile_top_offenders list ERROR: {e}")
    finally:
        try:
            db.close()
        except Exception:
            pass
    results = []
    for ip in ips:
        info = reconcile_ip_from_events(ip)
        info = dict(info or {})
        info["ip"] = ip
        results.append(info)
    return results


def stuck_high_score_offenders(limit: int = 40) -> list[str]:
    """
    IPs with multiple security events but reputation still looking 'clean'
    (legacy fake score=100 rows or never-penalized).
    """
    db = get_security_db()
    if db is None:
        return []
    try:
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT e.ip AS ip, COUNT(*) AS c, COALESCE(r.score, 100) AS score
            FROM pbt_security_events e
            LEFT JOIN pbt_reputation r ON r.ip = e.ip
            WHERE e.ip IS NOT NULL AND e.ip != '' AND e.ip != '0.0.0.0'
            GROUP BY e.ip, r.score
            HAVING c >= 3 AND COALESCE(r.score, 100) >= 85
            ORDER BY c DESC
            LIMIT %s
            """,
            (int(limit),),
        )
        return [r["ip"] for r in (cursor.fetchall() or []) if r.get("ip")]
    except Exception as e:
        logger(f"[REPUTATION] stuck_high_score_offenders ERROR: {e}")
        return []
    finally:
        try:
            db.close()
        except Exception:
            pass


_bootstrap_done = False


def bootstrap_reputation_from_history(*, force: bool = False, limit: int = 50) -> dict:
    """
    One-shot (per process) repair: recalc reputation for IPs that have attack
    history but still sit at a high score from the old broken scorer.
    Safe to call on app start and when opening the Security console.
    """
    global _bootstrap_done
    if _bootstrap_done and not force:
        return {"ran": False, "reason": "already_done", "fixed": 0}
    stuck = stuck_high_score_offenders(limit=limit)
    if not stuck and not force:
        _bootstrap_done = True
        return {"ran": False, "reason": "none_stuck", "fixed": 0}
    ips = stuck if stuck else []
    if force and not ips:
        # force full top-offender pass
        results = reconcile_top_offenders(limit=limit)
        _bootstrap_done = True
        return {"ran": True, "reason": "force_top", "fixed": len(results), "results": results}
    results = []
    for ip in ips:
        info = reconcile_ip_from_events(ip)
        info = dict(info or {})
        info["ip"] = ip
        results.append(info)
    _bootstrap_done = True
    logger(f"[REPUTATION] bootstrap fixed {len(results)} stuck high-score offender IP(s)")
    return {"ran": True, "reason": "stuck_high_score", "fixed": len(results), "results": results}


# Lean: no module-load banner (set PBT_LOG_VERBOSE=1 if you need chatter)

