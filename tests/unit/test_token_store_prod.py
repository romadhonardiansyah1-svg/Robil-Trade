"""Token store prod-mode round-trip + fail-closed (C8)."""

from __future__ import annotations

import pytest

from rtrade.llm.auth.token_store import StoredToken, load_token, save_token


def _key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def test_prod_with_key_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RTRADE_TOKEN_DIR", str(tmp_path))
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("RTRADE_TOKEN_KEY", _key())
    tok = StoredToken(access_token="abc", refresh_token="r", expiry_epoch=1.0, scopes=["s"])
    save_token("codex_oauth__utama", tok)
    got = load_token("codex_oauth__utama")
    assert got is not None
    assert got.access_token == "abc"


def test_prod_without_key_fails_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RTRADE_TOKEN_DIR", str(tmp_path))
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.delenv("RTRADE_TOKEN_KEY", raising=False)
    tok = StoredToken(access_token="abc", refresh_token=None, expiry_epoch=1.0, scopes=[])
    with pytest.raises(RuntimeError, match="RTRADE_TOKEN_KEY wajib di prod"):
        save_token("codex_oauth", tok)
