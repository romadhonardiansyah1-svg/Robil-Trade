"""pool_builder: auto-pool dari Secrets + token store + ADC (A7)."""

from __future__ import annotations

import pytest

from rtrade.core.errors import ConfigError


def _cfg(monkeypatch, tmp_path, **secrets_overrides):
    """AppConfig minimal via default config + env overrides."""
    monkeypatch.setenv("RTRADE_TOKEN_DIR", str(tmp_path / "tok"))
    monkeypatch.setenv("RTRADE_ADC_DIR", str(tmp_path / "adc"))
    from rtrade.core.config import AppConfig, Secrets

    cfg = AppConfig.load()
    object.__setattr__(cfg, "secrets", Secrets(**secrets_overrides))
    return cfg


def test_pool_multi_gemini_keys(monkeypatch, tmp_path) -> None:
    cfg = _cfg(
        monkeypatch,
        tmp_path,
        gemini_api_key_1="AIza1",
        gemini_api_key_2="AIza2",
        gemini_api_key_3="AIza3",
    )
    from rtrade.llm.pool_builder import build_scan_pool

    pool = build_scan_pool(cfg)
    ids = [e.cred_id for e in pool.entries]
    assert ids[:3] == ["gemini_key_1", "gemini_key_2", "gemini_key_3"]


def test_pool_dedups_identical_keys(monkeypatch, tmp_path) -> None:
    cfg = _cfg(
        monkeypatch,
        tmp_path,
        gemini_api_key_1="AIzaSAMA",
        gemini_api_key_2="AIzaSAMA",
    )
    from rtrade.llm.pool_builder import build_scan_pool

    pool = build_scan_pool(cfg)
    assert [e.cred_id for e in pool.entries] == ["gemini_key_1"]


def test_pool_includes_xai_keys(monkeypatch, tmp_path) -> None:
    cfg = _cfg(
        monkeypatch,
        tmp_path,
        gemini_api_key_1="AIza1",
        xai_api_key_1="xai-test-1",
        xai_api_key_2="xai-test-2",
    )
    from rtrade.llm.pool_builder import build_scan_pool

    pool = build_scan_pool(cfg)
    ids = [e.cred_id for e in pool.entries]
    assert "xai_key_1" in ids
    assert "xai_key_2" in ids
    # Check flavor
    by_id = {e.cred_id: e for e in pool.entries}
    assert by_id["xai_key_1"].flavor == "xai"


def test_pool_includes_vertex_adc_accounts(monkeypatch, tmp_path) -> None:
    cfg = _cfg(monkeypatch, tmp_path, gemini_api_key_1="AIza1")
    cfg.settings.llm.vertex_project = "proj-x"
    from rtrade.llm.auth.vertex import adc_path_for

    adc_path_for("kerja").write_text("{}", encoding="utf-8")
    from rtrade.llm.pool_builder import build_scan_pool

    pool = build_scan_pool(cfg)
    ids = [e.cred_id for e in pool.entries]
    assert "vertex__kerja" in ids
    assert {e.flavor for e in pool.entries if e.cred_id == "vertex__kerja"} == {"vertex_ai"}


def test_pool_empty_raises_config_error(monkeypatch, tmp_path) -> None:
    # Clear ALL API key env vars so Secrets() has truly empty keys
    for name in (
        "GEMINI_API_KEY_1",
        "GEMINI_API_KEY_2",
        "GEMINI_API_KEY_3",
        "GEMINI_API_KEY_4",
        "GEMINI_API_KEY_5",
        "ANTHROPIC_API_KEY_1",
        "ANTHROPIC_API_KEY_2",
        "ANTHROPIC_API_KEY_3",
        "OPENAI_API_KEY_1",
        "OPENAI_API_KEY_2",
        "OPENAI_API_KEY_3",
        "XAI_API_KEY_1",
        "XAI_API_KEY_2",
        "XAI_API_KEY_3",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RTRADE_TOKEN_DIR", str(tmp_path / "tok"))
    monkeypatch.setenv("RTRADE_ADC_DIR", str(tmp_path / "adc"))
    from rtrade.core.config import AppConfig, Secrets

    cfg = AppConfig.load()
    # Override with Secrets that reads from non-existent .env
    object.__setattr__(cfg, "secrets", Secrets(_env_file=str(tmp_path / "empty.env")))
    from rtrade.llm.pool_builder import build_scan_pool

    with pytest.raises(ConfigError):
        build_scan_pool(cfg)


def test_flavor_mapping_codex_xai() -> None:
    from rtrade.llm.pool_builder import _FLAVOR_BY_PROVIDER_ID

    assert _FLAVOR_BY_PROVIDER_ID["codex_oauth"] == "openai"
    assert _FLAVOR_BY_PROVIDER_ID["xai_oauth"] == "xai"
    assert _FLAVOR_BY_PROVIDER_ID["xai_hermes"] == "xai"
