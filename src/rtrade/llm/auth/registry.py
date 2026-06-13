"""Credential provider registry — builds the right provider from config.

O6: Global registry based on llm.auth_mode.
O11: Extended with per-profile builds (build_credential_provider_for_profile).
A0: Subscription OAuth (Codex/xAI) via device_code from manifest.
"""

from __future__ import annotations

import os

from rtrade.core.config import LLMSettings, Secrets
from rtrade.llm.auth.api_key import ApiKeyProvider
from rtrade.llm.auth.base import CredentialProvider
from rtrade.llm.auth.oauth2 import OAuth2Provider
from rtrade.llm.auth.vertex import VertexProvider


def build_credential_provider(llm: LLMSettings, secrets: Secrets) -> CredentialProvider:
    """Build credential provider from global llm.auth_mode setting."""
    mode = llm.auth_mode
    if mode == "vertex":
        return VertexProvider(project=llm.vertex_project, location=llm.vertex_location)
    if mode == "oauth2":
        return build_generic_oauth_from_env()
    if mode == "azure_ad":
        from rtrade.core.errors import ConfigError

        # Azure AD stub — requires azure-identity (optional dep)
        raise ConfigError(
            "Azure AD mode requires azure-identity package and env vars: "
            "AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_OPENAI_ENDPOINT"
        )
    return ApiKeyProvider(api_key=secrets.gemini_api_key_1)


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        from rtrade.core.errors import ConfigError

        raise ConfigError(f"env {name} wajib diisi untuk OAuth mode (lihat docs/AUTH_OAUTH.md)")
    return val


def build_generic_oauth_from_env() -> OAuth2Provider:
    return OAuth2Provider(
        provider_id=os.environ.get("RTRADE_OAUTH_PROVIDER_ID", "generic_gateway"),
        token_url=_require_env("RTRADE_OAUTH_TOKEN_URL"),
        client_id=_require_env("RTRADE_OAUTH_CLIENT_ID"),
        client_secret=os.environ.get("RTRADE_OAUTH_CLIENT_SECRET", ""),
        scopes=os.environ.get("RTRADE_OAUTH_SCOPES", "").split(),
        grant_type=os.environ.get("RTRADE_OAUTH_GRANT", "client_credentials"),
        device_auth_url=os.environ.get("RTRADE_OAUTH_DEVICE_URL", ""),
    )


def build_provider_from_profile(
    provider_id: str,
    profile_path: object = None,
    *,
    store_id: str = "",
) -> OAuth2Provider:
    """Build OAuth2Provider from provider profiles manifest (O8/A0).

    A0: subscription_oauth (codex_oauth, xai_oauth) supported via device_code
    with inline device_auth_url/token_url from manifest.
    """
    from rtrade.llm.auth.provider_profiles import (
        load_provider_profiles,
        resolve_env_profile,
        validate_provider_profile,
    )

    profiles = load_provider_profiles(None)
    if provider_id not in profiles:
        from rtrade.core.errors import ConfigError

        raise ConfigError(f"Provider '{provider_id}' tidak ditemukan di manifest")

    profile = profiles[provider_id]

    if not profile.enabled:
        from rtrade.core.errors import ConfigError

        raise ConfigError(
            f"Provider '{provider_id}' disabled. "
            f"Catatan: {profile.note or 'Aktifkan di oauth_providers.yaml'}"
        )

    # Validate profile
    issues = validate_provider_profile(provider_id, profile)
    if issues:
        from rtrade.core.errors import ConfigError

        raise ConfigError(f"profile {provider_id} invalid: {'; '.join(issues)}")

    if profile.auth_mode == "vertex":
        from rtrade.core.errors import ConfigError

        raise ConfigError("Untuk google_vertex, gunakan `rtrade auth login --provider google`")

    resolved = resolve_env_profile(profile)
    return OAuth2Provider(
        provider_id=provider_id,
        token_url=resolved.token_url,
        client_id=resolved.client_id,
        client_secret=resolved.client_secret,
        scopes=resolved.scopes,
        grant_type=resolved.grant_type,
        device_auth_url=resolved.device_auth_url,
        store_id=store_id,
    )
