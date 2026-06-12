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
