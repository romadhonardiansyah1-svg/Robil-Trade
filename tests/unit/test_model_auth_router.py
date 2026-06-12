"""Tests for model auth router (O11)."""

from __future__ import annotations

import pytest

from rtrade.core.errors import ConfigError
from rtrade.llm.auth.api_key import ApiKeyProvider
from rtrade.llm.model_router import ResolvedModelAuth, resolve_model_auth


class _FakeLLMSettings:
    auth_mode: str = "api_key"
    analyst_model: str = "gemini/gemini-3.1-flash-lite"
    critic_model: str = "gemini/gemini-3.1-flash-lite"
    flagship_model: str = "gemini/gemini-2.5-pro"
    vertex_project: str = "proj"
    vertex_location: str = "us-central1"
    model_routes: dict[str, object] = {}
    auth_profiles: dict[str, object] = {}
    default_auth_profile: str = ""


class _FakeSecrets:
    gemini_api_key_1: str = "test-key"


class _FakeSettings:
    def __init__(self, llm: object = None) -> None:
        self.llm = llm or _FakeLLMSettings()


class _FakeConfig:
    def __init__(self, llm: object = None) -> None:
        self.settings = _FakeSettings(llm)
        self.secrets = _FakeSecrets()


class TestBackwardCompat:
    def test_no_routes_uses_old_behavior(self) -> None:
        cfg = _FakeConfig()
        result = resolve_model_auth(cfg, "analyst")  # type: ignore[arg-type]
        assert isinstance(result, ResolvedModelAuth)
        assert result.model == "gemini/gemini-3.1-flash-lite"
        assert isinstance(result.credential_provider, ApiKeyProvider)

    def test_unknown_role_falls_back(self) -> None:
        cfg = _FakeConfig()
        result = resolve_model_auth(cfg, "unknown")  # type: ignore[arg-type]
        assert result.model == "gemini/gemini-3.1-flash-lite"


class TestWithRoutes:
    def test_api_key_profile(self) -> None:
        llm = _FakeLLMSettings()
        llm.auth_profiles = {
            "gemini_key": {
                "auth_type": "api_key",
                "provider_id": "google_ai_studio",
                "api_key_secret": "gemini_api_key_1",
            }
        }
        llm.model_routes = {
            "analyst": {"model": "gemini/gemini-2.5-pro", "auth_profile": "gemini_key"}
        }
        cfg = _FakeConfig(llm)
        result = resolve_model_auth(cfg, "analyst")  # type: ignore[arg-type]
        assert result.model == "gemini/gemini-2.5-pro"
        assert isinstance(result.credential_provider, ApiKeyProvider)

    def test_missing_profile_raises(self) -> None:
        llm = _FakeLLMSettings()
        llm.auth_profiles = {}
        llm.model_routes = {"analyst": {"model": "m", "auth_profile": "nonexistent"}}
        cfg = _FakeConfig(llm)
        with pytest.raises(ConfigError, match="nonexistent"):
            resolve_model_auth(cfg, "analyst")  # type: ignore[arg-type]

    def test_cli_oauth_disabled_raises(self) -> None:
        llm = _FakeLLMSettings()
        llm.auth_profiles = {
            "xai_oauth": {
                "auth_type": "cli_oauth",
                "provider_id": "xai",
                "enabled": False,
            }
        }
        llm.model_routes = {"analyst": {"model": "xai/grok", "auth_profile": "xai_oauth"}}
        cfg = _FakeConfig(llm)
        with pytest.raises(ConfigError, match="disabled"):
            resolve_model_auth(cfg, "analyst")  # type: ignore[arg-type]

    def test_literal_api_key_rejected(self) -> None:
        llm = _FakeLLMSettings()
        llm.auth_profiles = {
            "bad": {
                "auth_type": "api_key",
                "provider_id": "x",
                "api_key_secret": "sk-ant-very-bad-literal",
            }
        }
        llm.model_routes = {"analyst": {"model": "m", "auth_profile": "bad"}}
        cfg = _FakeConfig(llm)
        with pytest.raises(ConfigError, match="literal API key"):
            resolve_model_auth(cfg, "analyst")  # type: ignore[arg-type]
