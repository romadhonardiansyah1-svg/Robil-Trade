"""Abstract base classes for market data, calendar, and derivatives providers.

Every data provider in the system implements one or more of these ABCs.
The application code never depends on a concrete provider — only on the
interface defined here (PLAN §8.1, ADR-03).

Domain dataclasses live here too (thin, validated, provider-agnostic).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from rtrade.core.constants import Timeframe
from rtrade.core.timeutil import ensure_utc

# ---------------------------------------------------------------------------
# Domain dataclasses (provider-agnostic, validated)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candle:
    """OHLCV bar. `ts` is the OPEN time of the bar, timezone-aware UTC."""

    symbol: str
    timeframe: Timeframe
    ts: datetime  # open time, UTC
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        from math import isfinite

        ensure_utc(self.ts)
        # S8: reject non-finite and non-positive OHLC values (anti-poisoning)
        for name, val in (
            ("open", self.open),
            ("high", self.high),
            ("low", self.low),
            ("close", self.close),
        ):
            f = float(val)
            if not isfinite(f) or f <= 0:
                raise ValueError(f"candle {self.ts}: {name} invalid ({val})")
        # Volume: non-negative and finite
        vol = float(self.volume)
        if not isfinite(vol) or vol < 0:
            raise ValueError(f"candle {self.ts}: volume invalid ({self.volume})")
        if not (self.high >= self.open and self.high >= self.close):
            raise ValueError(f"candle {self.ts}: high < open/close")
        if not (self.low <= self.open and self.low <= self.close):
            raise ValueError(f"candle {self.ts}: low > open/close")
        if self.high < self.low:
            raise ValueError(f"candle {self.ts}: high < low")


@dataclass(frozen=True, slots=True)
class Quote:
    """Live price snapshot for drift checking (GR-06)."""

    symbol: str
    price: Decimal
    ts: datetime

    def __post_init__(self) -> None:
        ensure_utc(self.ts)


@dataclass(frozen=True, slots=True)
class EconomicEvent:
    """Calendar event (Finnhub / Trading Economics)."""

    event_id: str  # hash(provider, event_name, time)
    event: str
    currency: str
    impact: str  # low | medium | high
    event_time: datetime
    actual: Decimal | None = None
    forecast: Decimal | None = None
    previous: Decimal | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        ensure_utc(self.event_time)
        if self.impact not in ("low", "medium", "high"):
            raise ValueError(f"invalid impact: {self.impact!r}")


@dataclass(frozen=True, slots=True)
class FundingSnapshot:
    """Crypto perpetual funding rate snapshot."""

    symbol: str
    funding_rate: Decimal
    ts: datetime

    def __post_init__(self) -> None:
        ensure_utc(self.ts)


@dataclass(frozen=True, slots=True)
class OISnapshot:
    """Open interest snapshot."""

    symbol: str
    open_interest: Decimal
    ts: datetime

    def __post_init__(self) -> None:
        ensure_utc(self.ts)


# ---------------------------------------------------------------------------
# Abstract base classes — provider interfaces
# ---------------------------------------------------------------------------


class MarketDataProvider(ABC):
    """Interface for OHLCV + live-quote data (PLAN §8.1).

    Implementations: CcxtProvider (crypto), TwelveDataProvider (XAUUSD/FX).
    """

    @abstractmethod
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        since: datetime,
        limit: int = 500,
    ) -> list[Candle]:
        """Fetch historical OHLCV candles.

        `since` is the earliest open-time to fetch (inclusive).
        Returns candles sorted ascending by ts.
        """

    @abstractmethod
    async def fetch_quote(self, symbol: str) -> Quote:
        """Fetch current price (for GR-06 drift check)."""

    async def fetch_spread(self, symbol: str) -> float | None:
        """Bid/ask spread in price units; None when unsupported (T21)."""
        return None

    @abstractmethod
    async def close(self) -> None:
        """Release resources (HTTP sessions, etc.)."""


class CalendarProvider(ABC):
    """Economic calendar events (PLAN §8.1).

    Implementations: FinnhubCalendar, (fallback) TradingEconomicsCalendar.
    """

    @abstractmethod
    async def fetch_events(
        self,
        start: date,
        end: date,
    ) -> list[EconomicEvent]:
        """Fetch economic events in [start, end]."""

    @abstractmethod
    async def close(self) -> None:
        """Release resources."""


class DerivativesProvider(ABC):
    """Crypto derivatives data — funding rate + open interest (PLAN §8.1).

    Only applicable to crypto instruments with `derivatives: true`.
    """

    @abstractmethod
    async def fetch_funding_rate(self, symbol: str) -> FundingSnapshot:
        """Fetch latest funding rate."""

    @abstractmethod
    async def fetch_open_interest(self, symbol: str) -> OISnapshot:
        """Fetch latest open interest."""

    @abstractmethod
    async def close(self) -> None:
        """Release resources."""
