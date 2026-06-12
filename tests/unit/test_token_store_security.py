"""Tests for S3: token store fail-closed in prod + key rotation."""

from __future__ import annotations

from pathlib import Path

import pytest

from rtrade.llm.auth.token_store import StoredToken, load_token, rotate_key, save_token


@pytest.fixture()
def _token_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RTRADE_TOKEN_DIR", str(tmp_path))
    monkeypatch.delenv("RTRADE_TOKEN_KEY", raising=False)


class TestProdFailClosed:
    @pytest.mark.usefixtures("_token_env")
    def test_save_raises_in_prod_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV", "prod")
        tok = StoredToken("t", None, 9999999999.0, [])
        with pytest.raises(RuntimeError, match="wajib di prod"):
            save_token("test_prod", tok)

    @pytest.mark.usefixtures("_token_env")
    def test_save_ok_in_prod_with_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        monkeypatch.setenv("ENV", "prod")
        monkeypatch.setenv("RTRADE_TOKEN_KEY", key)
        tok = StoredToken("t", None, 9999999999.0, [])
        save_token("test_prod_ok", tok)
        loaded = load_token("test_prod_ok")
        assert loaded is not None
        assert loaded.access_token == "t"

    @pytest.mark.usefixtures("_token_env")
    def test_save_ok_in_dev_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV", "dev")
        tok = StoredToken("dev_tok", None, 9999999999.0, [])
        save_token("test_dev", tok)
        loaded = load_token("test_dev")
        assert loaded is not None


class TestKeyRotation:
    @pytest.mark.usefixtures("_token_env")
    def test_rotate_plaintext_to_encrypted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from cryptography.fernet import Fernet

        # Save plaintext
        tok = StoredToken("rot_tok", None, 9999999999.0, ["s"])
        save_token("rot_test", tok)

        new_key = Fernet.generate_key().decode()
        count = rotate_key("", new_key)
        assert count == 1

        # Now load with new key
        monkeypatch.setenv("RTRADE_TOKEN_KEY", new_key)
        loaded = load_token("rot_test")
        assert loaded is not None
        assert loaded.access_token == "rot_tok"

    @pytest.mark.usefixtures("_token_env")
    def test_rotate_encrypted_to_new_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from cryptography.fernet import Fernet

        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()

        monkeypatch.setenv("RTRADE_TOKEN_KEY", old_key)
        tok = StoredToken("enc_tok", None, 9999999999.0, [])
        save_token("enc_test", tok)

        monkeypatch.delenv("RTRADE_TOKEN_KEY")
        count = rotate_key(old_key, new_key)
        assert count == 1

        monkeypatch.setenv("RTRADE_TOKEN_KEY", new_key)
        loaded = load_token("enc_test")
        assert loaded is not None
        assert loaded.access_token == "enc_tok"
