"""Hermes-style OAuth provider profiles — load, validate, resolve env vars.

Manifest tunggal: config/oauth_providers.example.yaml (K2).
A0: Codex OAuth + xAI OAuth = subscription_oauth (Hermes-style, masuk pool).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class OAuthProviderProfile:
    """Satu entri di manifest oauth_providers.yaml."""

    label: str
    auth_mode: str  # vertex | oauth2 | azure_ad | external_command | api_key | disabled
    capability: str  # vertex_adc | oauth_gateway | subscription_oauth | api_key | external_adapter
    enabled: bool
    token_url_env: str = ""
    client_id_env: str = ""
    scopes_env: str = ""
    device_auth_url_env: str = ""
    note: str = ""
    login_flow: str = ""
    requires_official_oauth: bool = True
    transport: str = "HTTPS"
    models: list[str] = field(default_factory=list)
    models_url_env: str = ""
    external_command: list[str] = field(default_factory=list)
    device_auth_url: str = ""  # A0: hardcoded default (manifest inline)
    token_url: str = ""  # A0: hardcoded default (manifest inline)


@dataclass(frozen=True, slots=True)
class ResolvedOAuthProfile:
    """Profile yang env vars-nya sudah di-resolve."""

    token_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    scopes: list[str] = field(default_factory=list)
    grant_type: str = "client_credentials"
    device_auth_url: str = ""
    has_official_oauth_endpoint: bool = False


# Guard: token dari tool konsumen lain TIDAK boleh dibaca oleh adapter.
# Core bot melakukan OAuth SENDIRI dan menyimpan di token_store sendiri.
_CONSUMER_TOKEN_SOURCES = (
    ".codex",
    ".claude",
    ".gemini",
    "Cookies",
    "Local Storage",
    "Session Storage",
)


def is_subscription_oauth(provider_id: str, profile: OAuthProviderProfile) -> bool:
    """True bila profile = subscription-backed OAuth (Codex/xAI gaya Hermes)."""
    return profile.capability == "subscription_oauth"


def validate_provider_profile(provider_id: str, profile: OAuthProviderProfile) -> list[str]:
    """Validasi profile dengan id, kembalikan daftar masalah. Kosong = OK."""
    issues = validate_profile(profile)
    # Subscription OAuth wajib punya device_auth_url atau device_auth_url_env
    if (
        profile.capability == "subscription_oauth"
        and not profile.device_auth_url
        and not profile.device_auth_url_env
    ):
        issues.append(
            "subscription_oauth wajib punya device_auth_url atau "
            "device_auth_url_env untuk Device Code Flow"
        )
    # External command TIDAK boleh membaca sumber consumer tool lain
    joined = " ".join([profile.note, " ".join(profile.external_command)])
    for needle in _CONSUMER_TOKEN_SOURCES:
        if needle.lower() in joined.lower():
            issues.append(f"JANGAN membaca sumber token tool lain: {needle}")
    return issues


def load_provider_profiles(
    config_dir: Path | str | None = None,
) -> dict[str, OAuthProviderProfile]:
    """Load provider profiles dari manifest YAML."""
    if config_dir is None:
        config_dir = Path("config")
    else:
        config_dir = Path(config_dir)

    # Coba prod file dulu, fallback ke example
    for name in ("oauth_providers.yaml", "oauth_providers.example.yaml"):
        path = config_dir / name
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                doc = yaml.safe_load(fh) or {}
            break
    else:
        return {}

    providers_data = doc.get("oauth_providers", {})
    result: dict[str, OAuthProviderProfile] = {}
    for pid, data in providers_data.items():
        if not isinstance(data, dict):
            continue
        result[pid] = OAuthProviderProfile(
            label=data.get("label", pid),
            auth_mode=data.get("auth_mode", "oauth2"),
            capability=data.get("capability", ""),
            enabled=data.get("enabled", False),
            token_url_env=data.get("token_url_env", ""),
            client_id_env=data.get("client_id_env", ""),
            scopes_env=data.get("scopes_env", ""),
            device_auth_url_env=data.get("device_auth_url_env", ""),
            note=data.get("note", ""),
            login_flow=data.get("login_flow", ""),
            requires_official_oauth=data.get("requires_official_oauth", True),
            transport=data.get("transport", "HTTPS"),
            models=data.get("models", []),
            models_url_env=data.get("models_url_env", ""),
            external_command=data.get("external_command", []),
            device_auth_url=data.get("device_auth_url", ""),
            token_url=data.get("token_url", ""),
        )
    return result


def resolve_env_profile(profile: OAuthProviderProfile) -> ResolvedOAuthProfile:
    """Resolve env vars dari profile. Tidak raise bila env kosong — caller cek sendiri."""
    # Subscription OAuth: prefer hardcoded URL from manifest, fallback to env
    token_url = profile.token_url  # A0: inline manifest value
    if not token_url and profile.token_url_env:
        token_url = os.environ.get(profile.token_url_env, "")

    client_id = os.environ.get(profile.client_id_env, "") if profile.client_id_env else ""
    scopes_raw = os.environ.get(profile.scopes_env, "") if profile.scopes_env else ""

    device_url = profile.device_auth_url  # A0: inline manifest value
    if not device_url and profile.device_auth_url_env:
        device_url = os.environ.get(profile.device_auth_url_env, "")

    return ResolvedOAuthProfile(
        token_url=token_url,
        client_id=client_id,
        scopes=scopes_raw.split() if scopes_raw else [],
        device_auth_url=device_url,
        has_official_oauth_endpoint=bool(token_url),
    )


def validate_profile(profile: OAuthProviderProfile) -> list[str]:
    """Validasi profile, kembalikan daftar masalah. Kosong = OK."""
    issues: list[str] = []
    if (
        profile.auth_mode == "oauth2"
        and not profile.token_url_env
        and not profile.token_url
        and profile.capability != "subscription_oauth"
    ):
        issues.append("token_url_env atau token_url wajib untuk auth_mode=oauth2")
    if profile.transport not in ("HTTPS", "local_process", "none"):
        issues.append(f"transport harus HTTPS/local_process/none, bukan {profile.transport}")
    return issues
