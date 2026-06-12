"""API key credential provider — mode default (perilaku lama)."""

from __future__ import annotations

from dataclasses import dataclass

from rtrade.llm.auth.base import AuthMaterial, CredentialProvider


@dataclass(frozen=True, slots=True)
class ApiKeyProvider(CredentialProvider):
    """Mode default: API key resmi (perilaku lama)."""

    api_key: str

    @property
    def mode(self) -> str:
        return "api_key"

    async def resolve(self) -> AuthMaterial:
        return AuthMaterial(api_key=self.api_key, auth_type="api_key")
