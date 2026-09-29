"""Compatibility exports for the Apt Gemini API helpers."""
from app.services.gemini.tools import BASE, FREE_BLOCK, FALLBACK_FREE, GEAR_PROPS, TOOL_DECLS, CHAT_RULES
from app.services.gemini.common import QuotaError, retry_after
from app.services.gemini.models import _clean_id, free_ok, _rank, pick_free_model, ids_from_list_payload, list_models, health_check, resolve_model
from app.services.gemini.client import _generate, _contents, complete, backoff_until
from app.services.gemini.vision import read_nameplate
