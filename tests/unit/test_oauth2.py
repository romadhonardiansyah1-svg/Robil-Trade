"""Tests for OAuth2Provider (O3) — respx mocked."""

from __future__ import annotations

from pathlib import Path
import time

import httpx
import pytest
import respx
import structlog

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


class TestPkcePasteLogin:
    """PKCE paste-URL login (VPS-ready secure flow for xAI/Grok/Qwen/Google)."""

    @pytest.mark.usefixtures("_token_env")
    @respx.mock
    async def test_matching_state_completes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Deterministic state/verifier so the test can assert on the authorize URL.
        monkeypatch.setattr(
            "rtrade.llm.auth.oauth2.secrets.token_urlsafe", lambda _n=32: "FIXEDSTATE"
        )
        captured: list[str] = []
        monkeypatch.setattr(
            "builtins.print", lambda *a, **_k: captured.append(" ".join(str(x) for x in a))
        )
        monkeypatch.setattr(
            "builtins.input",
            lambda _prompt="": "http://127.0.0.1:56121/callback?code=ABC&state=FIXEDSTATE",
        )
        respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "pkce_tok", "expires_in": 3600})
        )
        prov = _make_provider()
        tok = await prov.pkce_paste_login(
            authorize_url="https://accounts.x.ai/oauth/authorize",
            redirect_uri="http://127.0.0.1:56121/callback",
        )
        assert tok.access_token == "pkce_tok"
        blob = "\n".join(captured)
        # The authorize URL printed to the user must carry our state + challenge.
        assert "state=FIXEDSTATE" in blob
        assert "code_challenge=" in blob
        assert "accounts.x.ai/oauth/authorize" in blob

    @pytest.mark.usefixtures("_token_env")
    @respx.mock
    async def test_state_mismatch_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "rtrade.llm.auth.oauth2.secrets.token_urlsafe", lambda _n=32: "FIXEDSTATE"
        )
        monkeypatch.setattr("builtins.print", lambda *_a, **_k: None)
        monkeypatch.setattr(
            "builtins.input",
            lambda _prompt="": "http://127.0.0.1:56121/callback?code=ABC&state=ATTACKER",
        )
        prov = _make_provider()
        with pytest.raises(ValueError, match="state mismatch"):
            await prov.pkce_paste_login(
                authorize_url="https://accounts.x.ai/oauth/authorize",
                redirect_uri="http://127.0.0.1:56121/callback",
            )

    @pytest.mark.usefixtures("_token_env")
    @respx.mock
    async def test_bare_code_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A bare code without state param is accepted (no state to verify)."""
        monkeypatch.setattr("rtrade.llm.auth.oauth2.secrets.token_urlsafe", lambda _n=32: "S")
        monkeypatch.setattr("builtins.print", lambda *_a, **_k: None)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "BARECODE123")
        respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "bare_tok", "expires_in": 3600})
        )
        prov = _make_provider()
        tok = await prov.pkce_paste_login(
            authorize_url="https://accounts.x.ai/oauth/authorize",
            redirect_uri="http://127.0.0.1:56121/callback",
        )
        assert tok.access_token == "bare_tok"

    @pytest.mark.usefixtures("_token_env")
    @respx.mock
    async def test_no_token_value_logged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("rtrade.llm.auth.oauth2.secrets.token_urlsafe", lambda _n=32: "FIXED")
        monkeypatch.setattr("builtins.print", lambda *_a, **_k: None)
        monkeypatch.setattr(
            "builtins.input",
            lambda _prompt="": "http://127.0.0.1:56121/callback?code=ABC&state=FIXED",
        )
        respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": "SECRET_TOK", "expires_in": 3600}
            )
        )
        prov = _make_provider()
        with structlog.testing.capture_logs() as logs:
            tok = await prov.pkce_paste_login(
                authorize_url="https://accounts.x.ai/oauth/authorize",
                redirect_uri="http://127.0.0.1:56121/callback",
            )
        assert tok.access_token == "SECRET_TOK"
        assert "SECRET_TOK" not in repr(logs)


class TestNoTokenRaisesError:
    @pytest.mark.usefixtures("_token_env")
    async def test_device_code_grant_no_token(self) -> None:
        prov = _make_provider(grant_type="device_code")
        with pytest.raises(RuntimeError, match="rtrade auth login"):
            await prov.resolve()


# C2: OAuth token bodies must never reach logs or exception messages.
ACCESS_SENTINEL = "SENTINEL_ACCESS"
REFRESH_SENTINEL = "SENTINEL_REFRESH"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"


def _mock_codex_device_init() -> None:
    """Codex-style device-init: triggers the 2-step authorization_code exchange."""
    respx.post(DEVICE_URL).mock(
        return_value=httpx.Response(
            200,
            json={"device_auth_id": "dev-123", "user_code": "WXYZ", "interval": 0},
        )
    )
    # First poll returns an authorization_code → forces the token-exchange path.
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={"authorization_code": "AUTHCODE", "code_verifier": "VERIF"},
        )
    )


class TestTokenExchangeNoLeak:
    """C2: token-exchange must not leak token bodies to logs or exceptions."""

    @pytest.mark.usefixtures("_token_env")
    @respx.mock
    async def test_token_exchange_does_not_log_token_values(self) -> None:
        _mock_codex_device_init()
        respx.post(CODEX_TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": ACCESS_SENTINEL,
                    "refresh_token": REFRESH_SENTINEL,
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "scope": "read",
                },
            )
        )
        prov = _make_provider(grant_type="device_code")
        with structlog.testing.capture_logs() as logs:
            tok = await prov.device_login()

        # Behavior preserved: caller still receives the real tokens.
        assert tok.access_token == ACCESS_SENTINEL
        assert tok.refresh_token == REFRESH_SENTINEL

        # No emitted log record may contain the access or refresh token.
        blob = repr(logs)
        assert ACCESS_SENTINEL not in blob
        assert REFRESH_SENTINEL not in blob

    @pytest.mark.usefixtures("_token_env")
    @respx.mock
    async def test_failed_exchange_error_message_omits_body(self) -> None:
        _mock_codex_device_init()
        respx.post(CODEX_TOKEN_URL).mock(
            return_value=httpx.Response(
                400,
                json={
                    "error": "invalid_grant",
                    "leaked_access": ACCESS_SENTINEL,
                    "leaked_refresh": REFRESH_SENTINEL,
                },
            )
        )
        prov = _make_provider(grant_type="device_code")
        with structlog.testing.capture_logs() as logs, pytest.raises(RuntimeError) as excinfo:
            await prov.device_login()

        msg = str(excinfo.value)
        assert ACCESS_SENTINEL not in msg
        assert REFRESH_SENTINEL not in msg
        # And the failed body must not have been logged either.
        blob = repr(logs)
        assert ACCESS_SENTINEL not in blob
        assert REFRESH_SENTINEL not in blob


# C7: device-code poll loop must be bounded by expires_in + a max-iteration cap.
class TestDeviceLoginBounded:
    @pytest.mark.usefixtures("_token_env")
    @respx.mock
    async def test_device_login_times_out_when_never_authorized(self) -> None:
        """RFC 8628 poll that never returns a token must raise a timeout RuntimeError,
        not loop forever."""
        respx.post(DEVICE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "device_code": "dev-code-123",
                    "user_code": "WXYZ",
                    "verification_uri": "https://example.com/device",
                    "interval": 0,
                    "expires_in": 1,
                },
            )
        )
        poll_route = respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"error": "authorization_pending"})
        )

        prov = _make_provider(grant_type="device_code")
        with pytest.raises(RuntimeError, match=r"(?i)timeout|expired|max"):
            await prov.device_login()

        # Must have polled a bounded number of times — proof it did not hang forever.
        assert poll_route.call_count >= 1
        assert poll_route.call_count < 100_000
