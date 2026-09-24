# ================================================================
# poweredbytop/security_build_db/security_build_db.py
# Full path: poweredbytop/security_build_db/security_build_db.py
# File name: security_build_db.py
# Purpose: Master Security Database Orchestrator
# Version: 2026-05-09-1 CLEAN PRODUCTION BUILD
# Security classification: CRITICAL
# ================================================================
# DESIGN PRINCIPLES (non-negotiable):
# 1. Aggressive but safe path fixing for your cPanel environment
# 2. No fancy Unicode characters (no more ASCII codec errors)
# 3. Clean, sequential table building
# 4. Full error handling and logging
# ================================================================

import os
import sys

# ====================== AGGRESSIVE PATH FIX ======================
current_file = os.path.abspath(__file__)
security_db_dir = os.path.dirname(current_file)
poweredbytop_dir = os.path.dirname(security_db_dir)
site_root = os.path.dirname(poweredbytop_dir)

for path in [site_root, poweredbytop_dir, security_db_dir]:
    if path not in sys.path:
        sys.path.insert(0, path)

print(f"PATH FIX: Added {site_root}")
print(f"PATH FIX: Added {poweredbytop_dir}")
print(f"Current dir: {os.getcwd()}")
print(f"sys.path top 3: {sys.path[:3]}")

# ====================== SAFE IMPORT ======================
try:
    from poweredbytop.models.connect_db import get_security_db
    print("SUCCESS: Imported get_security_db from poweredbytop.models.connect_db")
except Exception as e:
    print(f"IMPORT FAILED: {e}")
    sys.exit(1)

print("=== POWEREDBYTOP SECURITY BUILD ORCHESTRATOR STARTED ===")

def build_all(verbose: bool = True):
    if verbose:
        print("=== STARTING FULL TABLE BUILD ===")

    db = get_security_db()
    if db is None:
        print("CRITICAL ERROR: get_security_db() returned None")
        return False

    cursor = db.cursor()

    # Snapshot before schema work when PBT_SECURITY_SNAPSHOT=1
    if (os.getenv("PBT_SECURITY_SNAPSHOT") or "").strip().lower() in ("1", "true", "yes", "on"):
        try:
            from poweredbytop.security_build_db.security_schema_backup import snapshot_pbt_tables
            snapshot_pbt_tables(label="pre_build")
        except Exception as snap_err:
            print(f"   WARNING: pre-build snapshot failed (non-fatal): {snap_err}")

    # List your build modules here in order
    ordered_modules = [
        'security_events',
        'security_stats',
        'security_traffic',
        'security_scorer',
        'security_device_prints',
        'security_schema_backup',
    ]

    for module_name in ordered_modules:
        print(f"--- Processing module: {module_name} ---")
        try:
            full_name = f"poweredbytop.security_build_db.{module_name}"
            module = __import__(full_name, fromlist=[''])
            print(f"   Module loaded successfully")

            # Try common function names
            for func_name in ['create_tables', 'create_table', 'build']:
                if hasattr(module, func_name):
                    func = getattr(module, func_name)
                    print(f"   Calling {func_name}()")
                    func(cursor)
                    print(f"   SUCCESS: {module_name} tables built")
                    break
            else:
                print(f"   WARNING: No create function found in {module_name}")
        except Exception as e:
            print(f"   ERROR in {module_name}: {e}")

    db.commit()
    print("\n=== FINAL TABLE CHECK ===")
    try:
        cursor.execute("SHOW TABLES LIKE 'pbt_%'")
        tables = cursor.fetchall()
        if tables:
            print("TABLES CREATED:")
            for t in tables:
                print(f"   OK - {t[0]}")
        else:
            print("WARNING: No pbt_ tables found")
    except Exception as e:
        print(f"Check error: {e}")

    print("=== BUILD COMPLETE ===")
    return True

if __name__ == "__main__":
    build_all(verbose=True)

print("poweredbytop/security_build_db/security_build_db.py - 100% fresh rebuild loaded (clean, no unicode, robust path fixing)")