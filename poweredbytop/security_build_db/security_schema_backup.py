# ================================================================
# poweredbytop/security_build_db/security_schema_backup.py
# Snapshot / restore helpers for pbt_* security tables (reversal).
# Does NOT drop live tables unless restore is explicitly run.
# ================================================================
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from poweredbytop.models.connect_db import get_security_db, close_security_db

# Tables we may snapshot (order: parents first for restore notes)
PBT_TABLES = (
    "pbt_reputation",
    "pbt_security_events",
    "pbt_traffic",
    "pbt_attack_stats",
    "pbt_device_prints",
    "pbt_device_bans",
    "pbt_device_trust",
)


def _backup_dir() -> Path:
    root = Path(__file__).resolve().parents[2]
    d = root / "backups" / "pbt_security"
    d.mkdir(parents=True, exist_ok=True)
    return d


def snapshot_pbt_tables(label: str | None = None, limit_per_table: int = 50000) -> str | None:
    """
    Dump row counts + sample JSON of pbt_* tables for emergency review.
    Full mysqldump is preferred on HostM; this is an app-level safety net.
    Returns path to snapshot file or None.
    """
    db = get_security_db()
    if db is None:
        return None
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    name = f"pbt_snapshot_{stamp}_{label or 'manual'}.json"
    path = _backup_dir() / name
    out: dict = {"created_at": stamp, "label": label, "tables": {}}
    try:
        cur = db.cursor()
        for table in PBT_TABLES:
            try:
                cur.execute(f"SHOW TABLES LIKE %s", (table,))
                if not cur.fetchone():
                    out["tables"][table] = {"exists": False}
                    continue
                cur.execute(f"SELECT COUNT(*) AS c FROM `{table}`")
                count = int((cur.fetchone() or {}).get("c") or 0)
                cur.execute(f"SELECT * FROM `{table}` LIMIT %s", (int(limit_per_table),))
                rows = cur.fetchall() or []
                # Normalize datetimes
                clean = []
                for r in rows:
                    if isinstance(r, dict):
                        clean.append(
                            {
                                k: (v.isoformat() if hasattr(v, "isoformat") else v)
                                for k, v in r.items()
                            }
                        )
                out["tables"][table] = {
                    "exists": True,
                    "count": count,
                    "rows_exported": len(clean),
                    "truncated": count > limit_per_table,
                    "rows": clean,
                }
            except Exception as e:
                out["tables"][table] = {"exists": None, "error": str(e)[:200]}
        path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        print(f"[PBT BACKUP] Snapshot written: {path}")
        return str(path)
    except Exception as e:
        print(f"[PBT BACKUP] snapshot failed: {e}")
        return None
    finally:
        close_security_db(db)


def list_snapshots() -> list[str]:
    d = _backup_dir()
    return sorted(str(p) for p in d.glob("pbt_snapshot_*.json"))


def create_tables(cursor):
    """No table - backup helper only. Called safely from orchestrator."""
    # Ensure backup dir exists on build
    try:
        _backup_dir()
        print("PBT schema backup dir ready")
    except Exception as e:
        print(f"PBT backup dir warning: {e}")
