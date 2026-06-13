"""Tests for VertexProvider (O4)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from rtrade.llm.auth.vertex import VertexProvider, has_adc


class TestVertexProvider:
    @pytest.mark.asyncio
    async def test_resolve_returns_vertex_material(self) -> None:
        prov = VertexProvider(project="proj", location="us")
        mat = await prov.resolve()
        assert mat.auth_type == "vertex"
        assert mat.provider_id == "google_vertex"
        assert mat.extra_kwargs["vertex_project"] == "proj"
        assert mat.extra_kwargs["vertex_location"] == "us"

    def test_mode(self) -> None:
        prov = VertexProvider(project="p")
        assert prov.mode == "vertex"


class TestHasADC:
    def test_has_adc_with_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/path/to/sa.json")
        assert has_adc() is True

    def test_has_adc_without_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        with patch("google.auth.default", side_effect=Exception("no creds")):
            assert has_adc() is False


class TestVertexCredentialsPath:
    """A4: credentials_path for multi-account ADC."""

    @pytest.mark.asyncio
    async def test_credentials_path_in_extra_kwargs(self) -> None:
        prov = VertexProvider(project="proj-x", credentials_path="/tmp/google__acc1.json")
        material = await prov.resolve()
        assert material.extra_kwargs["vertex_credentials"] == "/tmp/google__acc1.json"
        assert material.extra_kwargs["vertex_project"] == "proj-x"

    @pytest.mark.asyncio
    async def test_no_credentials_path_omits_key(self) -> None:
        prov = VertexProvider(project="proj-x")
        material = await prov.resolve()
        assert "vertex_credentials" not in material.extra_kwargs

    def test_adc_account_helpers(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("RTRADE_ADC_DIR", str(tmp_path))
        from rtrade.llm.auth.vertex import adc_path_for, list_adc_accounts

        assert list_adc_accounts() == []
        adc_path_for("kerja").write_text("{}", encoding="utf-8")
        adc_path_for("pribadi").write_text("{}", encoding="utf-8")
        assert list_adc_accounts() == ["kerja", "pribadi"]
