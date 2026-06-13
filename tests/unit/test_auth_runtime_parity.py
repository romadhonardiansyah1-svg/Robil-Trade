"""Tests for auth runtime parity: CLI OAuth = API key at runtime (O14)."""

from __future__ import annotations

from pathlib import Path
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rtrade.llm.auth.api_key import ApiKeyProvider
from rtrade.llm.auth.base import AuthMaterial
from rtrade.llm.auth.cli_oauth import CliOAuthProvider
from rtrade.llm.auth.token_store import StoredToken, save_token


@pytest.fixture()
def _token_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RTRADE_TOKEN_DIR", str(tmp_path))
    monkeypatch.delenv("RTRADE_TOKEN_KEY", raising=False)


class TestApiKeyResolve:
    async def test_api_key_returns_material(self) -> None:
        prov = ApiKeyProvider(api_key="test-key")
        mat = await prov.resolve()
        assert mat.api_key == "test-key"
        assert mat.auth_type == "api_key"

    async def test_merge_into_sets_api_key(self) -> None:
        mat = AuthMaterial(api_key="k")
        d: dict[str, object] = {}
        mat.merge_into(d)
        assert d["api_key"] == "k"


class TestCliOAuthResolve:
    @pytest.mark.usefixtures("_token_env")
    async def test_cli_oauth_returns_bearer(self) -> None:
        save_token("test_prov", StoredToken("tok123", "ref", time.time() + 9999, ["s"]))
        prov = CliOAuthProvider(provider_id="test_prov")
        mat = await prov.resolve()
        assert mat.bearer_token == "tok123"
        assert mat.auth_type == "cli_oauth"

    @pytest.mark.usefixtures("_token_env")
    async def test_cli_oauth_merge_into_same_as_api_key(self) -> None:
        """OAuth token goes into same api_key slot as API key → LLMClient identical."""
        save_token("merge_test", StoredToken("oauth-tok", None, time.time() + 9999, []))
        prov = CliOAuthProvider(provider_id="merge_test")
        mat = await prov.resolve()
        d: dict[str, object] = {}
        mat.merge_into(d)
        assert d["api_key"] == "oauth-tok"

    @pytest.mark.usefixtures("_token_env")
    async def test_no_token_raises_login_message(self) -> None:
        prov = CliOAuthProvider(provider_id="nonexistent")
        with pytest.raises(RuntimeError, match="Belum login"):
            await prov.resolve()


class TestLLMClientNoBranching:
    """LLMClient.complete() does NOT branch based on auth type — unified path."""

    async def test_api_key_provider_through_client(self) -> None:
        from rtrade.llm.client import LLMClient

        prov = ApiKeyProvider(api_key="key-1")
        client = LLMClient(credential_provider=prov)

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "ok"
        mock_resp.usage = None

        with patch("rtrade.llm.client.litellm.acompletion", new_callable=AsyncMock) as mock:
            mock.return_value = mock_resp
            await client.complete("model", "sys", "usr")
            assert mock.call_args[1]["api_key"] == "key-1"

    @pytest.mark.usefixtures("_token_env")
    async def test_cli_oauth_provider_through_client(self) -> None:
        from rtrade.llm.client import LLMClient

        save_token("unified_test", StoredToken("bearer-x", None, time.time() + 9999, []))
        prov = CliOAuthProvider(provider_id="unified_test")
        client = LLMClient(credential_provider=prov)

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "ok"
        mock_resp.usage = None

        with patch("rtrade.llm.client.litellm.acompletion", new_callable=AsyncMock) as mock:
            mock.return_value = mock_resp
            await client.complete("model", "sys", "usr")
            # OAuth bearer goes into same api_key slot
            assert mock.call_args[1]["api_key"] == "bearer-x"


class TestStatusDoesNotPrintToken:
    @pytest.mark.usefixtures("_token_env")
    def test_status_output_redacted(self, capsys: pytest.CaptureFixture[str]) -> None:
        save_token("status_test", StoredToken("SECRET_TOKEN_XYZ", None, time.time() + 3600, ["s"]))

        from rtrade.cli.auth import _cmd_status

        class _Args:
            provider = "status_test"

        _cmd_status(_Args())  # type: ignore[arg-type]
        out = capsys.readouterr().out
        assert "SECRET_TOKEN_XYZ" not in out
        assert "logged_in" in out
