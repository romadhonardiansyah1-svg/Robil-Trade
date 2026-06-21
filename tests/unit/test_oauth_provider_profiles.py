"""Tests for OAuth provider profiles (O8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rtrade.llm.auth.provider_profiles import (
    OAuthProviderProfile,
    load_provider_profiles,
    resolve_env_profile,
    validate_profile,
)


@pytest.fixture()
def _profiles_dir(tmp_path: Path) -> Path:
    manifest = tmp_path / "oauth_providers.yaml"
    manifest.write_text(
        """
oauth_providers:
  google_vertex:
    label: "Google Vertex AI"
    auth_mode: vertex
    capability: vertex_adc
    enabled: true
    transport: HTTPS
    requires_official_oauth: true
  codex_openai:
    label: "Codex OpenAI"
    auth_mode: oauth2
    capability: disabled_unsupported
    enabled: false
    token_url_env: RTRADE_OPENAI_OAUTH_TOKEN_URL
    client_id_env: RTRADE_OPENAI_OAUTH_CLIENT_ID
    transport: HTTPS
    requires_official_oauth: true
  generic_gateway:
    label: "Gateway"
    auth_mode: oauth2
    capability: oauth_gateway
    enabled: true
    token_url_env: RTRADE_OAUTH_TOKEN_URL
    client_id_env: RTRADE_OAUTH_CLIENT_ID
    scopes_env: RTRADE_OAUTH_SCOPES
    device_auth_url_env: RTRADE_OAUTH_DEVICE_URL
    transport: HTTPS
