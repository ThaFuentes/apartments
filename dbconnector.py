# dbconnector.py — apt.poweredby.top
# Product data and PoweredByTop pbt_* tables share this MariaDB.
# There is no SQLite fallback. Do not URL-encode the password;
# connect_db.py splits the URI raw.
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.exc import DisconnectionError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import Pool

load_dotenv()

MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = os.getenv("MYSQL_PORT", "3306")
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE")

if not all([MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE]):
    raise RuntimeError(
        "apt.poweredby.top needs MariaDB. Set MYSQL_HOST, MYSQL_PORT, "
        "MYSQL_USER, MYSQL_PASSWORD, and MYSQL_DATABASE."
    )

DATABASE_URI = (
    f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@"
    f"{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}"
)

print(
    f"[apt dbconnector] MariaDB {MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE} user {MYSQL_USER}"
)

engine = create_engine(
    DATABASE_URI,
    pool_pre_ping=True,
    pool_recycle=50,
    pool_size=2,
    max_overflow=5,
    pool_timeout=45,
    pool_reset_on_return="rollback",
    echo=False,
    connect_args={
        "charset": "utf8mb4",
        "connect_timeout": 20,
        "read_timeout": 45,
        "write_timeout": 45,
    },
)


@event.listens_for(Pool, "checkout")
def _on_checkout(dbapi_conn, connection_rec, connection_proxy):
    try:
        cursor = dbapi_conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        cursor.close()
    except Exception as exc:
        connection_rec.invalidate()
        raise DisconnectionError(str(exc)) from exc


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_engine():
    return engine
