"""Shared Gemini quota helpers."""
from __future__ import annotations

def retry_after(resp) -> int:
    try:
        return max(30, int(resp.headers.get("retry-after") or 60))
    except (TypeError, ValueError):
        return 60


class QuotaError(Exception):
    def __init__(self, seconds: int = 60):
        super().__init__("quota")
        self.seconds = seconds

