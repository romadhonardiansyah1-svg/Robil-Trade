"""Membangun CredentialPool untuk scan dari SEMUA kredensial yang tersedia (A7).

Urutan prioritas (fallback berjalan dari atas ke bawah):
1. Semua API key terisi (Secrets.keys_for), family model analyst didahulukan.
2. Kredensial route per-role (model_routes/auth_profiles — non-api_key).
3. Semua akun OAuth CLI yang tersimpan di token store (per auth_profile cli_oauth).
4. Semua akun ADC Vertex (~/.rtrade/adc) bila llm.vertex_project diisi.

A0: codex_oauth → flavor openai, xai_oauth → flavor xai (Hermes-style).
Dedup: API key identik / store id identik hanya masuk sekali.
"""

from __future__ import annotations

import hashlib
from typing import Any

import structlog

from rtrade.core.config import AppConfig
from rtrade.core.errors import ConfigError
from rtrade.llm.auth.api_key import ApiKeyProvider
from rtrade.llm.auth.pool import CredentialPool, PooledCredential, model_flavor
from rtrade.llm.model_router import resolve_model_auth, resolve_role_model

logger = structlog.get_logger(__name__)

_ROLES = ("analyst", "critic", "flagship")

# provider_id manifest → flavor model litellm
_FLAVOR_BY_PROVIDER_ID: dict[str, str] = {
    "google_vertex": "vertex_ai",
    "azure_openai": "azure",
    "openai_api": "openai",
    "openai_gateway": "openai",
    "codex_oauth": "openai",  # A0: Codex subscription → flavor openai
    "codex_openai": "openai",  # alias compat
    "generic_gateway": "openai",
    "xai": "xai",
    "xai_api": "xai",
    "xai_oauth": "xai",  # A0: xAI subscription → flavor xai
    "xai_hermes": "xai",
}


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def build_credential_pool(
    cfg: AppConfig,
    *,
    redis_client: Any | None = None,
) -> CredentialPool:
    """Alias publik — memanggil build_scan_pool."""
    return build_scan_pool(cfg, redis_client=redis_client)


def build_scan_pool(
    cfg: AppConfig,
    *,
    redis_client: Any | None = None,
) -> CredentialPool:
    """Pool tunggal untuk seluruh pipeline scan (analyst+critic+flagship)."""
    entries: list[PooledCredential] = []
    seen_ids: set[str] = set()
    seen_api_fp: set[str] = set()
    seen_stores: set[str] = set()

    def add(entry: PooledCredential) -> None:
        if entry.cred_id in seen_ids:
            return
        seen_ids.add(entry.cred_id)
        entries.append(entry)

    primary_flavor = model_flavor(resolve_role_model(cfg, "analyst"))

    # --- 1. API keys, family model analyst dulu, sisanya menyusul ---
    families = ["gemini", "anthropic", "openai", "xai"]
    families.sort(key=lambda f: f != primary_flavor)
    for fam in families:
        for i, key in enumerate(cfg.secrets.keys_for(fam), start=1):
            fp = _fingerprint(key)
            if fp in seen_api_fp:
                continue
            seen_api_fp.add(fp)
            add(
                PooledCredential(
                    cred_id=f"{fam}_key_{i}",
                    flavor=fam,
                    credential=ApiKeyProvider(api_key=key),
                )
            )

    # --- 2. Kredensial route per-role (non-api_key saja; api_key sudah tercakup) ---
    for role in _ROLES:
        try:
            ra = resolve_model_auth(cfg, role)
        except ConfigError as exc:
            logger.warning("route auth invalid — dilewati", role=role, error=str(exc))
            continue
        if ra.credential_provider.mode == "api_key":
            continue  # gemini_api_key_1 dkk sudah masuk di blok 1
        add(
            PooledCredential(
                cred_id=f"route_{ra.auth_profile}",
                flavor=model_flavor(ra.model),
                credential=ra.credential_provider,
            )
        )

    # --- 3. Akun OAuth CLI tersimpan, per auth_profile cli_oauth ---
    from rtrade.llm.auth.cli_oauth import CliOAuthProvider
    from rtrade.llm.auth.token_store import account_store_id, list_accounts

    for pname, prof in cfg.settings.llm.auth_profiles.items():
        if not isinstance(prof, dict) or prof.get("auth_type") != "cli_oauth":
            continue
        if not prof.get("enabled", True):
            continue
        pid = str(prof.get("provider_id", ""))
        if not pid:
            continue
        flavor = str(prof.get("flavor", "")) or _FLAVOR_BY_PROVIDER_ID.get(pid, "openai")
        for acc in list_accounts(pid):
            store = account_store_id(pid, acc)
            if store in seen_stores:
                continue
            seen_stores.add(store)
            add(
                PooledCredential(
                    cred_id=f"{pname}__{acc}",
                    flavor=flavor,
                    credential=CliOAuthProvider(provider_id=pid, token_store_id=store),
                )
            )

    # --- 4. Akun ADC Vertex (multi-akun Google) ---
    if cfg.settings.llm.vertex_project:
        from rtrade.llm.auth.vertex import VertexProvider, adc_path_for, list_adc_accounts

        for acc in list_adc_accounts():
            add(
                PooledCredential(
                    cred_id=f"vertex__{acc}",
                    flavor="vertex_ai",
                    credential=VertexProvider(
                        project=cfg.settings.llm.vertex_project,
                        location=cfg.settings.llm.vertex_location,
                        credentials_path=str(adc_path_for(acc)),
                    ),
                )
            )

    if not entries:
        raise ConfigError(
            "Tidak ada kredensial LLM. Isi GEMINI_API_KEY_1 (atau key lain) di .env, "
            "atau login OAuth: python -m rtrade.cli.auth login --provider <id>"
        )

    logger.info(
        "credential pool built",
        n=len(entries),
        cred_ids=[e.cred_id for e in entries],
        primary_flavor=primary_flavor,
    )
    return CredentialPool(entries, redis_client=redis_client)
