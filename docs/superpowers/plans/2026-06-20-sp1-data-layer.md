# SP-1: Multi-Account + Fallback Data Layer (OANDA) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-key TwelveData market-data path with OANDA primary + multi-account + composite fallback, so rate limits never stall the engine, while staying signal-only and fully type/lint/test clean.

**Architecture:** Add `OandaProvider` (REST v20, mirrors `TwelveDataProvider`) and `CompositeMarketDataProvider` (ordered failover + per-leg health + alerts, mirrors `CompositeCalendarProvider`). Each vendor account/key is one "leg" with its own Redis token bucket. The provider factory `_make_market_provider` composes legs from config. This is a drop-in behind the existing `MarketDataProvider` ABC — no caller changes.

**Tech Stack:** Python 3.12 async, httpx.AsyncClient, structlog, tenacity, pydantic-settings, Redis token-bucket rate limiter, respx (HTTP test mocks), pytest.

## Global Constraints

- Signal-only — no order/broker placement.
- Hard risk floors untouched (GR-03 rr_min≥1.5, GR-04 sl_atr∈[0.5,3.0], GR-05 risk≤2.0) — not in scope here but must not be disturbed.
- `llm.enabled` stays false; no `model_construct` on production path.
- Determinism in tests: use `respx` for OANDA HTTP; no live network. Integration tests skip when the live endpoint/stack is unreachable (TCP/connection-error → `pytest.skip`).
- Toolchain via venv. Per-phase gate: `.venv\Scripts\python.exe -m ruff check src tests migrations` ; `ruff format src tests migrations` ; `mypy --strict src` ; `pytest tests -q`.
- Commit via `COMMIT_MSG_TMP.txt` + `git commit -F COMMIT_MSG_TMP.txt`, then delete it. Before commit run `cmd /c 'if exist nul del "\\?\%CD%\nul"'`. No push unless asked.
- Secrets never logged by value; reference by slot name.
- Domain `Candle` (`rtrade/data/base.py`) is frozen+validated (positive finite OHLC, high≥open/close, low≤open/close). Build candles through it so invalid rows raise.
- Follow existing file conventions (structlog logger, `from __future__ import annotations`, `Decimal` for prices, `ensure_utc` for timestamps).

---

## File Structure

- Create: `src/rtrade/data/oanda_provider.py` — OANDA v20 REST `MarketDataProvider` (one account/token per instance).
- Create: `src/rtrade/data/composite_market.py` — `CompositeMarketDataProvider` failover wrapper.
- Modify: `src/rtrade/core/config.py` — `Secrets`: add OANDA + multi-key TwelveData fields + `market_keys_for()`.
- Modify: `src/rtrade/data/ratelimit.py` — add OANDA buckets + per-account bucket factory.
- Modify: `src/rtrade/pipeline/scan.py:1256-1266` — rebuild `_make_market_provider` to compose legs.
- Modify: `config/instruments.yaml` — XAUUSD `provider: oanda`, `provider_symbol: "XAU_USD"` (timeframes unchanged here; M5/M15 enabled in SP-2).
- Modify: `.env.example` — document new env vars.
- Test: `tests/unit/test_oanda_provider.py`, `tests/unit/test_composite_market.py`, `tests/unit/test_secrets_market_keys.py`, `tests/unit/test_make_market_provider.py`, `tests/integration/test_oanda_live.py`.

---

## Task 1: Secrets — OANDA + multi-key TwelveData fields + `market_keys_for()`

**Files:**
- Modify: `src/rtrade/core/config.py` (the `Secrets` class)
- Test: `tests/unit/test_secrets_market_keys.py`

**Interfaces:**
- Consumes: existing `Secrets(BaseSettings)` with `twelvedata_api_key`, `keys_for(family)`.
- Produces:
  - New fields: `oanda_token_1/2/3: str=""`, `oanda_account_1/2/3: str=""`, `oanda_env: Literal["practice","live"]="practice"`, `twelvedata_api_key_2/3: str=""`.
  - `Secrets.market_keys_for(provider: str) -> list[tuple[str, str | None]]` — returns `[(token, account_or_None), ...]` for non-empty slots, in order. For `"oanda"`: `(token_i, account_i)`. For `"twelvedata"`: `(key_i, None)` including legacy `twelvedata_api_key` as slot 1.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_secrets_market_keys.py
from __future__ import annotations

from rtrade.core.config import Secrets


def _secrets(**over: str) -> Secrets:
    # _env_file=None: ignore the on-disk .env so the test is deterministic.
    return Secrets(_env_file=None, **over)  # type: ignore[call-arg]


def test_oanda_keys_pair_token_and_account_in_order() -> None:
    s = _secrets(
        oanda_token_1="t1", oanda_account_1="a1",
        oanda_token_2="t2", oanda_account_2="a2",
    )
    assert s.market_keys_for("oanda") == [("t1", "a1"), ("t2", "a2")]


def test_oanda_skips_empty_slots() -> None:
    s = _secrets(oanda_token_1="t1", oanda_account_1="a1", oanda_token_3="t3", oanda_account_3="a3")
    assert s.market_keys_for("oanda") == [("t1", "a1"), ("t3", "a3")]


def test_twelvedata_includes_legacy_key_first() -> None:
    s = _secrets(twelvedata_api_key="legacy", twelvedata_api_key_2="k2")
    assert s.market_keys_for("twelvedata") == [("legacy", None), ("k2", None)]


