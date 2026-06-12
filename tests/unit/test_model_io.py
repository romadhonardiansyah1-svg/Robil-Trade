"""Tests for integrity-verified model IO (S13)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rtrade.ml.model_io import load_model, save_model


class TestModelIO:
    def test_save_load_round_trip(self, tmp_path: Path) -> None:
        """Save then load succeeds."""
        model = {"type": "test_model", "weights": [1, 2, 3]}
        path = tmp_path / "test.joblib"
        save_model(model, path)
        loaded = load_model(path)
        assert loaded == model

    def test_sidecar_created(self, tmp_path: Path) -> None:
        """Sidecar .sha256 file is created alongside model."""
        path = tmp_path / "m.joblib"
        save_model({"x": 1}, path)
        assert path.with_suffix(".joblib.sha256").exists()

    def test_load_without_sidecar_raises(self, tmp_path: Path) -> None:
        """Loading model without sidecar → RuntimeError."""
        import joblib

        path = tmp_path / "no_side.joblib"
        joblib.dump({"x": 1}, path)
        with pytest.raises(RuntimeError, match="tanpa sidecar"):
            load_model(path)

    def test_tampered_model_rejected(self, tmp_path: Path) -> None:
        """Tampering model file after save → integrity failure."""
        path = tmp_path / "tampered.joblib"
        save_model({"original": True}, path)

        # Tamper with the file
        data = path.read_bytes()
        path.write_bytes(data + b"TAMPERED")

        with pytest.raises(RuntimeError, match=r"integritas.*GAGAL"):
            load_model(path)

    def test_tampered_sidecar_rejected(self, tmp_path: Path) -> None:
        """Tampering sidecar → integrity failure."""
        path = tmp_path / "tampered_side.joblib"
        save_model({"original": True}, path)

        sidecar = path.with_suffix(".joblib.sha256")
        sidecar.write_text("0" * 64, encoding="utf-8")

        with pytest.raises(RuntimeError, match=r"integritas.*GAGAL"):
            load_model(path)
