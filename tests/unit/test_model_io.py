"""Tests for keyed-HMAC integrity-verified model IO (S13 / C3).

The integrity sidecar is a keyed HMAC-SHA256 (`.hmac`), NOT a plain hash. The
secret key never lives beside the model, so an attacker who can overwrite the
model file cannot forge a valid sidecar. Controls fail CLOSED: no key, or no
sidecar, means refuse to load.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from rtrade.ml.model_io import load_model, save_model

# Fixed test key — explicit-arg path keeps tests independent of env/config.
_KEY = "test-model-hmac-key-0123456789abcdef"


class TestModelIO:
    def test_save_load_round_trip(self, tmp_path: Path) -> None:
        """Save with a key then load with the same key returns the object."""
        model = {"type": "test_model", "weights": [1, 2, 3]}
        path = tmp_path / "test.joblib"
        save_model(model, path, hmac_key=_KEY)
        loaded = load_model(path, hmac_key=_KEY)

        assert loaded == model

    def test_hmac_sidecar_created_not_sha256(self, tmp_path: Path) -> None:
        """Sidecar is `.hmac`; the old unkeyed `.sha256` path is gone."""
        path = tmp_path / "m.joblib"
        save_model({"x": 1}, path, hmac_key=_KEY)

        assert path.with_suffix(".joblib.hmac").exists()
        assert not path.with_suffix(".joblib.sha256").exists()

    def test_save_without_key_refuses(self, tmp_path: Path) -> None:
        """No key on save → refuse (no untrustworthy plain-hash sidecar)."""
        path = tmp_path / "nokey.joblib"
        with pytest.raises(RuntimeError, match="MODEL_HMAC_KEY"):
            save_model({"x": 1}, path, hmac_key="")

    def test_load_without_key_refuses(self, tmp_path: Path) -> None:
        """No key on load → fail closed."""
        path = tmp_path / "saved.joblib"
        save_model({"x": 1}, path, hmac_key=_KEY)
        with pytest.raises(RuntimeError, match="MODEL_HMAC_KEY"):
            load_model(path, hmac_key="")

    def test_load_without_sidecar_refuses(self, tmp_path: Path) -> None:
        """Model with no `.hmac` sidecar → fail closed (e.g. legacy .sha256 model)."""
        import joblib

        path = tmp_path / "no_side.joblib"
        joblib.dump({"x": 1}, path)
        with pytest.raises(RuntimeError, match="tanpa sidecar HMAC"):
            load_model(path, hmac_key=_KEY)

    def test_tampered_model_rejected(self, tmp_path: Path) -> None:
        """Overwriting model bytes only (stale sidecar) → integrity failure."""
        path = tmp_path / "tampered.joblib"
        save_model({"original": True}, path, hmac_key=_KEY)

        data = path.read_bytes()
        path.write_bytes(data + b"TAMPERED")

        with pytest.raises(RuntimeError, match=r"integritas.*GAGAL"):
            load_model(path, hmac_key=_KEY)

    def test_threat_attacker_rewrites_model_and_sidecar(self, tmp_path: Path) -> None:
        """C3 core proof: attacker overwrites model AND rewrites the sidecar with a
        freshly-computed PLAIN sha256 of the malicious file. Because the sidecar
        scheme is a *keyed* HMAC and the attacker lacks the key, the plain hash can
        never equal HMAC(K, file) → load with the real key RAISES.
        """
        path = tmp_path / "evil.joblib"
        save_model({"benign": True}, path, hmac_key=_KEY)

        # Attacker replaces the model with a malicious payload...
        import joblib

        joblib.dump({"malicious": "rce"}, path)
        # ...and recomputes a PLAIN sha256 sidecar (what the old scheme accepted).
        plain = hashlib.sha256(path.read_bytes()).hexdigest()
        path.with_suffix(".joblib.hmac").write_text(plain, encoding="utf-8")

        with pytest.raises(RuntimeError, match=r"integritas.*GAGAL"):
            load_model(path, hmac_key=_KEY)

    def test_tampered_sidecar_rejected(self, tmp_path: Path) -> None:
        """Garbage written into the sidecar → integrity failure."""
        path = tmp_path / "tampered_side.joblib"
        save_model({"original": True}, path, hmac_key=_KEY)

        sidecar = path.with_suffix(".joblib.hmac")
        sidecar.write_text("0" * 64, encoding="utf-8")

        with pytest.raises(RuntimeError, match=r"integritas.*GAGAL"):
            load_model(path, hmac_key=_KEY)