""",
        encoding="utf-8",
    )
    return tmp_path


class TestLoadProfiles:
    def test_loads_all_providers(self, _profiles_dir: Path) -> None:
        profiles = load_provider_profiles(_profiles_dir)
        assert "google_vertex" in profiles
        assert "codex_openai" in profiles
        assert "generic_gateway" in profiles

    def test_google_vertex_enabled(self, _profiles_dir: Path) -> None:
        profiles = load_provider_profiles(_profiles_dir)
        assert profiles["google_vertex"].enabled is True
        assert profiles["google_vertex"].auth_mode == "vertex"

    def test_codex_disabled(self, _profiles_dir: Path) -> None:
        profiles = load_provider_profiles(_profiles_dir)
        assert profiles["codex_openai"].enabled is False
        assert profiles["codex_openai"].capability == "disabled_unsupported"


class TestResolveEnvProfile:
    def test_resolve_with_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_TOKEN_URL", "https://tok.example.com/token")
        monkeypatch.setenv("MY_CLIENT_ID", "cid")
        profile = OAuthProviderProfile(
            label="test",
            auth_mode="oauth2",
            capability="oauth_gateway",
            enabled=True,
            token_url_env="MY_TOKEN_URL",
            client_id_env="MY_CLIENT_ID",
        )
        resolved = resolve_env_profile(profile)
        assert resolved.token_url == "https://tok.example.com/token"
        assert resolved.client_id == "cid"
        assert resolved.has_official_oauth_endpoint is True

    def test_resolve_missing_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NONEXISTENT_TOKEN_URL", raising=False)
        profile = OAuthProviderProfile(
            label="test",
            auth_mode="oauth2",
            capability="oauth_gateway",
            enabled=True,
            token_url_env="NONEXISTENT_TOKEN_URL",
        )
        resolved = resolve_env_profile(profile)
        assert resolved.token_url == ""
        assert resolved.has_official_oauth_endpoint is False


class TestValidateProfile:
    def test_valid_vertex(self) -> None:
        profile = OAuthProviderProfile(
            label="Vertex",
            auth_mode="vertex",
            capability="vertex_adc",
            enabled=True,
            transport="HTTPS",
        )
        assert validate_profile(profile) == []

    def test_oauth2_without_token_url(self) -> None:
        profile = OAuthProviderProfile(
            label="Bad",
            auth_mode="oauth2",
            capability="oauth_gateway",
            enabled=True,
            token_url_env="",  # missing!
            transport="HTTPS",
        )
        issues = validate_profile(profile)
        assert any("token_url_env" in i for i in issues)

    def test_subscription_oauth_without_device_url_warns(self) -> None:
        """subscription_oauth tanpa device_auth_url wajib menghasilkan warning."""
        from rtrade.llm.auth.provider_profiles import validate_provider_profile

        profile = OAuthProviderProfile(
            label="Bad",
            auth_mode="oauth2",
            capability="subscription_oauth",
            enabled=True,
            token_url_env="X",
            transport="HTTPS",
        )
        issues = validate_provider_profile("test_bad", profile)
        assert any("device_auth_url" in i for i in issues)


class TestPkceProfileFields:
    """PKCE loopback / paste-URL provider fields (authorize_url + redirect_uri)."""

    def test_resolve_authorize_and_redirect_inline(self) -> None:
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
        resolved = resolve_env_profile(profile)
        assert resolved.authorize_url == "https://accounts.x.ai/oauth/authorize"
        assert resolved.redirect_uri == "http://127.0.0.1:56121/callback"

    def test_resolve_authorize_and_redirect_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_AUTH_URL", "https://auth.example.com/authorize")
        monkeypatch.setenv("MY_REDIRECT", "http://127.0.0.1:9999/cb")
        profile = OAuthProviderProfile(
            label="x",
            auth_mode="oauth2",
            capability="subscription_oauth",
            enabled=True,
            login_flow="pkce_loopback",
            authorize_url_env="MY_AUTH_URL",
            redirect_uri_env="MY_REDIRECT",
            token_url="https://t.example.com",
        )
        resolved = resolve_env_profile(profile)
        assert resolved.authorize_url == "https://auth.example.com/authorize"
        assert resolved.redirect_uri == "http://127.0.0.1:9999/cb"

    def test_inline_preferred_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_AUTH_URL", "https://env.example.com/authorize")
        profile = OAuthProviderProfile(
            label="x",
            auth_mode="oauth2",
            capability="subscription_oauth",
            enabled=True,
            login_flow="pkce_loopback",
            authorize_url="https://inline.example.com/authorize",
            authorize_url_env="MY_AUTH_URL",
            token_url="https://t.example.com",
        )
        resolved = resolve_env_profile(profile)
        assert resolved.authorize_url == "https://inline.example.com/authorize"

    def test_pkce_loopback_valid_without_device_url(self) -> None:
        from rtrade.llm.auth.provider_profiles import validate_provider_profile

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
        assert validate_provider_profile("xai_oauth", profile) == []

    def test_pkce_loopback_without_authorize_url_warns(self) -> None:
        from rtrade.llm.auth.provider_profiles import validate_provider_profile

        profile = OAuthProviderProfile(
            label="xai",
            auth_mode="oauth2",
            capability="subscription_oauth",
            enabled=True,
            login_flow="pkce_loopback",
            token_url="https://accounts.x.ai/oauth/token",
        )
        issues = validate_provider_profile("xai_oauth", profile)
        assert any("authorize_url" in i for i in issues)


class TestExampleManifestXai:
    """The shipped example manifest must configure xAI for PKCE loopback."""

    def test_xai_oauth_is_pkce_loopback(self) -> None:
        from rtrade.llm.auth.provider_profiles import validate_provider_profile

        profiles = load_provider_profiles(Path("config"))
        xai = profiles["xai_oauth"]
        assert xai.login_flow == "pkce_loopback"
        assert xai.auth_mode == "oauth2"
        assert xai.capability == "subscription_oauth"
        assert xai.authorize_url
        assert xai.redirect_uri
        assert xai.token_url
        assert validate_provider_profile("xai_oauth", xai) == []

    def test_codex_oauth_still_device_code(self) -> None:
        from rtrade.llm.auth.provider_profiles import validate_provider_profile

        profiles = load_provider_profiles(Path("config"))
        codex = profiles["codex_oauth"]
        assert codex.login_flow == "device_code"
        assert validate_provider_profile("codex_oauth", codex) == []
