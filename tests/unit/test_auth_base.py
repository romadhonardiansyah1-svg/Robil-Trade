"""Tests for CredentialProvider abstraction + ApiKeyProvider (O1)."""

from __future__ import annotations

import pytest

from rtrade.llm.auth.api_key import ApiKeyProvider
from rtrade.llm.auth.base import AuthMaterial, CredentialProvider


class TestAuthMaterial:
    def test_merge_into_api_key(self) -> None:
        mat = AuthMaterial(api_key="test-key")
        d: dict[str, object] = {}
        mat.merge_into(d)
        assert d["api_key"] == "test-key"

    def test_merge_into_bearer_token(self) -> None:
        mat = AuthMaterial(bearer_token="bearer-tok")
        d: dict[str, object] = {}
        mat.merge_into(d)
        assert d["api_key"] == "bearer-tok"

    def test_merge_into_extra_kwargs(self) -> None:
        mat = AuthMaterial(extra_kwargs={"vertex_project": "proj"})
        d: dict[str, object] = {}
        mat.merge_into(d)
        assert d["vertex_project"] == "proj"
        assert "api_key" not in d

    def test_bearer_takes_precedence_over_api_key(self) -> None:
        mat = AuthMaterial(api_key="key", bearer_token="bearer")
        d: dict[str, object] = {}
        mat.merge_into(d)
        assert d["api_key"] == "bearer"

    def test_defaults(self) -> None:
        mat = AuthMaterial()
        assert mat.api_key is None
        assert mat.bearer_token is None
        assert mat.auth_type == "api_key"
        assert mat.provider_id == ""
        assert mat.profile_name == ""


class TestApiKeyProvider:
    @pytest.mark.asyncio
    async def test_resolve_returns_api_key(self) -> None:
        provider = ApiKeyProvider(api_key="k")
        mat = await provider.resolve()
        assert mat.api_key == "k"
        assert mat.auth_type == "api_key"

    def test_mode(self) -> None:
        provider = ApiKeyProvider(api_key="k")
        assert provider.mode == "api_key"

    def test_is_credential_provider(self) -> None:
        provider = ApiKeyProvider(api_key="k")
        assert isinstance(provider, CredentialProvider)
