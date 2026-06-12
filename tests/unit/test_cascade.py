"""T17: Cascade model escalation tests."""

from __future__ import annotations

import pytest

from rtrade.llm.cascade import _extract_confidence


class TestExtractConfidence:
    def test_json_format(self) -> None:
        content = '{"verdict": "CONFIRM", "confidence": 0.72, "reason": "ok"}'
        assert _extract_confidence(content) == pytest.approx(0.72)

    def test_plain_text(self) -> None:
        content = "confidence: 0.58"
        assert _extract_confidence(content) == pytest.approx(0.58)

    def test_no_confidence(self) -> None:
        content = "just some text without any number"
        assert _extract_confidence(content) is None

    def test_in_doubt_band(self) -> None:
        from rtrade.llm.cascade import DOUBT_HIGH, DOUBT_LOW

        content = '{"confidence": 0.55}'
        val = _extract_confidence(content)
        assert val is not None
        assert DOUBT_LOW <= val <= DOUBT_HIGH
