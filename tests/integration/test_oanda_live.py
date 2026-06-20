from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os

import pytest

from rtrade.core.constants import Timeframe
from rtrade.data.oanda_provider import OandaProvider
from rtrade.data.ratelimit import OANDA_BUCKET, RateLimiter
from rtrade.persistence.db import _get_redis

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_oanda_fetches_xau_usd_m5() -> None:
    token = os.environ.get("OANDA_TOKEN_1", "")
    account = os.environ.get("OANDA_ACCOUNT_1", "")
    if not token or not account:
        pytest.skip("OANDA_TOKEN_1/OANDA_ACCOUNT_1 not set — live OANDA test skipped")
    limiter = RateLimiter(
        _get_redis(os.environ.get("RTRADE_TEST_REDIS_URL", "redis://localhost:6379/0"))
    )
    provider = OandaProvider(token, account, limiter, bucket=OANDA_BUCKET, practice=True)
    try:
        since = datetime.now(UTC) - timedelta(days=2)
        candles = await provider.fetch_ohlcv("XAU_USD", Timeframe.M5, since, limit=100)
        assert len(candles) > 0
        assert all(c.high >= c.low for c in candles)
    finally:
        await provider.close()
