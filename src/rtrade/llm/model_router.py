"""Model-provider auth router — route AI models through selectable auth profiles.

O11: Mengganti pemilihan provider global (auth_mode) di scan.py dengan
model_routes per-peran. Field credential_provider di LLMClient (dari O6)
TETAP dipakai — hanya cara MEMILIH-nya yang pindah ke router.

Backward compatible: bila auth_profiles/model_routes kosong → perilaku lama
(auth_mode: api_key, model dari analyst_model/critic_model).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from rtrade.core.config import AppConfig
from rtrade.core.errors import ConfigError
from rtrade.llm.auth.api_key import ApiKeyProvider
from rtrade.llm.auth.base import CredentialProvider

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ResolvedModelAuth:
    """Hasil resolve: model + credential provider untuk satu role."""

    role: str  # analyst|critic|backup|flagship
    model: str
    auth_profile: str
    provider_id: str
    credential_provider: CredentialProvider


def resolve_model_auth(cfg: AppConfig, role: str) -> ResolvedModelAuth:
    """Pilih model + credential provider berdasarkan llm.model_routes[role].

    Backward compatible: jika model_routes kosong, pakai field lama
    (analyst_model/critic_model/flagship_model + auth_mode: api_key).
    """
    routes = cfg.settings.llm.model_routes
    profiles = cfg.settings.llm.auth_profiles

    # Backward compatible: no routes configured → old behavior
    if not routes or role not in routes:
        model_map = {
            "analyst": cfg.settings.llm.analyst_model,
            "critic": cfg.settings.llm.critic_model,
            "backup": cfg.settings.llm.analyst_model,
            "flagship": cfg.settings.llm.flagship_model,
        }
        model = model_map.get(role, cfg.settings.llm.analyst_model)
        return ResolvedModelAuth(
            role=role,
            model=model,
            auth_profile="api_key_default",
            provider_id="api_key",
            credential_provider=ApiKeyProvider(api_key=cfg.secrets.gemini_api_key_1),
        )

    route = routes[role]
    if not isinstance(route, dict):
        raise ConfigError(f"model_routes.{role} harus berupa dict, got {type(route).__name__}")

    route_model = route.get("model", "")
    profile_name = route.get("auth_profile", "")

    if not route_model:
        raise ConfigError(f"model_routes.{role}.model wajib diisi")
    if not profile_name:
        raise ConfigError(f"model_routes.{role}.auth_profile wajib diisi")

    if profile_name not in profiles:
        raise ConfigError(
            f"model_routes.{role}.auth_profile={profile_name!r} "
            f"tidak ada di llm.auth_profiles. Tersedia: {list(profiles.keys())}"
        )

    profile = profiles[profile_name]
    if not isinstance(profile, dict):
        raise ConfigError(f"auth_profiles.{profile_name} harus berupa dict")

    auth_type = profile.get("auth_type", "api_key")
    provider_id = profile.get("provider_id", "")
    enabled = profile.get("enabled", True)

    if auth_type == "cli_oauth" and not enabled:
        raise ConfigError(
            f"auth_profiles.{profile_name} disabled (cli_oauth). "
            "Aktifkan atau gunakan profile lain."
        )

    # Validate api_key_secret not literal
    api_key_secret = profile.get("api_key_secret", "")
    if api_key_secret and (
        api_key_secret.startswith("sk-")
        or api_key_secret.startswith("AIza")
        or api_key_secret.startswith("sk-ant")
    ):
        raise ConfigError(
            f"auth_profiles.{profile_name}.api_key_secret tidak boleh berisi literal API key. "
            "Isi nama field Secrets (mis. 'gemini_api_key_1')."
        )

    cred_provider = _build_provider_for_profile(profile, profile_name, cfg)

    logger.info(
        "llm_route_selected",
        role=role,
        model=route_model,
        auth_profile=profile_name,
        provider_id=provider_id,
    )

    return ResolvedModelAuth(
        role=role,
        model=route_model,
        auth_profile=profile_name,
        provider_id=provider_id,
        credential_provider=cred_provider,
    )


def _build_provider_for_profile(
    profile: dict[str, Any],
    profile_name: str,
    cfg: AppConfig,
) -> CredentialProvider:
    """Build credential provider from an auth_profile dict."""
    auth_type = profile.get("auth_type", "api_key")

    if auth_type == "api_key":
        secret_field = profile.get("api_key_secret", "")
        if secret_field:
            key = getattr(cfg.secrets, secret_field, "")
            if not key:
                raise ConfigError(
                    f"auth_profiles.{profile_name}.api_key_secret={secret_field!r} "
                    f"tetapi field Secrets.{secret_field} kosong"
                )
        else:
            key = cfg.secrets.gemini_api_key_1
        return ApiKeyProvider(api_key=key)

    if auth_type == "cli_oauth":
        from rtrade.llm.auth.cli_oauth import CliOAuthProvider

        return CliOAuthProvider(
            provider_id=profile.get("provider_id", ""),
            token_store_id=profile.get("token_store_id", profile.get("provider_id", "")),
        )

    if auth_type == "vertex" or profile.get("credential_provider") == "vertex":
        from rtrade.llm.auth.vertex import VertexProvider

        return VertexProvider(
            project=profile.get("vertex_project", cfg.settings.llm.vertex_project),
            location=profile.get("vertex_location", cfg.settings.llm.vertex_location),
        )

    raise ConfigError(
        f"auth_profiles.{profile_name}.auth_type={auth_type!r} tidak dikenal. "
        "Pilihan: api_key | cli_oauth | vertex"
    )
