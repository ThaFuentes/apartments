# ================================================================
# poweredbytop/models/__init__.py
# MODELS PACKAGE INITIALIZER
# 100% FRESH REBUILD - SECURITY FIRST - MATCHES UNCHANGED CONNECT_DB
# ================================================================
# MARIADB ONLY - EXACT DB TABLES ONLY - NO SCHEMA CHANGES
# ================================================================

import logging

# ====================== SAFE IMPORTS ======================
from .connect_db import (
    get_security_db,
    close_security_db,
    ensure_table_exists
)

__all__ = [
    "get_security_db",
    "close_security_db",
    "ensure_table_exists"
]

logger = logging.getLogger("poweredbytop.models")
logger.info("poweredbytop.models package initialized - DB connector ready for full pipeline")