def test_unknown_provider_returns_empty() -> None:
    assert _secrets().market_keys_for("nope") == []


def test_oanda_env_default_is_practice() -> None:
    assert _secrets().oanda_env == "practice"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_secrets_market_keys.py -q`
Expected: FAIL — `AttributeError: 'Secrets' object has no attribute 'market_keys_for'` (and unknown field errors).

- [ ] **Step 3: Add fields + method to `Secrets`**

In `src/rtrade/core/config.py`, inside `class Secrets(BaseSettings)`, add fields after `twelvedata_api_key: str = ""`:

```python
    twelvedata_api_key_2: str = ""
    twelvedata_api_key_3: str = ""

    oanda_token_1: str = ""
    oanda_token_2: str = ""
    oanda_token_3: str = ""
    oanda_account_1: str = ""
    oanda_account_2: str = ""
    oanda_account_3: str = ""
    oanda_env: Literal["practice", "live"] = "practice"
```

Add this method to `Secrets` (next to `keys_for`):

```python
    def market_keys_for(self, provider: str) -> list[tuple[str, str | None]]:
        """Market-data credential legs for a provider, ordered, empty slots dropped.

        Returns (token, account_or_None) pairs. Mirrors keys_for() for LLM keys.
        """
        if provider == "oanda":
            pairs = [
                (self.oanda_token_1, self.oanda_account_1),
                (self.oanda_token_2, self.oanda_account_2),
                (self.oanda_token_3, self.oanda_account_3),
            ]
            return [(t, a or None) for t, a in pairs if t]
        if provider == "twelvedata":
            keys = [self.twelvedata_api_key, self.twelvedata_api_key_2, self.twelvedata_api_key_3]
            return [(k, None) for k in keys if k]
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_secrets_market_keys.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Gate + commit**

```bash
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src
```
Expected: ruff clean, mypy `Success`.

```
# write COMMIT_MSG_TMP.txt then:
git add src/rtrade/core/config.py tests/unit/test_secrets_market_keys.py
git commit -F COMMIT_MSG_TMP.txt
```
Message: `feat(sp1): Secrets OANDA + multi-key TwelveData slots + market_keys_for()`

---

## Task 2: Rate buckets — OANDA + per-account bucket factory

**Files:**
- Modify: `src/rtrade/data/ratelimit.py`
- Test: `tests/unit/test_ratelimit_buckets.py`

**Interfaces:**
- Consumes: `BucketConfig.per_minute(name, rpm)`, existing `TWELVEDATA_BUCKET`.
- Produces:
  - `OANDA_BUCKET = BucketConfig.per_minute("oanda", 6000)` (100/s).
  - `def market_bucket(provider: str, index: int) -> BucketConfig` — per-account/key bucket, e.g. `oanda` idx 2 → name `"oanda_acc2"`, `twelvedata` idx 1 → `"twelvedata_k1"`, with that vendor's rpm.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_ratelimit_buckets.py
from __future__ import annotations

from rtrade.data.ratelimit import OANDA_BUCKET, market_bucket


def test_oanda_bucket_rpm() -> None:
    assert OANDA_BUCKET.name == "oanda"
    assert OANDA_BUCKET.max_tokens == 6000


def test_market_bucket_names_are_per_account() -> None:
    assert market_bucket("oanda", 1).name == "oanda_acc1"
    assert market_bucket("oanda", 2).name == "oanda_acc2"
    assert market_bucket("twelvedata", 1).name == "twelvedata_k1"


def test_market_bucket_rpm_matches_vendor() -> None:
    assert market_bucket("oanda", 1).max_tokens == 6000
    assert market_bucket("twelvedata", 1).max_tokens == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_ratelimit_buckets.py -q`
Expected: FAIL — `ImportError: cannot import name 'OANDA_BUCKET'`.

- [ ] **Step 3: Implement**

In `src/rtrade/data/ratelimit.py`, after `BINANCE_PUBLIC_BUCKET`:

```python
# OANDA v20: documented 120 req/s. Bucket at 100/s = 6000/min (safety margin).
OANDA_BUCKET = BucketConfig.per_minute("oanda", 6000)

_VENDOR_RPM: dict[str, int] = {"oanda": 6000, "twelvedata": 7}
_VENDOR_BUCKET_PREFIX: dict[str, str] = {"oanda": "oanda_acc", "twelvedata": "twelvedata_k"}


def market_bucket(provider: str, index: int) -> BucketConfig:
    """Per-account/key bucket so each leg rate-limits independently."""
    rpm = _VENDOR_RPM.get(provider, 7)
    prefix = _VENDOR_BUCKET_PREFIX.get(provider, f"{provider}_")
    return BucketConfig.per_minute(f"{prefix}{index}", rpm)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_ratelimit_buckets.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Gate + commit**

```
git add src/rtrade/data/ratelimit.py tests/unit/test_ratelimit_buckets.py
git commit -F COMMIT_MSG_TMP.txt
```
Message: `feat(sp1): OANDA rate bucket + per-account market_bucket factory`

---

## Task 3: `OandaProvider` (REST v20)

**Files:**
- Create: `src/rtrade/data/oanda_provider.py`
- Test: `tests/unit/test_oanda_provider.py`

