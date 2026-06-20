"""Tests for the crypto Fear & Greed provider (PLAN P3-4).

Deterministic, no live network — uses respx with a recorded-shape fixture.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from rtrade.core.errors import RateLimitExceeded
from rtrade.data.fear_greed import (
    FearGreedProvider,
    fear_greed_risk_multiplier,
)

_FNG_URL = "https://api.alternative.me/fng/"

# Recorded-shape payload from https://api.alternative.me/fng/?limit=1
_PAYLOAD = {
    "name": "Fear and Greed Index",
    "data": [
        {
            "value": "40",
            "value_classification": "Fear",
            "timestamp": "1551157200",
            "time_until_update": "68499",
        }
    ],
    "metadata": {"error": None},
}


# ---------------------------------------------------------------------------
# Provider — HTTP behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_parses_payload() -> None:
    respx.get(_FNG_URL).mock(return_value=httpx.Response(200, json=_PAYLOAD))
    provider = FearGreedProvider()
    try:
        result = await provider.fetch_latest()
    finally:
        await provider.close()

    assert result is not None
    assert result.value == 40
    assert result.classification == "Fear"
    assert result.timestamp.tzinfo is not None
    assert result.timestamp.year == 2019


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_429_raises_rate_limit() -> None:
    respx.get(_FNG_URL).mock(return_value=httpx.Response(429))
    provider = FearGreedProvider()
    try:
        with pytest.raises(RateLimitExceeded):
            await provider.fetch_latest()
    finally:
        await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_empty_data_returns_none() -> None:
    respx.get(_FNG_URL).mock(return_value=httpx.Response(200, json={"data": []}))
    provider = FearGreedProvider()
    try:
        result = await provider.fetch_latest()
    finally:
        await provider.close()
    assert result is None


# ---------------------------------------------------------------------------
# Pure soft de-risk multiplier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [25, 40, 50, 60, 75])
def test_risk_multiplier_neutral_band_is_one(value: int) -> None:
    assert fear_greed_risk_multiplier(value) == 1.0


@pytest.mark.parametrize("value", [0, 10, 24, 76, 90, 100])
def test_risk_multiplier_extremes_reduce(value: int) -> None:
    m = fear_greed_risk_multiplier(value)
    assert m < 1.0
    assert m > 0.0


@pytest.mark.parametrize("value", list(range(-20, 121, 5)))
def test_risk_multiplier_never_above_one(value: int) -> None:
    m = fear_greed_risk_multiplier(value)
    assert 0.0 < m <= 1.0


def test_risk_multiplier_monotonic_at_extremes() -> None:
    # Deeper fear -> smaller multiplier.
    assert fear_greed_risk_multiplier(0) < fear_greed_risk_multiplier(10)
    assert fear_greed_risk_multiplier(10) < fear_greed_risk_multiplier(24)
    # Deeper greed -> smaller multiplier.
    assert fear_greed_risk_multiplier(100) < fear_greed_risk_multiplier(90)
    assert fear_greed_risk_multiplier(90) < fear_greed_risk_multiplier(76)
