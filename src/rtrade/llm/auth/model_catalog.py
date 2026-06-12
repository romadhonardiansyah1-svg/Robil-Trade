"""Model catalog: daftar model per-provider (statis + discovery via /v1/models).

list_provider_models() menggabungkan katalog statis dari manifest
dengan discovery endpoint (jika tersedia). Best-effort: endpoint gagal
→ kembalikan katalog statis saja.
"""

from __future__ import annotations

import os

import httpx
import structlog

from rtrade.llm.auth.provider_profiles import OAuthProviderProfile

logger = structlog.get_logger(__name__)


async def list_provider_models(
    profile: OAuthProviderProfile,
    credential: object = None,
) -> list[str]:
    """Kembalikan daftar model: dari manifest `models:` (statis) digabung dengan
    hasil GET {models_url} bila ada (format OpenAI /v1/models: body['data'][*]['id']).
    Best-effort: endpoint gagal → kembalikan katalog statis saja."""
    static_models = list(profile.models) if profile.models else []

    # Discovery dari models_url_env (OpenAI-compatible /v1/models)
    models_url_env = profile.models_url_env
    if models_url_env:
        models_url = os.environ.get(models_url_env, "")
        if models_url:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(models_url)
                    resp.raise_for_status()
                    body = resp.json()
                    discovered = [m["id"] for m in body.get("data", []) if isinstance(m, dict)]
                    # Gabung + dedup (static first, preserving order)
                    combined = list(dict.fromkeys(static_models + discovered))
                    return combined
            except Exception as exc:
                logger.warning(
                    "model discovery gagal — pakai katalog statis",
                    provider=profile.label,
                    error=str(exc),
                )

    return static_models