**Interfaces:**
- Consumes: `MarketDataProvider` ABC, domain `Candle`/`Quote`, `RateLimiter`, `OANDA_BUCKET`/`BucketConfig`, `Timeframe`, `ensure_utc`, `ProviderError`/`RateLimitExceeded`.
- Produces:
  - `OANDA_PRACTICE_URL: str`, `OANDA_LIVE_URL: str`
  - `OandaProvider(token: str, account_id: str, rate_limiter: RateLimiter, *, bucket: BucketConfig = OANDA_BUCKET, practice: bool = True, http_timeout: float = 15.0)`
  - methods `fetch_ohlcv(symbol, timeframe, since, limit=500) -> list[Candle]`, `fetch_quote(symbol) -> Quote`, `fetch_spread(symbol) -> float | None`, `close() -> None`.
- Note: `fetch_quote`/`fetch_spread` use `/v3/accounts/{account_id}/pricing`; `fetch_ohlcv` uses `/v3/instruments/{symbol}/candles` with `price=M`. Only `fetch_ohlcv` retries on `RateLimitExceeded` (tenacity) so quote 429 tests stay fast.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_oanda_provider.py
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from rtrade.core.constants import Timeframe
from rtrade.core.errors import ProviderError, RateLimitExceeded
from rtrade.data.oanda_provider import OANDA_PRACTICE_URL, OandaProvider


class _NoLimit:
    async def acquire(self, _bucket: Any) -> None:
        return None


_CANDLES = {
    "candles": [
        {"complete": True, "volume": 10, "time": "2025-01-01T00:00:00.000000000Z",
         "mid": {"o": "2600.0", "h": "2605.0", "l": "2599.0", "c": "2603.0"}},
        {"complete": True, "volume": 12, "time": "2025-01-01T00:05:00.000000000Z",
         "mid": {"o": "2603.0", "h": "2607.0", "l": "2602.0", "c": "2606.0"}},
        {"complete": False, "volume": 3, "time": "2025-01-01T00:10:00.000000000Z",
         "mid": {"o": "2606.0", "h": "2606.5", "l": "2605.0", "c": "2605.5"}},
    ]
}
_PRICING = {"prices": [{"bids": [{"price": "2603.40"}], "asks": [{"price": "2603.80"}]}]}


@pytest.mark.asyncio
async def test_fetch_ohlcv_parses_complete_candles_ascending() -> None:
    with respx.mock(base_url=OANDA_PRACTICE_URL) as mock:
        mock.get("/v3/instruments/XAU_USD/candles").mock(
            return_value=httpx.Response(200, json=_CANDLES)
        )
        p = OandaProvider("tok", "acc", _NoLimit())  # type: ignore[arg-type]
        candles = await p.fetch_ohlcv("XAU_USD", Timeframe.M5, datetime(2025, 1, 1, tzinfo=UTC))
        await p.close()
    assert len(candles) == 2  # forming bar (complete=false) dropped
    assert [float(c.close) for c in candles] == [2603.0, 2606.0]
    assert candles[0].ts < candles[1].ts


@pytest.mark.asyncio
async def test_fetch_ohlcv_http_400_raises_provider_error() -> None:
    with respx.mock(base_url=OANDA_PRACTICE_URL) as mock:
        mock.get("/v3/instruments/XAU_USD/candles").mock(
            return_value=httpx.Response(400, text="bad")
        )
        p = OandaProvider("tok", "acc", _NoLimit())  # type: ignore[arg-type]
        with pytest.raises(ProviderError):
            await p.fetch_ohlcv("XAU_USD", Timeframe.M5, datetime(2025, 1, 1, tzinfo=UTC))
        await p.close()


@pytest.mark.asyncio
async def test_fetch_quote_returns_mid() -> None:
    with respx.mock(base_url=OANDA_PRACTICE_URL) as mock:
        mock.get("/v3/accounts/acc/pricing").mock(return_value=httpx.Response(200, json=_PRICING))
        p = OandaProvider("tok", "acc", _NoLimit())  # type: ignore[arg-type]
        q = await p.fetch_quote("XAU_USD")
        await p.close()
    assert float(q.price) == pytest.approx(2603.60)


@pytest.mark.asyncio
async def test_fetch_quote_429_raises_ratelimit() -> None:
    with respx.mock(base_url=OANDA_PRACTICE_URL) as mock:
        mock.get("/v3/accounts/acc/pricing").mock(return_value=httpx.Response(429, json={}))
        p = OandaProvider("tok", "acc", _NoLimit())  # type: ignore[arg-type]
        with pytest.raises(RateLimitExceeded):
            await p.fetch_quote("XAU_USD")
        await p.close()


@pytest.mark.asyncio
async def test_fetch_spread_returns_ask_minus_bid() -> None:
    with respx.mock(base_url=OANDA_PRACTICE_URL) as mock:
        mock.get("/v3/accounts/acc/pricing").mock(return_value=httpx.Response(200, json=_PRICING))
        p = OandaProvider("tok", "acc", _NoLimit())  # type: ignore[arg-type]
        spread = await p.fetch_spread("XAU_USD")
        await p.close()
    assert spread == pytest.approx(0.40)


