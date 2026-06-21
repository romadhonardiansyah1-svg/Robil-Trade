"""rtrade setup wizard: pure helpers + interactive multi-provider/model picker.

No network, no real OAuth. `builtins.input` is monkeypatched with a scripted
list; `perform_login`/`build_scan_pool` are monkeypatched where touched. Secret
VALUES are never asserted via logs — only that the right env KEYS were written.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from rtrade.core.errors import ConfigError


# --------------------------------------------------------------------------- #
# upsert_env_var
# --------------------------------------------------------------------------- #
def test_upsert_env_var_creates_file(tmp_path: Path) -> None:
    from rtrade.cli.setup import upsert_env_var

    env = tmp_path / ".env"
    upsert_env_var(env, "GEMINI_API_KEY_1", "value-1")
    assert env.exists()
    assert "GEMINI_API_KEY_1=value-1" in env.read_text(encoding="utf-8").splitlines()


def test_upsert_env_var_updates_existing_in_place(tmp_path: Path) -> None:
    from rtrade.cli.setup import upsert_env_var

    env = tmp_path / ".env"
    env.write_text("FIRST=keep\nGEMINI_API_KEY_1=old\nLAST=keep-too\n", encoding="utf-8")
    upsert_env_var(env, "GEMINI_API_KEY_1", "new-value")
    lines = env.read_text(encoding="utf-8").splitlines()
    assert lines == ["FIRST=keep", "GEMINI_API_KEY_1=new-value", "LAST=keep-too"]


def test_upsert_env_var_appends_new_key(tmp_path: Path) -> None:
    from rtrade.cli.setup import upsert_env_var

    env = tmp_path / ".env"
    env.write_text("EXISTING=here\n", encoding="utf-8")
    upsert_env_var(env, "OPENAI_API_KEY_1", "added")
    lines = env.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "EXISTING=here"
    assert "OPENAI_API_KEY_1=added" in lines


# --------------------------------------------------------------------------- #
# auth_mode_for
# --------------------------------------------------------------------------- #
def test_auth_mode_for_mapping() -> None:
    from rtrade.cli.setup import auth_mode_for

    assert auth_mode_for("api_key") == "api_key"
    assert auth_mode_for("oauth_vertex") == "vertex"
    assert auth_mode_for("oauth_device") == "cli_oauth"
    assert auth_mode_for("oauth_pkce") == "cli_oauth"


def _script_input(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> None:
    it: Iterator[str] = iter(answers)

    def fake_input(prompt: str = "") -> str:
        return next(it)

    monkeypatch.setattr("builtins.input", fake_input)


# --------------------------------------------------------------------------- #
# wizard — Gemini api_key happy path → analyst route
# --------------------------------------------------------------------------- #
def test_wizard_gemini_two_keys_and_analyst_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    settings = tmp_path / "settings.yaml"

    # 1=Gemini, 1=model gemini-2.5-pro, two keys then blank, role analyst, 0=Selesai
    _script_input(
        monkeypatch,
        [
            "1",  # provider: Gemini
            "1",  # model: suggested #1
            "AIza-key-one",  # key 1
            "AIza-key-two",  # key 2
            "",  # blank → stop keys
            "analyst",  # role
            "0",  # Selesai
        ],
    )

    from rtrade.cli.setup import wizard

    rc = wizard(env, settings)
    assert rc == 0

    env_lines = env.read_text(encoding="utf-8").splitlines()
    assert "GEMINI_API_KEY_1=AIza-key-one" in env_lines
    assert "GEMINI_API_KEY_2=AIza-key-two" in env_lines

    doc = yaml.safe_load(settings.read_text(encoding="utf-8"))
    route = doc["llm"]["model_routes"]["analyst"]
    assert route["model"] == "gemini/gemini-2.5-pro"
    profile_name = route["auth_profile"]
    assert doc["llm"]["auth_profiles"][profile_name]["auth_type"] == "api_key"
    assert "gemini" in profile_name


# --------------------------------------------------------------------------- #
# wizard — rejects consumer OAuth token (sk-ant-oat...)
# --------------------------------------------------------------------------- #
def test_wizard_rejects_sk_ant_oat_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    settings = tmp_path / "settings.yaml"

    # 2=Anthropic, model #1, bad oat key (rejected), good key, blank, skip role, 0
    _script_input(
        monkeypatch,
        [
            "2",  # provider: Anthropic
            "1",  # model #1
            "sk-ant-oat01-forbidden",  # rejected
            "sk-ant-api03-good",  # accepted
            "",  # blank → stop keys
            "skip",  # role: skip
            "0",  # Selesai
        ],
    )

    from rtrade.cli.setup import wizard

    rc = wizard(env, settings)
    assert rc == 0

    text = env.read_text(encoding="utf-8")
    assert "sk-ant-oat01-forbidden" not in text
    assert "ANTHROPIC_API_KEY_1=sk-ant-api03-good" in text.splitlines()


# --------------------------------------------------------------------------- #
# _collect_api_keys — per-provider slot cap (real Secrets slot count)
# --------------------------------------------------------------------------- #
def test_collect_api_keys_caps_non_gemini_at_three(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rtrade.cli.setup import PROVIDER_CATALOG, _collect_api_keys

    env = tmp_path / ".env"
    entry = next(e for e in PROVIDER_CATALOG if e.flavor == "anthropic" and e.kind == "api_key")

    # Script 4 keys + blank: the cap (3) must stop acceptance before slot 4.
    _script_input(monkeypatch, ["k-one", "k-two", "k-three", "k-four", ""])
    n = _collect_api_keys(entry, env)

    assert n == 3
    lines = env.read_text(encoding="utf-8").splitlines()
    assert "ANTHROPIC_API_KEY_1=k-one" in lines
    assert "ANTHROPIC_API_KEY_2=k-two" in lines
    assert "ANTHROPIC_API_KEY_3=k-three" in lines
    assert not any(line.startswith("ANTHROPIC_API_KEY_4") for line in lines)


def test_collect_api_keys_allows_five_for_gemini(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rtrade.cli.setup import PROVIDER_CATALOG, _collect_api_keys

    env = tmp_path / ".env"
    entry = next(e for e in PROVIDER_CATALOG if e.flavor == "gemini" and e.kind == "api_key")

    _script_input(monkeypatch, ["g1", "g2", "g3", "g4", "g5"])
    n = _collect_api_keys(entry, env)

    assert n == 5
    lines = env.read_text(encoding="utf-8").splitlines()
    assert "GEMINI_API_KEY_1=g1" in lines
    assert "GEMINI_API_KEY_5=g5" in lines


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #
class _FakePool:
    def __init__(self, entries: list[object]) -> None:
        self.entries = entries


class _FakeEntry:
    def __init__(self, cred_id: str, flavor: str, mode: str) -> None:
        self.cred_id = cred_id
        self.flavor = flavor

        class _Cred:
            pass

        cred = _Cred()
        cred.mode = mode  # type: ignore[attr-defined]
        self.credential = cred


def test_verify_non_empty_returns_0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import rtrade.cli.setup as setup

    monkeypatch.setattr(setup.AppConfig, "load", classmethod(lambda cls, **kw: object()))
    monkeypatch.setattr(
        setup,
        "build_scan_pool",
        lambda cfg, **kw: _FakePool([_FakeEntry("k", "gemini", "api_key")]),
    )
    assert setup.verify(tmp_path / "settings.yaml") == 0


def test_verify_empty_returns_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import rtrade.cli.setup as setup

    monkeypatch.setattr(setup.AppConfig, "load", classmethod(lambda cls, **kw: object()))
    monkeypatch.setattr(setup, "build_scan_pool", lambda cfg, **kw: _FakePool([]))
    assert setup.verify(tmp_path / "settings.yaml") == 1


def test_verify_config_error_returns_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import rtrade.cli.setup as setup

    def _boom(cfg: object, **kw: object) -> object:
        raise ConfigError("pool kosong")

    monkeypatch.setattr(setup.AppConfig, "load", classmethod(lambda cls, **kw: object()))
    monkeypatch.setattr(setup, "build_scan_pool", _boom)
    assert setup.verify(tmp_path / "settings.yaml") == 1
