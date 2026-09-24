# poweredbytop/models/connect_db.py
# Full path: poweredbytop/models/connect_db.py
# File name: connect_db.py
# Brief detailed purpose: Security DB layer - STRICTLY uses root dbconnector.py. Handles cPanel socket issues and background threads. Graceful fallback so security features still run. FULL REBUILD with UTF8MB4 forced everywhere, cleaner import from root dbconnector, safer ensure_table_exists, and background-thread safety.
"""
SECURITY DB CONNECTOR
- Never raises unhandled exceptions
- Works with .env MYSQL_* variables via root dbconnector.py
- Handles cPanel socket issues and background threads
- Graceful fallback so security features still run
- UTF8MB4 FORCED + pool_recycle alignment with root dbconnector
"""
import os
import time
import sys
from poweredbytop.utils.helpers import logger

# ====================== IMPORT ROOT DBCONNECTOR (CLEAN & SAFE) ======================
DATABASE_URI = None
MYSQL_HOST = "127.0.0.1"
MYSQL_PORT = "3306"
MYSQL_DATABASE = None
try:
    # Direct import from the root dbconnector we just rebuilt
    from dbconnector import DATABASE_URI, MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE
    logger("connect_db.py -> Successfully imported root dbconnector.py")
except ImportError:
    # Emergency fallback (only if import fails)
    fallback_path = "/home/ua882038/public_html/apt.poweredby.top/dbconnector.py"
    if os.path.exists(fallback_path):
        import importlib.util
        spec = importlib.util.spec_from_file_location("dbconnector", fallback_path)
        dbconnector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dbconnector)
        DATABASE_URI = getattr(dbconnector, "DATABASE_URI", None)
        MYSQL_HOST = getattr(dbconnector, "MYSQL_HOST", "127.0.0.1")
        MYSQL_PORT = getattr(dbconnector, "MYSQL_PORT", "3306")
        MYSQL_DATABASE = getattr(dbconnector, "MYSQL_DATABASE", None)
        logger("connect_db.py -> Used fallback import for dbconnector")
    else:
        logger("WARNING: dbconnector.py not found at expected path")

# ====================== CONNECTION FUNCTION ======================
def get_security_db():
    """Return pymysql connection using ONLY the root DATABASE_URI. Safe for background threads."""
    if not DATABASE_URI:
        logger("[DB] No DATABASE_URI available - security logging disabled")
        return None
    import pymysql
    db = None
    for attempt in range(4):
        try:
            # Safe URI parsing
            uri = DATABASE_URI.replace("mysql+pymysql://", "")
            auth_part, host_part = uri.split("@", 1)
            user, password = auth_part.split(":", 1)
            host_db_part = host_part.split("?", 1)[0]
            if ":" in host_db_part and "/" in host_db_part:
                host_port, db_name = host_db_part.split("/", 1)
                host, port_str = host_port.split(":", 1)
                port = int(port_str)
            else:
                host_db_split = host_db_part.split("/", 1)
                host = host_db_split[0]
                db_name = host_db_split[1] if len(host_db_split) > 1 else (MYSQL_DATABASE or "ua882038_family")
                port = int(MYSQL_PORT)
            # Connection parameters - UTF8MB4 + cPanel ready (aligned with hardened root dbconnector)
            connect_params = {
                "user": user,
                "password": password,
                "database": db_name,
                "charset": "utf8mb4",
                "init_command": "SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci",
                "cursorclass": pymysql.cursors.DictCursor,
                "connect_timeout": 15,
                "read_timeout": 35,
                "write_timeout": 35,
            }
            # cPanel unix socket optimization
            if host in ("127.0.0.1", "localhost"):
                socket_path = "/var/lib/mysql/mysql.sock"
                if os.path.exists(socket_path):
                    connect_params["unix_socket"] = socket_path
                    logger("[DB] Using cPanel unix socket")
                else:
                    connect_params["host"] = host
                    connect_params["port"] = port
            else:
                connect_params["host"] = host
                connect_params["port"] = port
            db = pymysql.connect(**connect_params)
            # Health check
            with db.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            logger("[DB] Secure connection established")
            return db
        except Exception as e:
            if db:
                try:
                    db.close()
                except:
                    pass
            if attempt == 3:
                logger("[DB ERROR] All 4 retries failed: " + str(e))
                return None
            backoff = 0.4 * (attempt + 1)
            time.sleep(backoff)
            logger("[DB] Retry " + str(attempt+1) + "/4")
    return None

def close_security_db(db_connection=None):
    """Safely close connection"""
    if db_connection and hasattr(db_connection, "close"):
        try:
            db_connection.close()
        except:
            pass

def ensure_table_exists(table_name: str, create_sql: str):
    """Create table if missing - silent on failure + UTF8MB4 forced"""
    db = get_security_db()
    if not db:
        return False
    try:
        with db.cursor() as cursor:
            cursor.execute("SHOW TABLES LIKE %s", (table_name,))
            if not cursor.fetchone():
                # Force UTF8MB4 on every table creation
                full_sql = f"CREATE TABLE IF NOT EXISTS `{table_name}` ({create_sql}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
                cursor.execute(full_sql)
                db.commit()
                logger("[DB] Table created: " + table_name + " (UTF8MB4 enforced)")
                return True
            else:
                logger("[DB] Table already exists: " + table_name)
                return True
    except Exception as e:
        logger("[DB] ensure_table_exists failed for " + table_name + ": " + str(e))
        return False
    finally:
        close_security_db(db)

def get_sqlalchemy_config():
    """Return config that matches the hardened root dbconnector.py (recycle=120 + reset_on_return)"""
    if not DATABASE_URI:
        return {}
    return {
        "SQLALCHEMY_DATABASE_URI": DATABASE_URI,
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "SQLALCHEMY_ENGINE_OPTIONS": {
            "pool_pre_ping": True,
            "pool_recycle": 120,
            "pool_size": 3,
            "max_overflow": 30,
            "pool_timeout": 45,
            "pool_reset_on_return": "commit",
        }
    }

logger("poweredbytop/models/connect_db.py - FULL REBUILD COMPLETE (UTF8MB4 forced + clean root dbconnector import + background safe + aligned pool settings)")