@pytest.mark.asyncio
async def test_unsupported_timeframe_raises() -> None:
    p = OandaProvider("tok", "acc", _NoLimit())  # type: ignore[arg-type]
    # Timeframe has no member unsupported by OANDA in the enum; assert map covers all used TFs.
    from rtrade.data.oanda_provider import _TF_MAP
    for tf in (Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1):
        assert tf in _TF_MAP
    await p.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_oanda_provider.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rtrade.data.oanda_provider'`.

- [ ] **Step 3: Implement the provider**

```python
# src/rtrade/data/oanda_provider.py
"""OANDA v20 REST market-data provider (FX + metals incl. XAU_USD).

One instance = one OANDA account/token (a single composite "leg"). Practice and
live share the v20 API shape; only the host differs. Uses mid prices (price=M).
Each instance rate-limits through its own Redis token bucket so multiple
accounts back off independently.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from rtrade.core.constants import Timeframe
from rtrade.core.errors import ProviderError, RateLimitExceeded
from rtrade.core.timeutil import ensure_utc
from rtrade.data.base import Candle, MarketDataProvider, Quote
from rtrade.data.ratelimit import OANDA_BUCKET, BucketConfig, RateLimiter

logger = structlog.get_logger(__name__)

OANDA_PRACTICE_URL = "https://api-fxpractice.oanda.com"
OANDA_LIVE_URL = "https://api-fxtrade.oanda.com"

_TF_MAP: dict[Timeframe, str] = {
    Timeframe.M1: "M1",
    Timeframe.M5: "M5",
    Timeframe.M15: "M15",
    Timeframe.H1: "H1",
    Timeframe.H4: "H4",
    Timeframe.D1: "D",
}


def _parse_oanda_time(raw: str) -> datetime:
    """RFC3339 with up to 9 fractional digits + 'Z' → UTC bar-open datetime."""
    return datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)


class OandaProvider(MarketDataProvider):
    """XAU_USD / FX OHLCV + quote via OANDA v20 REST (one account per instance)."""

    def __init__(
        self,
        token: str,
        account_id: str,
        rate_limiter: RateLimiter,
        *,
        bucket: BucketConfig = OANDA_BUCKET,
        practice: bool = True,
        http_timeout: float = 15.0,
    ) -> None:
        if not token:
            raise ProviderError("OANDA token is required")
        self._account_id = account_id
        self._limiter = rate_limiter
        self._bucket = bucket
        base = OANDA_PRACTICE_URL if practice else OANDA_LIVE_URL
        self._http = httpx.AsyncClient(
            base_url=base,
            timeout=http_timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept-Datetime-Format": "RFC3339",
                "User-Agent": "RobilTrade/0.1",
            },
        )

    @retry(
        retry=retry_if_exception_type(RateLimitExceeded),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        since: datetime,
        limit: int = 500,
    ) -> list[Candle]:
        gran = _TF_MAP.get(timeframe)
        if gran is None:
            raise ProviderError(f"unsupported timeframe for OANDA: {timeframe}")
        await self._limiter.acquire(self._bucket)
        since_utc = ensure_utc(since)
        params: dict[str, str | int] = {
            "granularity": gran,
            "price": "M",
            "from": since_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "count": min(limit, 5000),
        }
        try:
            resp = await self._http.get(f"/v3/instruments/{symbol}/candles", params=params)
        except httpx.HTTPError as exc:
            raise ProviderError(f"OANDA HTTP error: {exc}") from exc
        if resp.status_code == 429:
            raise RateLimitExceeded("OANDA 429: rate limit hit")
        if resp.status_code >= 400:
            raise ProviderError(f"OANDA HTTP {resp.status_code}: {resp.text[:200]}")

        body = resp.json()
        candles: list[Candle] = []
        for row in body.get("candles", []):
            if not row.get("complete", False):
                continue
            mid = row.get("mid", {})
            try:
                candles.append(
                    Candle(
                        symbol=symbol,
                        timeframe=timeframe,
                        ts=_parse_oanda_time(row["time"]),
                        open=Decimal(mid["o"]),
                        high=Decimal(mid["h"]),
                        low=Decimal(mid["l"]),
                        close=Decimal(mid["c"]),
                        volume=Decimal(str(row.get("volume", 0))),
                    )
                )
            except (KeyError, ValueError) as exc:
                logger.warning("skipping invalid OANDA candle", error=str(exc))
        candles.sort(key=lambda c: c.ts)
        logger.info(
            "oanda ohlcv fetched",
            symbol=symbol,
            timeframe=timeframe.value,
            count=len(candles),
        )
        return candles

    async def _pricing(self, symbol: str) -> dict[str, object]:
        resp = await self._http.get(
            f"/v3/accounts/{self._account_id}/pricing",
            params={"instruments": symbol},
        )
        if resp.status_code == 429:
            raise RateLimitExceeded("OANDA 429 on pricing")
        if resp.status_code >= 400:
            raise ProviderError(f"OANDA pricing HTTP {resp.status_code}")
        body: dict[str, object] = resp.json()
        return body

    async def fetch_quote(self, symbol: str) -> Quote:
        await self._limiter.acquire(self._bucket)
        try:
            body = await self._pricing(symbol)
        except httpx.HTTPError as exc:
            raise ProviderError(f"OANDA quote error: {exc}") from exc
        prices = body.get("prices", [])
        if not isinstance(prices, list) or not prices:
            raise ProviderError(f"OANDA pricing empty for {symbol}")
        p = prices[0]
        bid = Decimal(str(p["bids"][0]["price"]))
        ask = Decimal(str(p["asks"][0]["price"]))
        return Quote(symbol=symbol, price=(bid + ask) / 2, ts=datetime.now(UTC))

    async def fetch_spread(self, symbol: str) -> float | None:
        await self._limiter.acquire(self._bucket)
        try:
            body = await self._pricing(symbol)
        except (httpx.HTTPError, ProviderError, RateLimitExceeded):
            return None
        prices = body.get("prices", [])
        if not isinstance(prices, list) or not prices:
            return None
        p = prices[0]
        return float(Decimal(str(p["asks"][0]["price"])) - Decimal(str(p["bids"][0]["price"])))

    async def close(self) -> None:
        await self._http.aclose()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_oanda_provider.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Gate + commit**

Run the full gate (`ruff check`, `ruff format`, `mypy --strict src`, `pytest tests -q`). Expected: clean, all green.

```
git add src/rtrade/data/oanda_provider.py tests/unit/test_oanda_provider.py
git commit -F COMMIT_MSG_TMP.txt
```
Message: `feat(sp1): OandaProvider v20 REST (ohlcv/quote/spread)`

---

## Task 4: `CompositeMarketDataProvider` (failover + health + round-robin)

**Files:**
- Create: `src/rtrade/data/composite_market.py`
- Test: `tests/unit/test_composite_market.py`

**Interfaces:**
- Consumes: `MarketDataProvider` ABC, `Candle`, `Quote`, `ProviderError`, `RateLimitExceeded`.
- Produces:
  - `MarketSourceHealth` dataclass (`name, last_success, last_error, consecutive_failures, last_attempt`).
  - `CompositeMarketDataProvider(legs: list[tuple[str, MarketDataProvider]], *, alert_callback: Callable[[str], Awaitable[None]] | None = None, mode: Literal["failover", "round_robin"] = "failover")`
  - methods mirror the ABC; on each call iterate legs (failover) or rotate start index (round_robin); a leg that raises `RateLimitExceeded`/`ProviderError` is recorded and skipped; raise `ProviderError` only when ALL legs fail. `health_snapshot()`, `active_tier()`, `close()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_composite_market.py
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from rtrade.core.constants import Timeframe
from rtrade.core.errors import ProviderError, RateLimitExceeded
from rtrade.data.base import Candle, MarketDataProvider, Quote
from rtrade.data.composite_market import CompositeMarketDataProvider


