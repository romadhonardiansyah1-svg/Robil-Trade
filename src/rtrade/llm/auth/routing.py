"""Reusable, pure writer for LLM model routes + auth profiles in settings.yaml.

Extracted dari `rtrade.cli.auth._cmd_use` agar bisa dipakai ulang oleh wizard model
(tanpa argparse / katalog / network). Logika penamaan auth_profile, struktur entri
auth_profiles, dan merge `.update()` (preserve kunci manual operator) dipertahankan
identik dengan perilaku sebelumnya.
"""

from __future__ import annotations

from pathlib import Path

import structlog
import yaml

logger = structlog.get_logger(__name__)


def set_model_route(
    *,
    settings_path: Path,
    role: str,
    provider_id: str,
    model: str,
    auth_mode: str,  # "api_key" | "vertex" | "cli_oauth" | (others → cli_oauth)
    account: str = "default",
    vertex_project: str = "",
    vertex_location: str = "",
    api_key_secret: str = "",  # optional Secrets field name for api_key profiles
) -> str:
    """Write llm.model_routes[role] + llm.auth_profiles[<name>] into settings.yaml.

    Returns the auth_profile_name. Idempotent: re-running updates in place,
    preserves other keys an operator set manually. Returns the profile name.
    """
    # Determine auth_profile name for this provider.
    auth_profile_name = f"{provider_id}_cli_oauth"
    if auth_mode == "vertex":
        auth_profile_name = f"{provider_id}_vertex"
    elif auth_mode == "api_key":
        auth_profile_name = f"{provider_id}_api_key"

    # Load existing settings (or start empty).
    if settings_path.exists():
        with settings_path.open("r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
    else:
        doc = {}

    llm = doc.setdefault("llm", {})
    routes = llm.setdefault("model_routes", {})
    profiles_cfg = llm.setdefault("auth_profiles", {})

    # Buat/lengkapi entri auth_profiles supaya route TIDAK menggantung (C4).
    entry: dict[str, object] = {"enabled": True}
    if auth_mode == "vertex":
        entry["auth_type"] = "vertex"
        entry["vertex_project"] = vertex_project
        if vertex_location:
            entry["vertex_location"] = vertex_location
    elif auth_mode == "api_key":
        entry["auth_type"] = "api_key"
        # api_key_secret kosong → pool pakai key dari Secrets family (lihat pool_builder).
        if api_key_secret:
            entry["api_key_secret"] = api_key_secret
    else:
        # oauth2 / external_command / subscription → kredensial token store via CLI login.
        entry["auth_type"] = "cli_oauth"
        entry["provider_id"] = provider_id
        entry["account"] = account
    # Jangan timpa kunci lain yang mungkin sudah diisi operator manual.
    existing = profiles_cfg.get(auth_profile_name)
    if isinstance(existing, dict):
        existing.update(entry)
    else:
        profiles_cfg[auth_profile_name] = entry

    routes[role] = {
        "model": model,
        "auth_profile": auth_profile_name,
    }

    with settings_path.open("w", encoding="utf-8") as fh:
        yaml.dump(doc, fh, default_flow_style=False, allow_unicode=True)

    logger.info(
        "model route set",
        role=role,
        model=model,
        provider_id=provider_id,
        auth_mode=auth_mode,
        auth_profile=auth_profile_name,
    )
    return auth_profile_name
