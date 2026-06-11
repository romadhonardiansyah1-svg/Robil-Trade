"""Unit tests for data provider base classes and domain dataclasses."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from rtrade.core.constants import Timeframe
from rtrade.core.errors import DataValidationError
from rtrade.data.base import Candle, EconomicEvent, Quote


class TestCandle:
    """Candle domain dataclass validation tests."""

    def test_valid_candle(self) -> None:
        c = Candle(
            symbol="XAUUSD",
            timeframe=Timeframe.H1,
            ts=datetime(2026, 6, 11, 14, 0, tzinfo=UTC),
            open=Decimal("2700.00"),
            high=Decimal("2710.00"),
            low=Decimal("2695.00"),
            close=Decimal("2705.00"),
            volume=Decimal("1000"),
        )
        assert c.symbol == "XAUUSD"
        assert c.timeframe == Timeframe.H1

    def test_high_below_open_rejected(self) -> None:
        with pytest.raises(ValueError, match="high < open/close"):
            Candle(
                symbol="XAUUSD",
                timeframe=Timeframe.H1,
                ts=datetime(2026, 6, 11, 14, 0, tzinfo=UTC),
                open=Decimal("2700.00"),
                high=Decimal("2690.00"),  # below open
                low=Decimal("2680.00"),
                close=Decimal("2695.00"),
                volume=Decimal("100"),
            )

    def test_low_above_close_rejected(self) -> None:
        with pytest.raises(ValueError, match="low > open/close"):
            Candle(
                symbol="XAUUSD",
                timeframe=Timeframe.H1,
                ts=datetime(2026, 6, 11, 14, 0, tzinfo=UTC),
                open=Decimal("2700.00"),
                high=Decimal("2710.00"),
                low=Decimal("2705.00"),  # above close
                close=Decimal("2695.00"),
                volume=Decimal("100"),
            )

    def test_negative_volume_rejected(self) -> None:
        with pytest.raises(ValueError, match="negative volume"):
            Candle(
                symbol="XAUUSD",
                timeframe=Timeframe.H1,
                ts=datetime(2026, 6, 11, 14, 0, tzinfo=UTC),
                open=Decimal("2700.00"),
                high=Decimal("2710.00"),
                low=Decimal("2695.00"),
                close=Decimal("2705.00"),
                volume=Decimal("-1"),
            )

    def test_naive_datetime_rejected(self) -> None:
        with pytest.raises(DataValidationError):
            Candle(
                symbol="XAUUSD",
                timeframe=Timeframe.H1,
                ts=datetime(2026, 6, 11, 14, 0),  # noqa: DTZ001
                open=Decimal("2700.00"),
                high=Decimal("2710.00"),
                low=Decimal("2695.00"),
                close=Decimal("2705.00"),
            )

    def test_high_equals_low_valid(self) -> None:
        """Doji candle is valid."""
        c = Candle(
            symbol="XAUUSD",
            timeframe=Timeframe.H1,
            ts=datetime(2026, 6, 11, 14, 0, tzinfo=UTC),
            open=Decimal("2700.00"),
            high=Decimal("2700.00"),
            low=Decimal("2700.00"),
            close=Decimal("2700.00"),
        )
        assert c.high == c.low


class TestQuote:
    def test_valid_quote(self) -> None:
        q = Quote(
            symbol="XAUUSD",
            price=Decimal("2705.50"),
            ts=datetime(2026, 6, 11, 14, 30, tzinfo=UTC),
        )
        assert q.price == Decimal("2705.50")


class TestEconomicEvent:
    def test_valid_event(self) -> None:
        e = EconomicEvent(
            event_id="abc123",
            event="Non-Farm Payrolls",
            currency="USD",
            impact="high",
            event_time=datetime(2026, 7, 4, 12, 30, tzinfo=UTC),
        )
        assert e.impact == "high"

    def test_invalid_impact_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid impact"):
            EconomicEvent(
                event_id="abc",
                event="Test",
                currency="USD",
                impact="mega",
                event_time=datetime(2026, 7, 4, 12, 30, tzinfo=UTC),
            )