def _candle() -> Candle:
    return Candle(
        symbol="XAU_USD", timeframe=Timeframe.M5, ts=datetime(2025, 1, 1, tzinfo=UTC),
        open=Decimal("2600"), high=Decimal("2601"), low=Decimal("2599"), close=Decimal("2600"),
    )


class _Leg(MarketDataProvider):
    def __init__(self, *, fail: Exception | None = None) -> None:
        self.fail = fail
        self.ohlcv_calls = 0
        self.closed = False

    async def fetch_ohlcv(self, symbol: str, timeframe: Timeframe, since: datetime, limit: int = 500) -> list[Candle]:
        self.ohlcv_calls += 1
        if self.fail is not None:
            raise self.fail
        return [_candle()]

    async def fetch_quote(self, symbol: str) -> Quote:
        if self.fail is not None:
            raise self.fail
        return Quote(symbol=symbol, price=Decimal("2600"), ts=datetime.now(UTC))

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_first_leg_used_when_healthy() -> None:
    a, b = _Leg(), _Leg()
    comp = CompositeMarketDataProvider([("a", a), ("b", b)])
    out = await comp.fetch_ohlcv("XAU_USD", Timeframe.M5, datetime(2025, 1, 1, tzinfo=UTC))
    assert len(out) == 1
    assert a.ohlcv_calls == 1 and b.ohlcv_calls == 0
    assert comp.active_tier() == "a"


@pytest.mark.asyncio
async def test_failover_to_next_leg_on_ratelimit() -> None:
    alerts: list[str] = []

    async def cb(msg: str) -> None:
        alerts.append(msg)

    a = _Leg(fail=RateLimitExceeded("429"))
    b = _Leg()
    comp = CompositeMarketDataProvider([("a", a), ("b", b)], alert_callback=cb)
    out = await comp.fetch_ohlcv("XAU_USD", Timeframe.M5, datetime(2025, 1, 1, tzinfo=UTC))
    assert len(out) == 1
    assert a.ohlcv_calls == 1 and b.ohlcv_calls == 1
    assert comp.health_snapshot()["a"].consecutive_failures == 1
    assert any("a" in m for m in alerts)


@pytest.mark.asyncio
async def test_all_legs_fail_raises_provider_error() -> None:
    a = _Leg(fail=ProviderError("down"))
    b = _Leg(fail=RateLimitExceeded("429"))
    comp = CompositeMarketDataProvider([("a", a), ("b", b)])
    with pytest.raises(ProviderError):
        await comp.fetch_ohlcv("XAU_USD", Timeframe.M5, datetime(2025, 1, 1, tzinfo=UTC))


@pytest.mark.asyncio
async def test_round_robin_distributes_calls() -> None:
    a, b = _Leg(), _Leg()
    comp = CompositeMarketDataProvider([("a", a), ("b", b)], mode="round_robin")
    for _ in range(4):
        await comp.fetch_ohlcv("XAU_USD", Timeframe.M5, datetime(2025, 1, 1, tzinfo=UTC))
    assert a.ohlcv_calls == 2 and b.ohlcv_calls == 2


