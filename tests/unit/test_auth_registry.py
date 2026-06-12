"""Tests for auth registry (O6)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from rtrade.llm.auth.api_key import ApiKeyProvider
from rtrade.llm.auth.base import AuthMaterial
from rtrade.llm.auth.registry import build_credential_provider
from rtrade.llm.auth.vertex import VertexProvider


class _FakeLLMSettings:
    auth_mode: str = "api_key"
    vertex_project: str = "proj"
    vertex_location: str = "us-central1"


class _FakeSecrets:
    gemini_api_key_1: str = "test-gemini-key"


class TestBuildCredentialProvider:
    def test_api_key_mode(self) -> None:
        s = _FakeLLMSettings()
        s.auth_mode = "api_key"
        prov = build_credential_provider(s, _FakeSecrets())  # type: ignore[arg-type]
        assert isinstance(prov, ApiKeyProvider)

    def test_vertex_mode(self) -> None:
        s = _FakeLLMSettings()
        s.auth_mode = "vertex"
        prov = build_credential_provider(s, _FakeSecrets())  # type: ignore[arg-type]
        assert isinstance(prov, VertexProvider)

    def test_azure_stub_raises(self) -> None:
        s = _FakeLLMSettings()
        s.auth_mode = "azure_ad"
        from rtrade.core.errors import ConfigError

        with pytest.raises(ConfigError, match="Azure AD"):
            build_credential_provider(s, _FakeSecrets())  # type: ignore[arg-type]


class TestLLMClientWithProvider:
    async def test_credential_provider_resolve_called(self) -> None:
        """LLMClient with credential_provider → resolve() called, merge_into applied."""
        from unittest.mock import MagicMock

        from rtrade.llm.client import LLMClient

        mock_provider = AsyncMock()
        mock_provider.resolve.return_value = AuthMaterial(api_key="oauth-token")

        client = LLMClient(credential_provider=mock_provider)

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = '{"result": "ok"}'
        mock_resp.usage = None

        with patch("rtrade.llm.client.litellm.acompletion", new_callable=AsyncMock) as mock_acomp:
            mock_acomp.return_value = mock_resp

            await client.complete("test-model", "sys", "usr")

            # Verify credential_provider was resolved
            mock_provider.resolve.assert_awaited_once()
            # Verify api_key was passed from AuthMaterial
            call_kwargs = mock_acomp.call_args[1]
            assert call_kwargs.get("api_key") == "oauth-token"
