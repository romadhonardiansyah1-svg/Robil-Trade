"""Tests for encrypted token store (O2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rtrade.llm.auth.token_store import StoredToken, delete_token, load_token, save_token


@pytest.fixture()
def _token_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RTRADE_TOKEN_DIR", str(tmp_path))


@pytest.fixture()
def _token_env_encrypted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    monkeypatch.setenv("RTRADE_TOKEN_DIR", str(tmp_path))
    monkeypatch.setenv("RTRADE_TOKEN_KEY", Fernet.generate_key().decode())


class TestTokenStoreEncrypted:
    @pytest.mark.usefixtures("_token_env_encrypted")
    def test_save_load_roundtrip(self) -> None:
        tok = StoredToken(
            access_token="acc",
            refresh_token="ref",
            expiry_epoch=9999999.0,
            scopes=["scope1"],
        )
        save_token("test_prov", tok)
        loaded = load_token("test_prov")
        assert loaded is not None
        assert loaded.access_token == "acc"
        assert loaded.refresh_token == "ref"
        assert loaded.expiry_epoch == 9999999.0
        assert loaded.scopes == ["scope1"]

    @pytest.mark.usefixtures("_token_env_encrypted")
    def test_file_is_encrypted(self, tmp_path: Path) -> None:
        tok = StoredToken("acc", "ref", 0.0, [])
        save_token("enc_test", tok)
        raw = (tmp_path / "enc_test.json").read_bytes()
        # Encrypted data is not valid JSON
        with pytest.raises(json.JSONDecodeError):
            json.loads(raw)


class TestTokenStorePlaintext:
    @pytest.mark.usefixtures("_token_env")
    def test_plaintext_roundtrip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RTRADE_TOKEN_KEY", raising=False)
        tok = StoredToken("acc_plain", None, 1000.0, ["s1", "s2"])
        save_token("plain_prov", tok)
        loaded = load_token("plain_prov")
        assert loaded is not None
        assert loaded.access_token == "acc_plain"
        assert loaded.refresh_token is None

    @pytest.mark.usefixtures("_token_env")
    def test_plaintext_is_readable_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("RTRADE_TOKEN_KEY", raising=False)
        tok = StoredToken("acc", "ref", 0.0, [])
        save_token("json_test", tok)
        raw = (tmp_path / "json_test.json").read_bytes()
        d = json.loads(raw)
        assert d["access_token"] == "acc"


class TestTokenNotFound:
    @pytest.mark.usefixtures("_token_env")
    def test_load_nonexistent(self) -> None:
        assert load_token("nonexistent") is None


class TestDeleteToken:
    @pytest.mark.usefixtures("_token_env")
    def test_delete_existing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RTRADE_TOKEN_KEY", raising=False)
        tok = StoredToken("acc", None, 0.0, [])
        save_token("del_prov", tok)
        assert delete_token("del_prov") is True
        assert load_token("del_prov") is None

    @pytest.mark.usefixtures("_token_env")
    def test_delete_nonexistent(self) -> None:
        assert delete_token("nonexistent") is False


class TestMultiAccountHelpers:
    """A2: token store multi-account helpers."""

    def test_account_store_id_default_and_named(self) -> None:
        from rtrade.llm.auth.token_store import account_store_id

        assert account_store_id("generic_gateway") == "generic_gateway"
        assert account_store_id("generic_gateway", "default") == "generic_gateway"
        assert account_store_id("generic_gateway", "acc2") == "generic_gateway__acc2"

    def test_account_store_id_rejects_path_tricks(self) -> None:
        from rtrade.llm.auth.token_store import account_store_id

        for bad in ("../evil", "a b", "UPPER", "x" * 33, ""):
            with pytest.raises(ValueError):
                account_store_id("p", bad)

    @pytest.mark.usefixtures("_token_env")
    def test_list_accounts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RTRADE_TOKEN_KEY", raising=False)
        from rtrade.llm.auth.token_store import list_accounts

        tok = StoredToken(access_token="a", refresh_token=None, expiry_epoch=1.0, scopes=[])
        save_token("gw", tok)
        save_token("gw__kerja", tok)
        save_token("gw__pribadi", tok)
        save_token("lain__x", tok)
        assert list_accounts("gw") == ["default", "kerja", "pribadi"]
        assert list_accounts("lain") == ["x"]
        assert list_accounts("kosong") == []