@pytest.mark.asyncio
async def test_close_closes_all_legs() -> None:
    a, b = _Leg(), _Leg()
    comp = CompositeMarketDataProvider([("a", a), ("b", b)])
    await comp.close()
    assert a.closed and b.closed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_composite_market.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rtrade.data.composite_market'`.

- [ ] **Step 3: Implement**

```python
# src/rtrade/data/composite_market.py
"""Composite market-data provider: ordered failover across vendor accounts.

Mirrors data/composite_calendar.py. Each "leg" is one account/key of one vendor
with its own rate bucket. On RateLimitExceeded/ProviderError the composite
records health, alerts on the transition, and advances to the next leg. It
raises ProviderError only when every leg fails (fail-CLOSE for signals).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import structlog

from rtrade.core.constants import Timeframe
from rtrade.core.errors import ProviderError, RateLimitExceeded
from rtrade.data.base import Candle, MarketDataProvider, Quote

logger = structlog.get_logger(__name__)

AlertCallback = Callable[[str], Awaitable[None]]


@dataclass
class MarketSourceHealth:
    name: str
    last_success: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    last_attempt: datetime | None = None


class CompositeMarketDataProvider(MarketDataProvider):
    def __init__(
        self,
        legs: list[tuple[str, MarketDataProvider]],
        *,
        alert_callback: AlertCallback | None = None,
        mode: Literal["failover", "round_robin"] = "failover",
    ) -> None:
        if not legs:
            raise ValueError("CompositeMarketDataProvider needs at least one leg")
        self._legs = list(legs)
        self._alert = alert_callback
        self._mode = mode
        self._rr_index = 0
        self._health: dict[str, MarketSourceHealth] = {
            n: MarketSourceHealth(name=n) for n, _ in legs
        }

    async def _emit(self, message: str) -> None:
        if self._alert is not None:
            try:
                await self._alert(message)
            except Exception as exc:  # noqa: BLE001 - alert must never break data path
                logger.warning("market alert callback failed", error=str(exc))

    def _ordered_legs(self) -> list[tuple[str, MarketDataProvider]]:
        if self._mode == "round_robin" and len(self._legs) > 1:
            start = self._rr_index % len(self._legs)
            self._rr_index += 1
            return self._legs[start:] + self._legs[:start]
        return self._legs

    async def _attempt(self, op_name: str, call: Callable[[MarketDataProvider], Awaitable[object]]) -> object:
        failed: list[str] = []
        for name, provider in self._ordered_legs():
            health = self._health[name]
            health.last_attempt = datetime.now(UTC)
            if failed:
                await self._emit(f"⚠️ Market fallback ({op_name}): {failed[-1]} gagal → coba {name}")
            try:
                result = await call(provider)
            except (RateLimitExceeded, ProviderError) as exc:
                health.last_error = str(exc)
                health.consecutive_failures += 1
                logger.warning("market leg failed", leg=name, op=op_name, error=str(exc))
                failed.append(name)
                continue
            health.last_success = datetime.now(UTC)
            health.consecutive_failures = 0
            health.last_error = None
            if failed:
                await self._emit(f"✅ Market fallback recovered: {' → '.join(failed)} gagal → {name} OK")
            return result
        await self._emit("🚨 MARKET DATA: semua leg gagal")
        raise ProviderError(f"all market-data legs unavailable for {op_name}")

    async def fetch_ohlcv(
        self, symbol: str, timeframe: Timeframe, since: datetime, limit: int = 500
    ) -> list[Candle]:
        result = await self._attempt(
            "fetch_ohlcv", lambda p: p.fetch_ohlcv(symbol, timeframe, since, limit)
        )
        assert isinstance(result, list)
        return result

    async def fetch_quote(self, symbol: str) -> Quote:
        result = await self._attempt("fetch_quote", lambda p: p.fetch_quote(symbol))
        assert isinstance(result, Quote)
        return result

    async def fetch_spread(self, symbol: str) -> float | None:
        for _name, provider in self._ordered_legs():
            spread = await provider.fetch_spread(symbol)
            if spread is not None:
                return spread
        return None

    def health_snapshot(self) -> dict[str, MarketSourceHealth]:
        return dict(self._health)

    def active_tier(self) -> str | None:
        best: tuple[datetime, str] | None = None
        for h in self._health.values():
            if h.last_success is not None and (best is None or h.last_success > best[0]):
                best = (h.last_success, h.name)
        return best[1] if best else None

    async def close(self) -> None:
        for _name, provider in self._legs:
            try:
                await provider.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("market leg close failed", error=str(exc))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_composite_market.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Gate + commit**

```
git add src/rtrade/data/composite_market.py tests/unit/test_composite_market.py
git commit -F COMMIT_MSG_TMP.txt
```
Message: `feat(sp1): CompositeMarketDataProvider failover + health + round-robin`

---

## Task 5: Rebuild `_make_market_provider` to compose legs

**Files:**
- Modify: `src/rtrade/pipeline/scan.py` (imports block ~`:49-60` and `_make_market_provider` at `:1256-1266`)
- Test: `tests/unit/test_make_market_provider.py`

**Interfaces:**
- Consumes: `Secrets.market_keys_for`, `market_bucket`, `OandaProvider`, `TwelveDataProvider`, `CcxtProvider`, `CompositeMarketDataProvider`, `ConfigError`.
- Produces: `_make_market_provider(instrument, cfg, limiter) -> MarketDataProvider` returning a `CompositeMarketDataProvider` for `oanda`/`twelvedata` (one leg per configured account/key; OANDA legs first, then TwelveData fallback legs), unchanged `CcxtProvider` for `ccxt_binance`.
- Known limitation (documented): TwelveData legs share the module `TWELVEDATA_BUCKET` (per-key isolation would need a `bucket` param on `TwelveDataProvider`; out of scope, TwelveData is last-resort fallback).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_make_market_provider.py
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from rtrade.core.config import Secrets
from rtrade.core.errors import ConfigError
from rtrade.data.composite_market import CompositeMarketDataProvider
from rtrade.pipeline.scan import _make_market_provider


def _cfg(**over: Any) -> SimpleNamespace:
    return SimpleNamespace(secrets=Secrets(_env_file=None, **over))  # type: ignore[call-arg]


def _inst(provider: str) -> SimpleNamespace:
    return SimpleNamespace(provider=provider, provider_symbol="XAU_USD", symbol="XAUUSD")


def _close(prov: Any) -> None:
    asyncio.run(prov.close())


def test_oanda_builds_composite_two_legs() -> None:
    cfg = _cfg(
        oanda_token_1="t1", oanda_account_1="a1",
        oanda_token_2="t2", oanda_account_2="a2",
    )
    prov = _make_market_provider(_inst("oanda"), cfg, None)
    try:
        assert isinstance(prov, CompositeMarketDataProvider)
        assert list(prov.health_snapshot().keys()) == ["oanda_1", "oanda_2"]
    finally:
        _close(prov)


def test_oanda_appends_twelvedata_fallback_legs() -> None:
    cfg = _cfg(oanda_token_1="t1", oanda_account_1="a1", twelvedata_api_key="td1")
    prov = _make_market_provider(_inst("oanda"), cfg, None)
    try:
        assert list(prov.health_snapshot().keys()) == ["oanda_1", "twelvedata_1"]
    finally:
        _close(prov)


def test_oanda_no_credentials_raises() -> None:
    with pytest.raises(ConfigError):
        _make_market_provider(_inst("oanda"), _cfg(), None)


def test_twelvedata_only_builds_composite() -> None:
    cfg = _cfg(twelvedata_api_key="td1", twelvedata_api_key_2="td2")
    prov = _make_market_provider(_inst("twelvedata"), cfg, None)
    try:
        assert list(prov.health_snapshot().keys()) == ["twelvedata_1", "twelvedata_2"]
    finally:
        _close(prov)


def test_unsupported_provider_raises() -> None:
    with pytest.raises(ConfigError):
        _make_market_provider(_inst("bogus"), _cfg(), None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_make_market_provider.py -q`
