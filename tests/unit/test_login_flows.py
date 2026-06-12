"""Tests for login flows (O12)."""

from __future__ import annotations

import httpx
import pytest
import respx

from rtrade.llm.auth.login_flows import LoginFlow, auto_flow
from rtrade.llm.auth.oauth2 import OAuth2Provider


class TestAutoFlow:
    def test_explicit_device_code(self) -> None:
        assert auto_flow("device_code") == LoginFlow.DEVICE_CODE

    def test_explicit_loopback(self) -> None:
        assert auto_flow("loopback") == LoginFlow.LOOPBACK

    def test_explicit_paste_url(self) -> None:
        assert auto_flow("paste_url") == LoginFlow.PASTE_URL

    def test_ssh_connection_returns_paste(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 22 5.6.7.8 22")
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("BROWSER", raising=False)
        assert auto_flow(None) == LoginFlow.PASTE_URL

    def test_with_display_returns_loopback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.delenv("SSH_CONNECTION", raising=False)
        assert auto_flow(None) == LoginFlow.LOOPBACK

    def test_no_display_no_ssh_returns_paste(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("BROWSER", raising=False)
        monkeypatch.delenv("SSH_CONNECTION", raising=False)
        assert auto_flow(None) == LoginFlow.PASTE_URL


class TestPasteURLFlow:
    def test_build_authorize_url_contains_pkce(self) -> None:
        prov = OAuth2Provider(
            provider_id="test",
            token_url="https://t.example.com/token",
            client_id="cid",
            scopes=["read"],
        )
        url = prov.build_authorize_url(
            redirect_uri="http://localhost:1",
            state="st",
            code_challenge="ch",
            authorize_url="https://t.example.com/authorize",
        )
        assert "code_challenge=ch" in url
        assert "redirect_uri=http://localhost:1" in url
        assert "state=st" in url

    @respx.mock
    async def test_exchange_pasted_redirect_success(
        self, tmp_path: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RTRADE_TOKEN_DIR", str(tmp_path))
        monkeypatch.delenv("RTRADE_TOKEN_KEY", raising=False)

        respx.post("https://t.example.com/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        )
        prov = OAuth2Provider(
            provider_id="paste_test",
            token_url="https://t.example.com/token",
            client_id="cid",
        )
        tok = await prov.exchange_pasted_redirect(
            redirect_response="http://localhost:1?code=ABC&state=s",
            redirect_uri="http://localhost:1",
            code_verifier="verifier",
        )
        assert tok.access_token == "tok"

    async def test_exchange_no_code_raises(self) -> None:
        prov = OAuth2Provider(
            provider_id="bad",
            token_url="https://t.example.com/token",
            client_id="cid",
        )
        with pytest.raises(ValueError, match="code"):
            await prov.exchange_pasted_redirect(
                redirect_response="http://localhost:1?error=denied",
                redirect_uri="http://localhost:1",
                code_verifier="v",
            )
