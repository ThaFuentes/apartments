# ================================================================
# poweredbytop/security_build_db/security_device_prints.py
# Creates pbt_device_prints + pbt_device_bans (MariaDB, IF NOT EXISTS)
# ================================================================


def create_tables(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS pbt_device_prints (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            device_fp VARCHAR(40) NOT NULL,
            ip VARCHAR(45) NOT NULL,
            ua_hash VARCHAR(40) NULL,
            user_agent VARCHAR(255) NULL,
            accept_language VARCHAR(80) NULL,
            user_id INT NULL,
            hit_count INT NOT NULL DEFAULT 1,
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_path VARCHAR(255) NULL,
            last_method VARCHAR(10) NULL,
            risk_score INT NOT NULL DEFAULT 0,
            notes TEXT NULL,
            UNIQUE KEY uq_device_ip (device_fp, ip),
            KEY idx_pbt_dev_ip (ip),
            KEY idx_pbt_dev_fp (device_fp),
            KEY idx_pbt_dev_user (user_id),
            KEY idx_pbt_dev_last (last_seen)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS pbt_device_bans (
            device_fp VARCHAR(40) PRIMARY KEY,
            ban_until DATETIME NULL,
            ban_reason VARCHAR(500) NULL,
            ban_count INT NOT NULL DEFAULT 0,
            permanent TINYINT(1) NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            KEY idx_pbt_devban_until (ban_until)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS pbt_device_trust (
            device_fp VARCHAR(40) PRIMARY KEY,
            score INT NOT NULL DEFAULT 250,
            grade VARCHAR(20) NOT NULL DEFAULT 'trusted',
            notes VARCHAR(500) NULL,
            trusted_by INT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            KEY idx_pbt_devtrust_grade (grade)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    print("Table created: pbt_device_prints + pbt_device_bans + pbt_device_trust")
