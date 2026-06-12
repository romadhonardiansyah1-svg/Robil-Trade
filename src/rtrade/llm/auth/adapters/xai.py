"""xAI (Grok) OAuth adapter.

Bila endpoint OAuth tidak tersedia, status api_key_only/disabled_unsupported.
Dilarang fallback diam-diam ke scraping dashboard atau token web.
"""

from __future__ import annotations

from rtrade.core.errors import ConfigError
from rtrade.llm.auth.oauth2 import OAuth2Provider
from rtrade.llm.auth.provider_profiles import OAuthProviderProfile, resolve_env_profile


def build_xai_provider(profile: OAuthProviderProfile) -> OAuth2Provider:
    """Build xAI OAuth provider. Hanya jika endpoint resmi tersedia."""
    resolved = resolve_env_profile(profile)
    if not resolved.has_official_oauth_endpoint:
        raise ConfigError(
            "xAI OAuth belum tersedia. Gunakan API key resmi dari console.x.ai, "
            "atau konfigurasikan endpoint OAuth resmi bila tersedia."
        )
    return OAuth2Provider(
        provider_id="xai",
        token_url=resolved.token_url,
        client_id=resolved.client_id,
        client_secret=resolved.client_secret,
        scopes=resolved.scopes,
        grant_type=resolved.grant_type,
        device_auth_url=resolved.device_auth_url,
    )
