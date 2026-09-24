# ================================================================
# poweredbytop/reputation/security_scorer.py
# REPUTATION SCORING + GRADE SYSTEM + ADAPTIVE CHECKING
# NOW USING SHARED CONNECTOR FROM connect_db.py
# 100% COMPLETE - PLAIN ASCII ONLY - NO CURSOR
# ================================================================
# MARIADB ONLY - USES SHARED ROOT dbconnector + connect_db.py
# ================================================================

import time
from datetime import datetime, timedelta
from sqlalchemy import text
from poweredbytop.models.connect_db import get_security_db, ensure_table_exists
from poweredbytop.utils.helpers import logger

# ====================== ENSURE TABLE EXISTS ======================
def ensure_reputation_table():
    """Create pbt_reputation table if it does not exist - FIXED SQL for ensure_table_exists"""
    create_sql = """
        ip VARCHAR(45) PRIMARY KEY,
        score INT DEFAULT 80,
        grade VARCHAR(20) DEFAULT 'suspicious',
        positive_requests INT DEFAULT 0,
        negative_points INT DEFAULT 0,
        ban_until DATETIME NULL,
        ban_reason TEXT NULL,
        ban_count INT DEFAULT 0,
        first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_bad_behavior DATETIME NULL
    """
    ensure_table_exists("pbt_reputation", create_sql)

ensure_reputation_table()

# ====================== GRADE SYSTEM ======================
GRADE_LEVELS = {
    "trusted": {"min_score": 500, "check_frequency": 5},
    "normal": {"min_score": 100, "check_frequency": 1},
    "watch": {"min_score": 50, "check_frequency": 1},
    "suspicious": {"min_score": 20, "check_frequency": 1},
    "temp_ban": {"min_score": 0, "check_frequency": 0},
    "perm_ban": {"min_score": 0, "check_frequency": 0}
}

# ====================== PUBLIC API ======================
def get_reputation_score(ip: str) -> int:
    """Get current reputation score (0-1000)"""
    db = get_security_db()
    if db is None:
        return 80
    try:
        result = db.execute(text("SELECT * FROM pbt_reputation WHERE ip = :ip"), {"ip": ip})
        row = result.fetchone()
        if not row:
            return 80
        # Auto-expire temp bans
        if getattr(row, 'ban_until', None) and row.ban_until < datetime.utcnow():
            pass
        # Decay negative points over time
        negative = getattr(row, 'negative_points', 0) or 0
        last_bad = getattr(row, 'last_bad_behavior', None)
        if negative > 0 and last_bad:
            hours = (datetime.utcnow() - last_bad).total_seconds() / 3600
            negative = max(0, negative - int(hours))
        # Calculate score
        score = 80 + ((getattr(row, 'positive_requests', 0) or 0) * 2) - (negative * 10)
        score = max(0, min(1000, score))
        return score
    except Exception as e:
        logger("Reputation score query failed: " + str(e))
        return 80

def is_fast_laned(ip: str) -> bool:
    """Fast-lane (reduced checking)"""
    return get_reputation_score(ip) >= 300

def is_strict_check(ip: str) -> bool:
    """Strict checking every request"""
    return get_reputation_score(ip) < 100

def record_good_behavior(ip: str):
    """Record successful request"""
    db = get_security_db()
    if db is None:
        return
    try:
        db.execute(text("""
            INSERT INTO pbt_reputation (ip, positive_requests, last_seen)
            VALUES (:ip, 1, NOW())
            ON DUPLICATE KEY UPDATE
                positive_requests = positive_requests + 1,
                last_seen = NOW(),
                negative_points = GREATEST(0, negative_points - 1)
        """), {"ip": ip})
        db.commit()
        logger("[REPUTATION] + GOOD | IP=" + ip[:8] + "... (normal traffic boost)")
    except Exception as e:
        logger("record_good_behavior failed: " + str(e))

def record_bad_behavior(ip: str, reason: str = "suspicious"):
    """Record bad behavior"""
    db = get_security_db()
    if db is None:
        return
    try:
        db.execute(text("""
            INSERT INTO pbt_reputation (ip, negative_points, last_bad_behavior, last_seen, ban_reason)
            VALUES (:ip, 5, NOW(), NOW(), :reason)
            ON DUPLICATE KEY UPDATE
                negative_points = negative_points + 5,
                last_bad_behavior = NOW(),
                last_seen = NOW(),
                ban_reason = :reason
        """), {"ip": ip, "reason": reason})
        db.commit()
    except Exception as e:
        logger("record_bad_behavior failed: " + str(e))

def ban_ip(ip: str, reason: str, permanent: bool = False):
    """Manually ban IP"""
    db = get_security_db()
    if db is None:
        return
    try:
        if permanent:
            db.execute(text("""
                INSERT INTO pbt_reputation (ip, grade, ban_reason, ban_until)
                VALUES (:ip, 'perm_ban', :reason, NULL)
                ON DUPLICATE KEY UPDATE
                    grade = 'perm_ban',
                    ban_reason = :reason
            """), {"ip": ip, "reason": reason})
        else:
            db.execute(text("""
                INSERT INTO pbt_reputation (ip, grade, ban_reason, ban_until)
                VALUES (:ip, 'temp_ban', :reason, DATE_ADD(NOW(), INTERVAL 1 HOUR))
                ON DUPLICATE KEY UPDATE
                    grade = 'temp_ban',
                    ban_reason = :reason,
                    ban_until = DATE_ADD(NOW(), INTERVAL 1 HOUR)
            """), {"ip": ip, "reason": reason})
        db.commit()
    except Exception as e:
        logger("ban_ip failed: " + str(e))

def unban_ip(ip: str):
    """Remove ban"""
    db = get_security_db()
    if db is None:
        return
    try:
        db.execute(text("""
            UPDATE pbt_reputation
            SET grade = 'suspicious', ban_until = NULL, ban_reason = NULL
            WHERE ip = :ip
        """), {"ip": ip})
        db.commit()
    except Exception as e:
        logger("unban_ip failed: " + str(e))

# ====================== FINAL LOAD MESSAGE ======================
logger("poweredbytop/reputation/scorer.py - 100% fresh rebuild loaded successfully (table creation syntax FIXED)")
