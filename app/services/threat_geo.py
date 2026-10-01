# Lazy IP → country. Loaded only when the threat map is opened.
from __future__ import annotations

import gzip
import ipaddress
import os
import struct
from pathlib import Path

_RANGES: list[tuple[int, int, str]] | None = None
_LOAD_TRIED = False

_PACKED = Path(__file__).resolve().parents[1] / "data" / "ipv4-country.bin.gz"
_HOME_BIN = Path.home() / "aegis_geo" / "ipv4-country.bin"


def ensure_ip_geo_table(conn) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pbt_ip_geo (
            ip VARCHAR(45) PRIMARY KEY,
            country_iso2 CHAR(2) NOT NULL DEFAULT 'XX',
            source VARCHAR(32) NULL,
            resolved_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            KEY idx_pbt_ip_geo_cc (country_iso2),
            KEY idx_pbt_ip_geo_resolved (resolved_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    conn.commit()


def _load_ranges() -> list[tuple[int, int, str]]:
    global _RANGES, _LOAD_TRIED
    if _RANGES is not None:
        return _RANGES
    if _LOAD_TRIED:
        return []
    _LOAD_TRIED = True
    blob = b""
    try:
        env = (os.getenv("AEGIS_GEO_IPV4") or "").strip()
        candidates = []
        if env:
            candidates.append(Path(env).expanduser())
        candidates.extend([_HOME_BIN, _PACKED])
        for path in candidates:
            if not path.is_file():
                continue
            raw = path.read_bytes()
            if path.suffix == ".gz" or raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            blob = raw
            break
    except Exception as exc:
        print(f"[threat-map] geo load failed: {exc}")
        _RANGES = []
        return _RANGES
    out: list[tuple[int, int, str]] = []
    rec = 10
    for i in range(0, len(blob) - rec + 1, rec):
        start, end = struct.unpack_from(">II", blob, i)
        cc = blob[i + 8 : i + 10].decode("ascii", errors="ignore")
        if len(cc) == 2:
            out.append((start, end, cc))
    _RANGES = out
    print(f"[threat-map] loaded {len(out)} IPv4 country ranges")
    return out


def _lookup_ipv4(n: int) -> str:
    ranges = _load_ranges()
    lo, hi = 0, len(ranges) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        start, end, cc = ranges[mid]
        if n < start:
            hi = mid - 1
        elif n > end:
            lo = mid + 1
        else:
            return cc
    return "XX"


def country_for_ip(ip: str) -> str:
    raw = (ip or "").strip()
    if not raw:
        return "XX"
    try:
        addr = ipaddress.ip_address(raw.split("%")[0])
    except ValueError:
        return "XX"
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
        return "ZZ"
    if addr.version == 4:
        return _lookup_ipv4(int(addr))
    # IPv6: country grain not in the packed v4 table
    return "XX"


def fill_missing_ips(conn, ips: list[str], *, limit: int = 200) -> int:
    """Resolve and upsert up to `limit` IPs. Returns how many were written."""
    ensure_ip_geo_table(conn)
    uniq = []
    seen = set()
    for ip in ips:
        ip = (ip or "").strip()
        if not ip or ip in seen:
            continue
        seen.add(ip)
        uniq.append(ip)
    if not uniq:
        return 0
    cur = conn.cursor()
    missing: list[str] = []
    # chunk IN lists
    for i in range(0, len(uniq), 200):
        chunk = uniq[i : i + 200]
        ph = ",".join(["%s"] * len(chunk))
        cur.execute(
            f"SELECT ip FROM pbt_ip_geo WHERE ip IN ({ph}) "
            f"AND resolved_at >= NOW() - INTERVAL 90 DAY",
            chunk,
        )
        have = {(r.get("ip") if isinstance(r, dict) else r[0]) for r in (cur.fetchall() or [])}
        for ip in chunk:
            if ip not in have:
                missing.append(ip)
    wrote = 0
    for ip in missing[: int(limit)]:
        cc = country_for_ip(ip)
        cur.execute(
            """
            INSERT INTO pbt_ip_geo (ip, country_iso2, source, resolved_at)
            VALUES (%s, %s, 'whois-asn', NOW())
            ON DUPLICATE KEY UPDATE
                country_iso2 = VALUES(country_iso2),
                source = VALUES(source),
                resolved_at = NOW()
            """,
            (ip, cc),
        )
        wrote += 1
    if wrote:
        conn.commit()
    return wrote
