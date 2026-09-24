# ================================================================
# poweredbytop/config/settings.py
# ALL CONSTANTS FOR FULL SOVEREIGN SECURITY PIPELINE
# 100% FRESH REBUILD - CLEAN - NO DUPLICATES - SECURITY FIRST
# ================================================================
# MARIADB ONLY - EXACT DB TABLES ONLY - WORKS WITH core/security.py
# LIGHTWEIGHT JSON FALLBACK ENABLED FOR NORMAL TRAFFIC
# ================================================================
import os
import logging
from pathlib import Path

logger = logging.getLogger("poweredbytop.config")

# ====================== PROJECT ROOT & .env LOADING ======================
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
ENV_PATH = PROJECT_ROOT / ".env"

if ENV_PATH.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_PATH)
        logger.info(f".env loaded from {ENV_PATH}")
    except ImportError:
        logger.warning(".env file found but python-dotenv not installed - using os.environ only")
else:
    logger.info("No .env file - using secure defaults (safe for dev)")

# ====================== DATABASE CONFIGURATION ======================
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3307"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "aegis")
DB_CHARSET = os.getenv("DB_CHARSET", "utf8mb4")

# ====================== CORE SECURITY FLAGS ======================
CLOUDFLARE_ENABLED = False
# Local laptop: set DEBUG_MODE=true and REQUIRE_HTTPS=false in .env
DEBUG_MODE = os.getenv("DEBUG_MODE", "False").lower() in ("1", "true", "yes")
REQUIRE_HTTPS = os.getenv("REQUIRE_HTTPS", "true" if not DEBUG_MODE else "false").lower() in ("1", "true", "yes")
SESSION_COOKIE_SECURE = os.getenv(
    "SESSION_COOKIE_SECURE",
    "false" if DEBUG_MODE or not REQUIRE_HTTPS else "true",
).lower() in ("1", "true", "yes")


# ====================== FULL PIPELINE CONTROL ======================
FULL_SECURITY_PIPELINE_ENABLED = True
WRITE_PASS_FAIL_TO_DB = True          # Master switch (we will make this smarter in security.py)
# Soft failures (rate limit, vetting, low score) must NOT 403 every page.
# Hard blocks only: attack paths, scanners, active IP bans.
BLOCK_ON_ANY_FAILURE = False

# ====================== RATE LIMITING & DDoS / REFRESH SPAM ======================
GLOBAL_RATE_LIMIT = 800
PER_IP_RATE_LIMIT = 180
RATE_WINDOW_SECONDS = 60
STAGGER_DELAY_MS = 150
STAGGER_DELAY = 0.8
BURST_TOLERANCE = 5
JAIL_THRESHOLD = 10
JAIL_DURATION_SECONDS = 300

# ====================== BRUTE FORCE PROTECTION ======================
BRUTE_FORCE_MAX_ATTEMPTS = 5
BRUTE_FORCE_JAIL_SECONDS = 300

# ====================== REPUTATION SYSTEM ======================
INITIAL_REPUTATION = 80
GOOD_BEHAVIOR_BONUS = 5
BAD_BEHAVIOR_PENALTY = 15
REPUTATION_DECAY_PER_HOUR = 1

# ====================== REPUTATION-BASED STAGGER SYSTEM ======================
REPUTATION_STAGGER_ENABLED = True
REPUTATION_STAGGER_MIN = 1
REPUTATION_STAGGER_MAX = 25
REPUTATION_STAGGER_SCORE_DIVISOR = 200
REPUTATION_STAGGER_POSITIVE_DIVISOR = 5000

# ====================== LIGHTWEIGHT JSON FALLBACK LOGGING ======================
# Normal / trusted traffic → fast JSON append (survives worker death)
# Only critical events or every Nth request do real DB writes
# This is the core change to stop signal 15 kills from logging load
LIGHTWEIGHT_LOGGING_ENABLED = True
TRAFFIC_LOG_PATH = PROJECT_ROOT / "logs" / "traffic_log.jsonl"
TRAFFIC_LOG_MAX_SIZE_MB = 8              # Opportunistic flush triggered at this size
TRAFFIC_LOG_FLUSH_BATCH_SIZE = 40        # Max records flushed in one opportunistic attempt
TRAFFIC_LOG_NORMAL_DB_FREQUENCY = 12     # Only write normal/trusted traffic to DB every N requests
CRITICAL_STATUSES_FORCE_DB = ["blocked", "rate_limited", "attack", "suspicious", "banned", "jail"]