Expected: FAIL — current factory returns a bare `TwelveDataProvider`, so `health_snapshot` / composite assertions error.

- [ ] **Step 3: Update imports in `scan.py`**

In the `from rtrade.data...` import region (around `:27-34`), add:

```python
from rtrade.data.composite_market import CompositeMarketDataProvider
from rtrade.data.oanda_provider import OandaProvider
from rtrade.data.ratelimit import RateLimiter, market_bucket
```
(`RateLimiter` is already imported — keep a single import line; just add `market_bucket` to the existing `from rtrade.data.ratelimit import RateLimiter` line.)

- [ ] **Step 4: Replace `_make_market_provider`**

Replace the body at `src/rtrade/pipeline/scan.py:1256-1266` with:

```python
def _make_market_provider(
    instrument: InstrumentConfig,
    cfg: AppConfig,
    limiter: RateLimiter,
) -> MarketDataProvider:
    if instrument.provider == "ccxt_binance":
        return CcxtProvider(limiter)

    legs: list[tuple[str, MarketDataProvider]] = []
    if instrument.provider == "oanda":
        practice = cfg.secrets.oanda_env == "practice"
        for i, (token, account) in enumerate(cfg.secrets.market_keys_for("oanda"), start=1):
            legs.append(
                (
                    f"oanda_{i}",
                    OandaProvider(
                        token,
                        account or "",
                        limiter,
                        bucket=market_bucket("oanda", i),
                        practice=practice,
                    ),
                )
            )
        # TwelveData as last-resort fallback after all OANDA accounts.
        for j, (key, _acc) in enumerate(cfg.secrets.market_keys_for("twelvedata"), start=1):
            legs.append((f"twelvedata_{j}", TwelveDataProvider(key, limiter)))
        if not legs:
            raise ConfigError(
                "provider 'oanda' selected but no OANDA_TOKEN_*/ACCOUNT_* (or TwelveData) configured"
            )
        return CompositeMarketDataProvider(legs)

    if instrument.provider == "twelvedata":
        for j, (key, _acc) in enumerate(cfg.secrets.market_keys_for("twelvedata"), start=1):
            legs.append((f"twelvedata_{j}", TwelveDataProvider(key, limiter)))
        if not legs:
            raise ConfigError("provider 'twelvedata' selected but no TWELVEDATA_API_KEY configured")
        return CompositeMarketDataProvider(legs)

    raise ConfigError(f"unsupported market data provider: {instrument.provider}")
```

