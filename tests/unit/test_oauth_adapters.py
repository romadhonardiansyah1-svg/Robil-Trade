"""Tests for OAuth adapters (O9)."""

from __future__ import annotations

import pytest

from rtrade.core.errors import ConfigError
from rtrade.llm.auth.adapters.generic_oidc import build_generic_oidc_provider
from rtrade.llm.auth.adapters.openai_codex import build_openai_codex_provider
from rtrade.llm.auth.adapters.xai import build_xai_provider
from rtrade.llm.auth.provider_profiles import OAuthProviderProfile


def _profile(**overrides: object) -> OAuthProviderProfile:
    defaults = {
        "label": "Test",
        "auth_mode": "oauth2",
        "capability": "disabled_unsupported",
        "enabled": False,
        "token_url_env": "",
        "client_id_env": "",
        "transport": "HTTPS",
    }
    defaults.update(overrides)
    return OAuthProviderProfile(**defaults)  # type: ignore[arg-type]


class TestCodexOpenAI:
    def test_without_endpoint_raises(self) -> None:
        profile = _profile()
        with pytest.raises(ConfigError, match="codex_openai OAuth belum aktif"):
            build_openai_codex_provider(profile)

    def test_with_gateway_capability(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_TOKEN", "https://tok.example.com/token")
        monkeypatch.setenv("CODEX_CID", "cid")
        profile = _profile(
            capability="oauth_gateway",
            token_url_env="CODEX_TOKEN",
            client_id_env="CODEX_CID",
        )
        provider = build_openai_codex_provider(profile)
        assert provider.provider_id == "codex_openai"
        assert provider.token_url == "https://tok.example.com/token"

    def test_with_official_oauth_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OAI_TOK", "https://auth.openai.com/token")
        monkeypatch.setenv("OAI_CID", "cid2")
        profile = _profile(
            token_url_env="OAI_TOK",
            client_id_env="OAI_CID",
        )
        provider = build_openai_codex_provider(profile)
        assert provider.provider_id == "codex_openai"


class TestXAI:
    def test_without_endpoint_raises(self) -> None:
        profile = _profile()
        with pytest.raises(ConfigError, match="xAI OAuth belum tersedia"):
            build_xai_provider(profile)

    def test_with_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XAI_TOK", "https://xai.example.com/token")
        monkeypatch.setenv("XAI_CID", "xci")
        profile = _profile(
            token_url_env="XAI_TOK",
            client_id_env="XAI_CID",
        )
        provider = build_xai_provider(profile)
        assert provider.provider_id == "xai"


class TestGenericOIDC:
    def test_issuer_mismatch_rejected(self) -> None:
        profile = _profile()
        endpoints = {"token_endpoint": "https://t.com/token", "issuer": "https://wrong.com"}
        with pytest.raises(ConfigError, match="issuer mismatch"):
            build_generic_oidc_provider(profile, endpoints, expected_issuer="https://correct.com")

    def test_with_discovered_endpoints(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OIDC_CID", "cid")
        profile = _profile(client_id_env="OIDC_CID")
        endpoints = {
            "token_endpoint": "https://oidc.example.com/token",
            "device_authorization_endpoint": "https://oidc.example.com/device",
            "issuer": "https://oidc.example.com",
        }
        provider = build_generic_oidc_provider(
            profile, endpoints, expected_issuer="https://oidc.example.com"
        )
        assert provider.token_url == "https://oidc.example.com/token"
        assert provider.device_auth_url == "https://oidc.example.com/device"

    def test_no_token_url_raises(self) -> None:
        profile = _profile()
        with pytest.raises(ConfigError, match="token_url tidak ditemukan"):
            build_generic_oidc_provider(profile)