# ====================== DB GUARD / N+1 / SQL INJECTION PROTECTION ======================
DB_CONNECTION_TIMEOUT = 5
DB_QUERY_TIMEOUT_SECONDS = 30
N1_QUERY_THRESHOLD = 15
DB_BULKHEAD_ENABLED = True
SQLI_PROTECTION_ENABLED = True

# ====================== DB GUARD CONSTANTS (tuned for cPanel MariaDB) ======================
DB_MAX_RETRIES = 5
DB_BASE_BACKOFF_SECONDS = 0.3
DB_MAX_BACKOFF_SECONDS = 5.0
DB_POOL_RECYCLE_SECONDS = 300
DB_BULKHEAD_MAX_CONCURRENT = 25

# ====================== TOKEN / FIREWALL ======================
# Firewall HMAC only. Never reuse Flask SECRET_KEY — one secret must not
# sign both session cookies and firewall tokens.
TOKEN_SECRET = (os.getenv("PBT_TOKEN_SECRET") or "").strip() or "CHANGE-THIS-TO-64-CHAR-RANDOM-STRING-NOW"
TOKEN_LIFETIME_SECONDS = 3600
HMAC_ALGORITHM = "sha256"

# ====================== BOT / SCRAPER / HACKER PROTECTION ======================
# Do NOT use bare "bot" or "java" — they false-positive real browsers / strings.
SUSPICIOUS_UA_KEYWORDS = [
    "crawler", "spider", "curl/", "wget/", "python-requests",
    "scrapy", "go-http-client", "sqlmap", "nikto",
]
ALLOWED_COUNTRIES = []
BLOCKED_COUNTRIES = []

# ====================== SESSION & AUTH ======================
# Fallback only. init_security binds a per-site name from site_profile.py:
#   aegis  -> pbt_aegis_session
#   aegisx -> pbt_aegisx_session
#   ax     -> pbt_ax_session
#   family -> pbt_family_session
SESSION_COOKIE_NAME = "pbt_vetted_session"
VETTED_SESSION_TTL = 86400
CSRF_PROTECTION = True

# ====================== LOGGING ======================
LOG_LEVEL = "INFO"
LOG_SECURITY_EVENTS = True

# ====================== FALLBACK ======================
GRACEFUL_DEGRADATION = True
HUB_TIMEOUT_SECONDS = 3.0

# ====================== HUB & GATEKEEPER ======================
# Multi-domain browsing across this fleet is NORMAL (owner/sponsor hopping
# aegis ↔ aegisx ↔ ax ↔ landing). That is NOT a threat signal by itself.
# Reputation is shared on purpose; hard blocks must not treat fleet hops as scanning.
PBT_HUB_DOMAINS = [
    "poweredby.top",
    "hub.poweredby.top",
    "www.poweredby.top",
    "aegis.poweredby.top",
    "aegisx.poweredby.top",
    "ax.poweredby.top",
    "family.poweredby.top",
    "www.family.poweredby.top",
    "new.poweredby.top",
    "www.new.poweredby.top",
    "myvineos.poweredby.top",
    "myvinechurch.online",
    "www.myvinechurch.online",
]
# Comma-separated owner/ops IPs that never hard-block (see helpers.is_trusted_ip)
# Set in .env: PBT_TRUSTED_IPS=1.2.3.4,5.6.7.8
PBT_TRUSTED_IPS_RAW = os.getenv("PBT_TRUSTED_IPS", "")
PBT_SECRET_KEY = (os.getenv("PBT_SECRET_KEY") or os.getenv("SECRET_KEY") or "").strip()
PBT_INTERNAL_FALLBACK = True

# ====================== PRINT ON LOAD ======================
logger.info("poweredbytop/config/settings.py - 100% FRESH REBUILD LOADED")
logger.info("LIGHTWEIGHT_LOGGING_ENABLED=True | JSON fallback active for normal traffic")
logger.info(f"TRAFFIC_LOG_PATH={TRAFFIC_LOG_PATH}")
logger.info(f"TRAFFIC_LOG_NORMAL_DB_FREQUENCY={TRAFFIC_LOG_NORMAL_DB_FREQUENCY} (every Nth normal request writes to DB)")
logger.info("CLOUDFLARE_ENABLED=False | FULL_SECURITY_PIPELINE_ENABLED=True")
logger.info(f"GLOBAL_RATE_LIMIT={GLOBAL_RATE_LIMIT} | PER_IP_RATE_LIMIT={PER_IP_RATE_LIMIT}")
logger.info(f"REPUTATION_STAGGER_ENABLED={REPUTATION_STAGGER_ENABLED}")
logger.info(f"DB Config: {DB_HOST}:{DB_PORT} / {DB_NAME}")