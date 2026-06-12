"""Tests for model catalog (O13)."""

from __future__ import annotations

import httpx
import pytest
import respx

from rtrade.llm.auth.model_catalog import list_provider_models
from rtrade.llm.auth.provider_profiles import OAuthProviderProfile


def _profile(**overrides: object) -> OAuthProviderProfile:
    defaults = {
        "label": "Test Provider",
        "auth_mode": "oauth2",
        "capability": "oauth_gateway",
        "enabled": True,
        "models": ["model-a", "model-b"],
    }
    defaults.update(overrides)
    return OAuthProviderProfile(**defaults)  # type: ignore[arg-type]


class TestListProviderModels:
    async def test_static_only(self) -> None:
        profile = _profile()
        models = await list_provider_models(profile)
        assert models == ["model-a", "model-b"]

    @respx.mock
    async def test_with_discovery(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_MODELS_URL", "https://api.example.com/v1/models")
        profile = _profile(models_url_env="TEST_MODELS_URL")

        respx.get("https://api.example.com/v1/models").mock(
            return_value=httpx.Response(
                200,
                json={"data": [{"id": "discovered-x"}, {"id": "model-a"}]},
            )
        )
        models = await list_provider_models(profile)
        # model-a dedup, discovered-x added
        assert "model-a" in models
        assert "model-b" in models
        assert "discovered-x" in models
        assert models.count("model-a") == 1  # no duplicates

    @respx.mock
    async def test_discovery_error_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_MODELS_URL", "https://api.example.com/v1/models")
        profile = _profile(models_url_env="TEST_MODELS_URL")

        respx.get("https://api.example.com/v1/models").mock(return_value=httpx.Response(500))
        # Should fall back to static catalog
        models = await list_provider_models(profile)
        assert models == ["model-a", "model-b"]

    async def test_no_models_at_all(self) -> None:
        profile = _profile(models=[], models_url_env="")
        models = await list_provider_models(profile)
        assert models == []
