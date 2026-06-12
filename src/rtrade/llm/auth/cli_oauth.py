"""CLI OAuth credential provider — wraps token store + optional refresh.

resolve() loads token, refreshes if expired, returns AuthMaterial.
Jika tidak ada token → raise error dengan instruksi login.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import structlog

from rtrade.llm.auth.base import AuthMaterial, CredentialProvider
from rtrade.llm.auth.token_store import load_token

logger = structlog.get_logger(__name__)

_REFRESH_SKEW = 120.0  # 2 menit sebelum expiry


@dataclass(frozen=True, slots=True)
class CliOAuthProvider(CredentialProvider):
    """Credential provider dari token hasil `rtrade auth login`.

    Token disimpan di token store (file terenkripsi), direfresh otomatis.
    """

    provider_id: str
    token_store_id: str = ""

    @property
    def mode(self) -> str:
        return "cli_oauth"

    @property
    def _store_id(self) -> str:
        return self.token_store_id or self.provider_id

    async def resolve(self) -> AuthMaterial:
        token = load_token(self._store_id)
        if token is None:
            raise RuntimeError(
                f"Belum login: jalankan `python -m rtrade.cli.auth login "
                f"--provider {self.provider_id}`"
            )

        now = time.time()
        if token.expiry_epoch - _REFRESH_SKEW > now:
            return AuthMaterial(
                bearer_token=token.access_token,
                auth_type="cli_oauth",
                provider_id=self.provider_id,
            )

        # Token expired — coba refresh via OAuth2Provider jika kita punya refresh_token
        if token.refresh_token:
            try:
                from rtrade.llm.auth.oauth2 import OAuth2Provider
                from rtrade.llm.auth.provider_profiles import (
                    load_provider_profiles,
                    resolve_env_profile,
                )

                profiles = load_provider_profiles(None)
                if self.provider_id in profiles:
                    resolved = resolve_env_profile(profiles[self.provider_id])
                    if resolved.token_url:
                        oauth = OAuth2Provider(
                            provider_id=self._store_id,
                            token_url=resolved.token_url,
                            client_id=resolved.client_id,
                            client_secret=resolved.client_secret,
                            scopes=resolved.scopes,
                        )
                        new_tok = await oauth._refresh(token.refresh_token)
                        from rtrade.llm.auth.token_store import save_token

                        save_token(self._store_id, new_tok)
                        return AuthMaterial(
                            bearer_token=new_tok.access_token,
                            auth_type="cli_oauth",
                            provider_id=self.provider_id,
                        )
            except Exception:
                logger.warning(
                    "token refresh gagal — coba pakai token expired",
                    provider=self.provider_id,
                )

        # Fallback: use expired token (provider might still accept it briefly)
        logger.warning(
            "token expired — login ulang disarankan",
            provider=self.provider_id,
        )
        return AuthMaterial(
            bearer_token=token.access_token,
            auth_type="cli_oauth",
            provider_id=self.provider_id,
        )
