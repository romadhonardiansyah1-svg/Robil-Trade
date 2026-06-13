"""Provider capability policy: subscription OAuth + consumer token guard (A0)."""

from __future__ import annotations

import pytest

from rtrade.llm.auth.provider_profiles import (
    OAuthProviderProfile,
    is_subscription_oauth,
    validate_provider_profile,
)


def _profile(**overrides: object) -> OAuthProviderProfile:
    data: dict[str, object] = {
        "label": "test",
        "auth_mode": "oauth2",
        "capability": "subscription_oauth",
        "enabled": True,
        "note": "",
        "device_auth_url": "https://example.invalid/device",
        "token_url": "https://example.invalid/token",
    }
    data.update(overrides)
    return OAuthProviderProfile(**data)  # type: ignore[arg-type]


def test_codex_oauth_is_subscription_oauth() -> None:
    profile = _profile(label="OpenAI Codex OAuth (langganan ChatGPT)")
    assert is_subscription_oauth("codex_oauth", profile)
    assert validate_provider_profile("codex_oauth", profile) == []


def test_xai_oauth_is_subscription_oauth() -> None:
    profile = _profile(
        label="xAI Grok OAuth (langganan SuperGrok)",
        device_auth_url="https://accounts.x.ai/oauth/authorize/device",
        token_url="https://accounts.x.ai/oauth/token",
    )
    assert is_subscription_oauth("xai_oauth", profile)
    assert validate_provider_profile("xai_oauth", profile) == []


def test_subscription_oauth_requires_device_auth_url() -> None:
    profile = _profile(device_auth_url="", device_auth_url_env="")
    issues = validate_provider_profile("codex_oauth", profile)
    assert any("device_auth_url" in i for i in issues)


def test_xai_api_key_is_not_subscription_oauth() -> None:
    profile = _profile(
        label="xAI official API key",
        auth_mode="api_key",
        capability="api_key",
    )
    assert not is_subscription_oauth("xai_api", profile)


def test_consumer_token_source_guard() -> None:
    profile = _profile(
        auth_mode="external_command",
        capability="external_adapter",
        external_command=["tool", "--read", "~/.codex/auth.json"],
    )
    issues = validate_provider_profile("custom_adapter", profile)
    assert any(".codex" in i for i in issues)


def test_registry_builds_codex_oauth() -> None:
    """codex_oauth manifest can build a provider (mock-safe: no actual HTTP)."""
    from rtrade.llm.auth.registry import build_provider_from_profile

    prov = build_provider_from_profile("codex_oauth")
    assert prov.provider_id == "codex_oauth"
    assert "auth.openai.com" in prov.device_auth_url or prov.device_auth_url != ""


def test_registry_builds_xai_oauth() -> None:
    from rtrade.llm.auth.registry import build_provider_from_profile

    prov = build_provider_from_profile("xai_oauth")
    assert prov.provider_id == "xai_oauth"
    assert "accounts.x.ai" in prov.device_auth_url or prov.device_auth_url != ""


def test_disabled_alias_rejected() -> None:
    """codex_openai alias is disabled and should raise."""
    from rtrade.core.errors import ConfigError
    from rtrade.llm.auth.registry import build_provider_from_profile

    with pytest.raises(ConfigError, match="disabled"):
        build_provider_from_profile("codex_openai")
