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
