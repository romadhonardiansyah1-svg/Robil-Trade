"""Pluggable credential providers for LLM calls.

Setiap provider menjawab satu pertanyaan: "untuk model ini, bagaimana cara
mengautentikasi panggilan litellm?" — via API key, bearer token OAuth, atau
kredensial Vertex/Azure. Runtime tidak tahu detailnya, hanya memanggil resolve().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AuthMaterial:
    """Bahan auth yang diteruskan ke litellm.acompletion(**kwargs). Bentuk KANONIK (lihat 0K/K1).

    Semua field punya default → konstruksi `AuthMaterial(api_key=...)` /
    `AuthMaterial(extra_kwargs=...)` tetap valid. O14 TIDAK mendefinisikan ulang kelas ini.
    """

    api_key: str | None = None
    bearer_token: str | None = None
    extra_kwargs: dict[str, Any] = field(
        default_factory=dict
    )  # mis. vertex_project, azure_ad_token
    auth_type: str = "api_key"  # "api_key" | "cli_oauth" | "vertex" | "azure_ad"
    provider_id: str = ""
    profile_name: str = ""

    def merge_into(self, kwargs: dict[str, Any]) -> None:
        """Terapkan ke kwargs litellm (in-place). LiteLLM memakai 'api_key' sbg slot bearer."""
        token = self.bearer_token or self.api_key
        if token:
            kwargs["api_key"] = token
        kwargs.update(self.extra_kwargs)


class CredentialProvider(ABC):
    """Sumber kredensial untuk satu model/provider."""

    @property
    @abstractmethod
    def mode(self) -> str:
        """Identifier mode: 'api_key' | 'oauth2' | 'vertex' | 'azure_ad'."""

    @abstractmethod
    async def resolve(self) -> AuthMaterial:
        """Kembalikan AuthMaterial siap pakai (refresh token bila perlu)."""
