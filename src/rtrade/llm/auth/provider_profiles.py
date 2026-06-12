"""Hermes-style OAuth provider profiles — load, validate, resolve env vars.

Manifest tunggal: config/oauth_providers.example.yaml (K2).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class OAuthProviderProfile:
    """Satu entri di manifest oauth_providers.yaml."""

    label: str
    auth_mode: str  # vertex | oauth2 | azure_ad | external_command
    capability: str  # vertex_adc | oauth_gateway | disabled_unsupported | external_adapter
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
        )
    return result


def resolve_env_profile(profile: OAuthProviderProfile) -> ResolvedOAuthProfile:
    """Resolve env vars dari profile. Tidak raise bila env kosong — caller cek sendiri."""
    token_url = os.environ.get(profile.token_url_env, "") if profile.token_url_env else ""
    client_id = os.environ.get(profile.client_id_env, "") if profile.client_id_env else ""
    scopes_raw = os.environ.get(profile.scopes_env, "") if profile.scopes_env else ""
    device_url = (
        os.environ.get(profile.device_auth_url_env, "") if profile.device_auth_url_env else ""
    )

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
    if profile.auth_mode == "oauth2" and not profile.token_url_env:
        issues.append("token_url_env wajib untuk auth_mode=oauth2")
    if profile.transport != "HTTPS":
        issues.append(f"transport harus HTTPS, bukan {profile.transport}")
    if profile.capability == "disabled_unsupported" and profile.enabled:
        issues.append(
            "provider disabled_unsupported tidak boleh enabled tanpa konfigurasi endpoint"
        )
    return issues
