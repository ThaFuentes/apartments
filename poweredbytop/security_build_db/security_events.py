# ================================================================
# poweredbytop/security_build_db/security_events.py
# pbt_security_events - full audit fields (device, path, user, ua)
# CREATE IF NOT EXISTS + safe column evolve
# ================================================================


def _add_col(cursor, table: str, col: str, ddl: str) -> None:
    try:
        cursor.execute(
            """
            SELECT COUNT(*) AS c FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s AND COLUMN_NAME = %s
            """,
            (table, col),
        )
        row = cursor.fetchone()
        # Dict or tuple cursor
        count = 0
        if isinstance(row, dict):
            count = int(row.get("c") or 0)
        elif row:
            count = int(row[0] or 0)
        if count == 0:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            print(f"  + column {table}.{col}")
    except Exception as e:
        print(f"  column evolve {table}.{col}: {e}")


def create_tables(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS pbt_security_events (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            event_type VARCHAR(50) NOT NULL,
            ip VARCHAR(45) NOT NULL,
            device_fp VARCHAR(40) NULL,
            user_id INT NULL,
            path VARCHAR(255) NULL,
            method VARCHAR(10) NULL,
            user_agent VARCHAR(255) NULL,
            reputation_score INT DEFAULT 100,
            behavior_grade VARCHAR(20) DEFAULT 'normal',
            ban_until DATETIME NULL,
            ban_reason TEXT NULL,
            ban_count INT DEFAULT 0,
            notes TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            KEY idx_pbt_events_ip (ip),
            KEY idx_pbt_events_type (event_type),
            KEY idx_pbt_events_grade (behavior_grade),
            KEY idx_pbt_events_device (device_fp),
            KEY idx_pbt_events_user (user_id),
            KEY idx_pbt_events_created (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    # Evolve legacy installs
    for col, ddl in (
        ("device_fp", "VARCHAR(40) NULL"),
        ("user_id", "INT NULL"),
        ("path", "VARCHAR(255) NULL"),
        ("method", "VARCHAR(10) NULL"),
        ("user_agent", "VARCHAR(255) NULL"),
    ):
        _add_col(cursor, "pbt_security_events", col, ddl)

    print("Table ready: pbt_security_events (full audit columns)")
