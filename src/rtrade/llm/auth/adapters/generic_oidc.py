"""Generic OIDC adapter — .well-known/openid-configuration discovery.

Discover token_endpoint dan device_authorization_endpoint dari metadata OIDC.
Validasi issuer exact-match untuk mencegah metadata substitution.
"""

from __future__ import annotations

import httpx

from rtrade.core.errors import ConfigError
from rtrade.llm.auth.oauth2 import OAuth2Provider
from rtrade.llm.auth.provider_profiles import OAuthProviderProfile, resolve_env_profile


async def discover_oidc_endpoints(issuer_metadata_url: str) -> dict[str, str]:
    """Fetch OIDC metadata and return endpoint URLs."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(issuer_metadata_url)
        resp.raise_for_status()
        meta = resp.json()
    return {
        "token_endpoint": meta.get("token_endpoint", ""),
        "device_authorization_endpoint": meta.get("device_authorization_endpoint", ""),
        "issuer": meta.get("issuer", ""),
    }


def build_generic_oidc_provider(
    profile: OAuthProviderProfile,
    discovered_endpoints: dict[str, str] | None = None,
    expected_issuer: str = "",
) -> OAuth2Provider:
    """Build OAuth2Provider from OIDC discovery or profile env vars."""
    resolved = resolve_env_profile(profile)

    token_url = resolved.token_url
    device_url = resolved.device_auth_url

    if discovered_endpoints:
        # Validasi issuer exact-match
        if expected_issuer and discovered_endpoints.get("issuer") != expected_issuer:
            raise ConfigError(
                f"OIDC issuer mismatch: expected {expected_issuer!r}, "
                f"got {discovered_endpoints.get('issuer')!r}. "
                "Metadata substitution ditolak."
            )
        token_url = discovered_endpoints.get("token_endpoint", token_url)
        device_url = discovered_endpoints.get("device_authorization_endpoint", device_url)

    if not token_url:
        raise ConfigError(
            f"Provider {profile.label}: token_url tidak ditemukan "
            "(baik dari env maupun OIDC discovery)"
        )

    return OAuth2Provider(
        provider_id="generic_oidc",
        token_url=token_url,
        client_id=resolved.client_id,
        client_secret=resolved.client_secret,
        scopes=resolved.scopes,
        grant_type=resolved.grant_type,
        device_auth_url=device_url,
    )
