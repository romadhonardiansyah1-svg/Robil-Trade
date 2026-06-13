"""Tests for OAuth2Provider (O3) — respx mocked."""

from __future__ import annotations

from pathlib import Path
import time

import httpx
import pytest
import respx

from rtrade.llm.auth.oauth2 import OAuth2Provider, generate_pkce_pair
from rtrade.llm.auth.token_store import StoredToken, load_token, save_token


@pytest.fixture()
def _token_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RTRADE_TOKEN_DIR", str(tmp_path))
    monkeypatch.delenv("RTRADE_TOKEN_KEY", raising=False)


TOKEN_URL = "https://oauth.example.com/token"
DEVICE_URL = "https://oauth.example.com/device/code"


def _make_provider(**overrides: object) -> OAuth2Provider:
    defaults = {
        "provider_id": "test_prov",
        "token_url": TOKEN_URL,
        "client_id": "cid",
        "client_secret": "csec",
        "scopes": ["read"],
        "grant_type": "client_credentials",
        "device_auth_url": DEVICE_URL,
    }
    defaults.update(overrides)
    return OAuth2Provider(**defaults)  # type: ignore[arg-type]


class TestClientCredentials:
    @pytest.mark.usefixtures("_token_env")
    @respx.mock
    async def test_client_credentials_flow(self) -> None:
        respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "acc_tok",
                    "expires_in": 3600,
                },
            )
        )
        prov = _make_provider()
        mat = await prov.resolve()
        assert mat.bearer_token == "acc_tok"
        assert mat.auth_type == "cli_oauth"

    @pytest.mark.usefixtures("_token_env")
    @respx.mock
    async def test_cached_token_no_http(self) -> None:
        """Token belum expiry → tidak ada HTTP call kedua."""
        save_token(
            "test_prov",
            StoredToken("cached_tok", "ref", time.time() + 9999, ["read"]),
        )
        # No mock needed — if HTTP is called, respx would fail
        prov = _make_provider()
        mat = await prov.resolve()
        assert mat.bearer_token == "cached_tok"


class TestRefresh:
    @pytest.mark.usefixtures("_token_env")
    @respx.mock
    async def test_refresh_on_expiry(self) -> None:
        """Token expired + refresh_token → panggil refresh."""
        save_token(
            "test_prov",
            StoredToken("old_tok", "ref_tok", time.time() - 999, ["read"]),
        )
        respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "new_tok",
                    "expires_in": 3600,
                },
            )
        )
        prov = _make_provider()
        mat = await prov.resolve()
        assert mat.bearer_token == "new_tok"
        # Verify old refresh_token is preserved (provider didn't send new one)
        stored = load_token("test_prov")
        assert stored is not None
        assert stored.refresh_token == "ref_tok"


class TestPKCE:
    def test_generate_pkce_pair(self) -> None:
        verifier, challenge = generate_pkce_pair()
        assert len(verifier) > 40
        assert len(challenge) > 20
        assert verifier != challenge

    def test_build_authorize_url(self) -> None:
        prov = _make_provider()
        url = prov.build_authorize_url(
            redirect_uri="http://localhost:1",
            state="s123",
            code_challenge="ch456",
            authorize_url="https://oauth.example.com/authorize",
        )
        assert "code_challenge=ch456" in url
        assert "state=s123" in url
        assert "redirect_uri=http://localhost:1" in url

    @pytest.mark.usefixtures("_token_env")
    @respx.mock
    async def test_exchange_pasted_redirect(self) -> None:
        respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={"access_token": "exchanged", "expires_in": 3600},
            )
        )
        prov = _make_provider()
        tok = await prov.exchange_pasted_redirect(
            redirect_response="http://localhost:1?code=ABC&state=s",
            redirect_uri="http://localhost:1",
            code_verifier="verifier123",
        )
        assert tok.access_token == "exchanged"

    @pytest.mark.usefixtures("_token_env")
    async def test_exchange_no_code_raises(self) -> None:
        prov = _make_provider()
        with pytest.raises(ValueError, match="code"):
            await prov.exchange_pasted_redirect(
                redirect_response="http://localhost:1?error=denied",
                redirect_uri="http://localhost:1",
                code_verifier="v",
            )


class TestNoTokenRaisesError:
    @pytest.mark.usefixtures("_token_env")
    async def test_device_code_grant_no_token(self) -> None:
        prov = _make_provider(grant_type="device_code")
        with pytest.raises(RuntimeError, match="rtrade auth login"):
            await prov.resolve()
