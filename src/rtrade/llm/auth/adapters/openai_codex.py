"""Codex/OpenAI OAuth adapter.

Adapter ini hanya membangun OAuth2Provider dari endpoint OAuth resmi/gateway enterprise
yang diberikan lewat manifest/env. DILARANG membaca token Codex CLI, ChatGPT session,
browser cookie, atau file credential consumer.
"""

from __future__ import annotations

from rtrade.core.errors import ConfigError
from rtrade.llm.auth.oauth2 import OAuth2Provider
from rtrade.llm.auth.provider_profiles import OAuthProviderProfile, resolve_env_profile


def build_openai_codex_provider(profile: OAuthProviderProfile) -> OAuth2Provider:
    """Build OAuth2Provider dari endpoint OAuth resmi/gateway enterprise.

    WAJIB: profile harus punya endpoint OAuth resmi atau capability oauth_gateway.
    Tidak ada fallback ke token consumer — error eksplisit.
    """
    resolved = resolve_env_profile(profile)
    if not resolved.has_official_oauth_endpoint and profile.capability != "oauth_gateway":
        raise ConfigError(
            "codex_openai OAuth belum aktif: isi endpoint OAuth resmi/gateway enterprise, "
            "atau gunakan API key resmi."
        )
    return OAuth2Provider(
        provider_id="codex_openai",
        token_url=resolved.token_url,
        client_id=resolved.client_id,
        client_secret=resolved.client_secret,
        scopes=resolved.scopes,
        grant_type=resolved.grant_type,
        device_auth_url=resolved.device_auth_url,
    )
