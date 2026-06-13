"""auth use: membuat entri auth_profiles, route tidak menggantung (C4)."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path


def _write_min_settings(tmp_path: Path) -> Path:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "settings.yaml").write_text("llm:\n  enabled: false\n", encoding="utf-8")
    (cfg_dir / "oauth_providers.yaml").write_text(
        "oauth_providers:\n"
        "  generic_gateway:\n"
        "    label: gw\n"
        "    auth_mode: oauth2\n"
        "    capability: oauth_gateway\n"
        "    enabled: true\n"
        "    token_url_env: RTRADE_OAUTH_TOKEN_URL\n"
        "    client_id_env: RTRADE_OAUTH_CLIENT_ID\n"
        "    transport: HTTPS\n",
        encoding="utf-8",
    )
    return cfg_dir


def test_use_creates_auth_profile_entry(tmp_path, monkeypatch) -> None:
    import yaml

    cfg_dir = _write_min_settings(tmp_path)
    monkeypatch.chdir(tmp_path)  # _cmd_use membaca config/ relatif CWD

    from rtrade.cli.auth import _cmd_use

    _cmd_use(
        Namespace(
            role="analyst",
            provider="generic_gateway",
            model="openai/gpt-4.1",
            force=True,
            account="default",
        )
    )
    doc = yaml.safe_load((cfg_dir / "settings.yaml").read_text(encoding="utf-8"))
    routes = doc["llm"]["model_routes"]
    profiles = doc["llm"]["auth_profiles"]
    pname = routes["analyst"]["auth_profile"]
    assert pname in profiles  # route TIDAK menggantung
    assert profiles[pname]["auth_type"] == "cli_oauth"
    assert profiles[pname]["provider_id"] == "generic_gateway"
    assert profiles[pname]["enabled"] is True
