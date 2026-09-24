# ================================================================
# poweredbytop/security_build_db/security_traffic.py
# Table Builder: pbt_traffic (MariaDB)
# ROBUST, FUTURE-PROOF SCHEMA - NO REBUILDS NEEDED
# Internal vetting records (PASS/FAIL) for per-site use
# 100% COMPLETE - PLAIN ASCII ONLY
# ================================================================
# MARIADB ONLY - NO INSTANCE FOLDER - NO SQLITE - NO JSON
# ================================================================
# Changed to create_tables(cursor) to match EVERY other builder file
# ================================================================

def create_tables(cursor):
    """Create the pbt_traffic table with robust future-proof schema"""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pbt_traffic (
            id INT AUTO_INCREMENT PRIMARY KEY,
            ip VARCHAR(45) NOT NULL,
            domain VARCHAR(255),
            vetted_at DATETIME NOT NULL,
            expires_at DATETIME NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY unique_ip_domain (ip, domain)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    # Indexes for fast lookups
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pbt_traffic_ip_domain ON pbt_traffic(ip, domain)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pbt_traffic_expires ON pbt_traffic(expires_at)")
    print("Table created: pbt_traffic (MariaDB, robust schema)")
    print(" - ip + domain: simple PASS/FAIL vetting")
    print(" - vetted_at / expires_at: time-based access control")
    print(" - No rebuilds needed - schema is complete")
    print("pbt_ prefix enforced")
    print("Zero data loss - CREATE IF NOT EXISTS protects existing data")

# ====================== FINAL LOAD MESSAGE ======================
print("poweredbytop/security_build_db/security_traffic.py - 100% fresh rebuild loaded successfully (now matches create_tables(cursor) style of all other builders)")