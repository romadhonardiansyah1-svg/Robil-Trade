"""auth login: per-provider OAuth flow dispatch (device_code vs pkce paste-URL)."""

from __future__ import annotations

from argparse import Namespace

import pytest

from rtrade.llm.auth.provider_profiles import OAuthProviderProfile, ResolvedOAuthProfile
from rtrade.llm.auth.token_store import StoredToken


class _FakeProvider:
    def __init__(self) -> None:
        self.device_called = False
        self.pkce_called = False
        self.pkce_kwargs: dict[str, str] = {}

    async def device_login(self) -> StoredToken:
        self.device_called = True
        return StoredToken("tok", None, 0.0, [])

    async def pkce_paste_login(self, *, authorize_url: str, redirect_uri: str) -> StoredToken:
        self.pkce_called = True
        self.pkce_kwargs = {"authorize_url": authorize_url, "redirect_uri": redirect_uri}
        return StoredToken("tok", None, 0.0, [])


def _login_args(provider: str) -> Namespace:
    return Namespace(
        provider=provider,
        account="default",
        flow=None,
        manual_paste=False,
        no_browser=False,
    )


def test_pkce_profile_calls_pkce_paste_login(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = OAuthProviderProfile(
        label="xai",
        auth_mode="oauth2",
        capability="subscription_oauth",
        enabled=True,
        login_flow="pkce_loopback",
        authorize_url="https://accounts.x.ai/oauth/authorize",
        token_url="https://accounts.x.ai/oauth/token",
        redirect_uri="http://127.0.0.1:56121/callback",
        client_id="cid",
    )
    resolved = ResolvedOAuthProfile(
        token_url="https://accounts.x.ai/oauth/token",
        client_id="cid",
        authorize_url="https://accounts.x.ai/oauth/authorize",
        redirect_uri="http://127.0.0.1:56121/callback",
    )
    fake = _FakeProvider()
    monkeypatch.setattr(
        "rtrade.llm.auth.provider_profiles.load_provider_profiles",
        lambda _p: {"xai_oauth": profile},
    )
    monkeypatch.setattr(
        "rtrade.llm.auth.provider_profiles.resolve_env_profile", lambda _p: resolved
    )
    monkeypatch.setattr(
        "rtrade.llm.auth.registry.build_provider_from_profile", lambda _pid, **_kw: fake
    )

    from rtrade.cli.auth import _cmd_login

    _cmd_login(_login_args("xai_oauth"))
    assert fake.pkce_called is True
    assert fake.device_called is False
    assert fake.pkce_kwargs["authorize_url"] == "https://accounts.x.ai/oauth/authorize"
    assert fake.pkce_kwargs["redirect_uri"] == "http://127.0.0.1:56121/callback"


def test_device_profile_calls_device_login(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = OAuthProviderProfile(
        label="codex",
        auth_mode="oauth2",
        capability="subscription_oauth",
        enabled=True,
        login_flow="device_code",
        device_auth_url="https://auth.openai.com/device",
        token_url="https://auth.openai.com/token",
        client_id="cid",
    )
    fake = _FakeProvider()
    monkeypatch.setattr(
        "rtrade.llm.auth.provider_profiles.load_provider_profiles",
        lambda _p: {"codex_oauth": profile},
    )
    monkeypatch.setattr(
        "rtrade.llm.auth.registry.build_provider_from_profile", lambda _pid, **_kw: fake
    )

    from rtrade.cli.auth import _cmd_login

    _cmd_login(_login_args("codex_oauth"))
    assert fake.device_called is True
    assert fake.pkce_called is False


def test_pkce_missing_client_id_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = OAuthProviderProfile(
        label="xai",
        auth_mode="oauth2",
        capability="subscription_oauth",
        enabled=True,
        login_flow="pkce_loopback",
        authorize_url="https://accounts.x.ai/oauth/authorize",
        token_url="https://accounts.x.ai/oauth/token",
        redirect_uri="http://127.0.0.1:56121/callback",
        client_id_env="RTRADE_XAI_CLIENT_ID",
    )
    resolved = ResolvedOAuthProfile(
        token_url="https://accounts.x.ai/oauth/token",
        client_id="",  # missing → must abort
        authorize_url="https://accounts.x.ai/oauth/authorize",
        redirect_uri="http://127.0.0.1:56121/callback",
    )
    monkeypatch.setattr(
        "rtrade.llm.auth.provider_profiles.load_provider_profiles",
        lambda _p: {"xai_oauth": profile},
    )
    monkeypatch.setattr(
        "rtrade.llm.auth.provider_profiles.resolve_env_profile", lambda _p: resolved
    )

    from rtrade.cli.auth import _cmd_login

    with pytest.raises(SystemExit):
        _cmd_login(_login_args("xai_oauth"))