- [ ] **Step 5: Run test + gate**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_make_market_provider.py -q`
Expected: PASS (5 passed).
Then full gate (`ruff`, `ruff format`, `mypy --strict src`, `pytest tests -q`) — all green. Note: existing tests that monkeypatch `_make_market_provider` or build providers may need the composite shape; run the full suite and fix any that asserted the old bare-provider return.

- [ ] **Step 6: Commit**

```
git add src/rtrade/pipeline/scan.py tests/unit/test_make_market_provider.py
git commit -F COMMIT_MSG_TMP.txt
```
Message: `feat(sp1): compose market-data legs (OANDA primary + TwelveData fallback)`

---

## Task 6: Wire config + `.env.example` + integration test

**Files:**
- Modify: `config/instruments.yaml` (XAUUSD entry)
- Modify: `.env.example`
- Test: `tests/integration/test_oanda_live.py`

**Interfaces:**
- Consumes: everything above.
- Produces: XAUUSD routed through OANDA; documented env vars; a live smoke test that skips without credentials.

- [ ] **Step 1: Write the integration test (skips without creds)**

```python
# tests/integration/test_oanda_live.py
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

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
    limiter = RateLimiter(_get_redis(os.environ.get("RTRADE_TEST_REDIS_URL", "redis://localhost:6379/0")))
    provider = OandaProvider(token, account, limiter, bucket=OANDA_BUCKET, practice=True)
    try:
        since = datetime.now(UTC) - timedelta(days=2)
        candles = await provider.fetch_ohlcv("XAU_USD", Timeframe.M5, since, limit=100)
        assert len(candles) > 0
        assert all(c.high >= c.low for c in candles)
    finally:
        await provider.close()
```

- [ ] **Step 2: Run it to confirm it SKIPS cleanly (no creds in this env)**

Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_oanda_live.py -q`
Expected: `1 skipped` (skip reason: OANDA creds not set). This proves the default gate stays green without a live account.

- [ ] **Step 3: Point XAUUSD at OANDA in `config/instruments.yaml`**

Change the XAUUSD block's `provider`/`provider_symbol` (leave `timeframes: ["1h","4h"]` for now — M5/M15 enabling is SP-2):

```yaml
  - symbol: XAUUSD
    market: metals
    provider: oanda
    provider_symbol: "XAU_USD"
    timeframes: ["1h", "4h"]
    context_timeframe: "1d"
    pip_size: 0.01
    quote_currency: USD
    related_currencies: [USD]
    session_filter: true
```

- [ ] **Step 4: Document env vars in `.env.example`**

Append:

```bash
# --- OANDA market data (FX/metals primary). Free practice account + REST token. ---
# Create a demo (practice) account at oanda.com, then Manage API Access → generate token.
OANDA_ENV=practice            # practice | live
OANDA_TOKEN_1=
OANDA_ACCOUNT_1=
# Optional extra OANDA accounts for multi-account failover:
OANDA_TOKEN_2=
OANDA_ACCOUNT_2=
OANDA_TOKEN_3=
OANDA_ACCOUNT_3=
# Optional TwelveData fallback keys (used only if all OANDA legs fail):
TWELVEDATA_API_KEY_2=
TWELVEDATA_API_KEY_3=
```

- [ ] **Step 5: Full gate + commit**

Run the full gate (`ruff`, `ruff format`, `mypy --strict src`, `pytest tests -q`). Expected: all green, OANDA live test skipped.

```
git add config/instruments.yaml .env.example tests/integration/test_oanda_live.py
git commit -F COMMIT_MSG_TMP.txt
```
Message: `feat(sp1): route XAUUSD via OANDA + document multi-account env + live smoke test`

---

## Self-Review (completed by plan author)

**1. Spec coverage (SP-1 section of design):**
- OandaProvider (§6.3) → Task 3. ✅
- CompositeMarketDataProvider failover + round-robin (§6.3) → Task 4. ✅
- Secrets multi-account fields + `market_keys_for` (§6.3) → Task 1. ✅
- Per-account rate buckets (§6.3) → Task 2. ✅
- Provider factory composing legs (§6.3) → Task 5. ✅
- instruments.yaml OANDA routing (§6.3) → Task 6. ✅
- Tests incl. respx + composite + integration skip (§6.5) → Tasks 3,4,6. ✅
- Note: full M5/M15 timeframe enablement is intentionally SP-2, not SP-1 (keeps SP-1 shippable without the MTF engine).

**2. Placeholder scan:** No TBD/TODO; every code step has complete, typed code and exact commands.

**3. Type consistency:** `OandaProvider.__init__(token, account_id, rate_limiter, *, bucket, practice, http_timeout)` used identically in Tasks 3, 5, 6. `CompositeMarketDataProvider(legs, *, alert_callback, mode)` consistent in Tasks 4, 5. `market_bucket(provider, index)` consistent in Tasks 2, 5. `Secrets.market_keys_for(provider) -> list[tuple[str, str|None]]` consistent in Tasks 1, 5.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-20-sp1-data-layer.md`.
This is **SP-1 of 6**. Recommended execution: **subagent-driven-development** (fresh subagent per task + two-stage review), one task at a time, full gate green before advancing.
