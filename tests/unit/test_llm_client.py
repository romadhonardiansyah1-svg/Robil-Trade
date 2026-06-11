"""Unit tests for LLM client (PLAN 8.9.1)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, Field

from rtrade.core.errors import LLMOutputError, LLMUnavailableError
from rtrade.llm.client import LLMClient, _validate_json


class _TestSchema(BaseModel):
    """Simple schema for testing."""

    verdict: str = Field(pattern=r"^(CONFIRM|VETO|ABSTAIN)$")
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class TestValidateJson:
    def test_valid_json(self) -> None:
        content = '{"verdict": "CONFIRM", "confidence": 0.7, "reason": "test"}'
        result = _validate_json(content, _TestSchema)
        assert isinstance(result, _TestSchema)
        assert result.verdict == "CONFIRM"
        assert result.confidence == 0.7

    def test_json_with_code_fences(self) -> None:
        content = '```json\n{"verdict": "VETO", "confidence": 0.3, "reason": "bad"}\n```'
        result = _validate_json(content, _TestSchema)
        assert result.verdict == "VETO"

    def test_invalid_json(self) -> None:
        with pytest.raises(ValueError, match="invalid JSON"):
            _validate_json("not json at all", _TestSchema)

    def test_schema_validation_fail(self) -> None:
        # confidence out of range.
        with pytest.raises((ValueError, Exception)):
            _validate_json(
                '{"verdict": "CONFIRM", "confidence": 2.0, "reason": "x"}',
                _TestSchema,
            )


class TestLLMClient:
    @pytest.mark.asyncio
    async def test_complete_success(self) -> None:
        """Successful LLM call with valid response."""
        client = LLMClient(api_key="test-key")

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"verdict":"CONFIRM","confidence":0.8,"reason":"good setup"}'
                )
            )
        ]
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 100
        mock_usage.completion_tokens = 50
        mock_usage.total_tokens = 150
        mock_response.usage = mock_usage

        with patch("rtrade.llm.client.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)
            mock_litellm.suppress_debug_info = True

            result = await client.complete(
                model="gemini/gemini-3.1-flash-lite",
                system_prompt="test system",
                user_prompt="test user",
                response_schema=_TestSchema,
            )

        assert result.total_tokens == 150
        assert "CONFIRM" in result.content

    @pytest.mark.asyncio
    async def test_retry_on_invalid_json(self) -> None:
        """Client retries once on invalid JSON."""
        client = LLMClient(api_key="test-key", max_retries=1)

        # First call returns invalid JSON, second returns valid.
        bad_response = MagicMock()
        bad_response.choices = [MagicMock(message=MagicMock(content="not json"))]
        bad_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        good_response = MagicMock()
        good_response.choices = [
            MagicMock(
                message=MagicMock(content='{"verdict":"CONFIRM","confidence":0.7,"reason":"ok"}')
            )
        ]
        good_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        with patch("rtrade.llm.client.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(side_effect=[bad_response, good_response])
            mock_litellm.suppress_debug_info = True

            result = await client.complete(
                model="test-model",
                system_prompt="test",
                user_prompt="test",
                response_schema=_TestSchema,
            )

        assert "CONFIRM" in result.content

    @pytest.mark.asyncio
    async def test_all_retries_fail(self) -> None:
        """All retries fail -> LLMOutputError."""
        client = LLMClient(api_key="test-key", max_retries=1)

        bad_response = MagicMock()
        bad_response.choices = [MagicMock(message=MagicMock(content="bad json"))]
        bad_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        with patch("rtrade.llm.client.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=bad_response)
            mock_litellm.suppress_debug_info = True

            with pytest.raises(LLMOutputError):
                await client.complete(
                    model="test-model",
                    system_prompt="test",
                    user_prompt="test",
                    response_schema=_TestSchema,
                )

    @pytest.mark.asyncio
    async def test_provider_failure(self) -> None:
        """Provider failure -> LLMUnavailableError."""
        client = LLMClient(api_key="test-key", max_retries=0)

        with patch("rtrade.llm.client.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(side_effect=ConnectionError("provider down"))
            mock_litellm.suppress_debug_info = True

            with pytest.raises(LLMUnavailableError):
                await client.complete(
                    model="test-model",
                    system_prompt="test",
                    user_prompt="test",
                )

    def test_stats_tracking(self) -> None:
        """Stats should be initialized correctly."""
        client = LLMClient()
        stats = client.stats
        assert stats["call_count"] == 0
        assert stats["total_cost_usd"] == 0
        assert stats["total_tokens"] == 0
