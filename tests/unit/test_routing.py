"""Unit tests untuk set_model_route (pure YAML route+profile writer)."""

from __future__ import annotations

from pathlib import Path

import yaml


def _empty_settings(tmp_path: Path) -> Path:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    sp = cfg_dir / "settings.yaml"
    sp.write_text("llm:\n  enabled: false\n", encoding="utf-8")
    return sp


def test_set_model_route_cli_oauth(tmp_path) -> None:
    from rtrade.llm.auth.routing import set_model_route

    sp = _empty_settings(tmp_path)
    name = set_model_route(
        settings_path=sp,
        role="analyst",
        provider_id="generic_gateway",
        model="openai/gpt-4.1",
        auth_mode="oauth2",
        account="default",
    )
    assert name == "generic_gateway_cli_oauth"
    doc = yaml.safe_load(sp.read_text(encoding="utf-8"))
    routes = doc["llm"]["model_routes"]
    profiles = doc["llm"]["auth_profiles"]
    assert routes["analyst"] == {
        "model": "openai/gpt-4.1",
        "auth_profile": "generic_gateway_cli_oauth",
    }
    prof = profiles["generic_gateway_cli_oauth"]
    assert prof["enabled"] is True
    assert prof["auth_type"] == "cli_oauth"
    assert prof["provider_id"] == "generic_gateway"
    assert prof["account"] == "default"


def test_set_model_route_api_key(tmp_path) -> None:
    from rtrade.llm.auth.routing import set_model_route

    sp = _empty_settings(tmp_path)
    name = set_model_route(
        settings_path=sp,
        role="critic",
        provider_id="openai_key",
        model="gpt-4o",
        auth_mode="api_key",
    )
    assert name == "openai_key_api_key"
    doc = yaml.safe_load(sp.read_text(encoding="utf-8"))
    profiles = doc["llm"]["auth_profiles"]
    prof = profiles["openai_key_api_key"]
    assert prof["enabled"] is True
    assert prof["auth_type"] == "api_key"
    # api_key entry tidak punya provider_id/account
    assert "provider_id" not in prof
    assert "account" not in prof
    assert doc["llm"]["model_routes"]["critic"] == {
        "model": "gpt-4o",
        "auth_profile": "openai_key_api_key",
    }


def test_set_model_route_vertex(tmp_path) -> None:
    from rtrade.llm.auth.routing import set_model_route

    sp = _empty_settings(tmp_path)
    name = set_model_route(
        settings_path=sp,
        role="flagship",
        provider_id="google_vertex",
        model="gemini-2.0-flash",
        auth_mode="vertex",
        vertex_project="my-proj",
    )
    assert name == "google_vertex_vertex"
    doc = yaml.safe_load(sp.read_text(encoding="utf-8"))
    prof = doc["llm"]["auth_profiles"]["google_vertex_vertex"]
    assert prof["enabled"] is True
    assert prof["auth_type"] == "vertex"
    assert prof["vertex_project"] == "my-proj"


def test_set_model_route_idempotent(tmp_path) -> None:
    from rtrade.llm.auth.routing import set_model_route

    sp = _empty_settings(tmp_path)
    set_model_route(
        settings_path=sp,
        role="analyst",
        provider_id="generic_gateway",
        model="openai/gpt-4.1",
        auth_mode="oauth2",
    )
    set_model_route(
        settings_path=sp,
        role="analyst",
        provider_id="generic_gateway",
        model="openai/gpt-4o-mini",
        auth_mode="oauth2",
    )
    doc = yaml.safe_load(sp.read_text(encoding="utf-8"))
    profiles = doc["llm"]["auth_profiles"]
    # tidak menduplikasi profile name
    assert list(profiles.keys()).count("generic_gateway_cli_oauth") == 1
    # route ter-update di tempat
    assert doc["llm"]["model_routes"]["analyst"]["model"] == "openai/gpt-4o-mini"


def test_set_model_route_preserves_manual_key(tmp_path) -> None:
    from rtrade.llm.auth.routing import set_model_route

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    sp = cfg_dir / "settings.yaml"
    sp.write_text(
        "llm:\n"
        "  enabled: false\n"
        "  auth_profiles:\n"
        "    generic_gateway_cli_oauth:\n"
        "      manual_key: keep_me\n"
        "      enabled: false\n",
        encoding="utf-8",
    )
    set_model_route(
        settings_path=sp,
        role="analyst",
        provider_id="generic_gateway",
        model="openai/gpt-4.1",
        auth_mode="oauth2",
    )
    doc = yaml.safe_load(sp.read_text(encoding="utf-8"))
    prof = doc["llm"]["auth_profiles"]["generic_gateway_cli_oauth"]
    # manual key dipertahankan (merge .update())
    assert prof["manual_key"] == "keep_me"
    # field yang dikelola di-update
    assert prof["enabled"] is True
    assert prof["auth_type"] == "cli_oauth"
