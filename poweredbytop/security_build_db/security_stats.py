# ================================================================
# poweredbytop/security_build_db/security_stats.py
# Full path: poweredbytop/security_build_db/security_stats.py
# File name: security_stats.py
# Purpose: Creates the pbt_attack_stats table (MariaDB only)
# Version: 2026-05-09-1 CLEAN REBUILD
# Security classification: CRITICAL
# ================================================================
# DESIGN PRINCIPLES (non-negotiable):
# 1. Uses pbt_ prefix on ALL tables and indexes
# 2. Plain ASCII only - no special characters
# 3. CREATE IF NOT EXISTS - safe to run multiple times
# 4. Performance indexes for fast lookups
# ================================================================

def create_tables(cursor):
    """Create the pbt_attack_stats table with robust schema"""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pbt_attack_stats (
            attack_type VARCHAR(50) PRIMARY KEY,
            total_attempts INT DEFAULT 0,
            blocked_count INT DEFAULT 0,
            last_attack_ip VARCHAR(45),
            last_attack_time DATETIME,
            severity_level INT DEFAULT 1,
            notes TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # Performance indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pbt_stats_type ON pbt_attack_stats(attack_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pbt_stats_severity ON pbt_attack_stats(severity_level)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pbt_stats_last ON pbt_attack_stats(last_attack_time)")

    print("Table created: pbt_attack_stats")

print("poweredbytop/security_build_db/security_stats.py - 100% fresh rebuild loaded (pbt_ prefix + clean ASCII)")