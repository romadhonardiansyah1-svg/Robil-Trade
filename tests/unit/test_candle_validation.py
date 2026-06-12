"""Tests for candle data validation (S8): reject non-finite/invalid values."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from rtrade.core.constants import Timeframe
from rtrade.data.base import Candle


def _ts() -> datetime:
    return datetime(2025, 1, 1, tzinfo=UTC)


class TestCandleValidation:
    def test_valid_candle(self) -> None:
        c = Candle(
            symbol="XAUUSD",
            timeframe=Timeframe.H1,
            ts=_ts(),
            open=Decimal("2000"),
            high=Decimal("2010"),
            low=Decimal("1990"),
            close=Decimal("2005"),
            volume=Decimal("1000"),
        )
        assert c.close == Decimal("2005")

    def test_nan_close_rejected(self) -> None:
        with pytest.raises(ValueError, match="close invalid"):
            Candle(
                symbol="XAUUSD",
                timeframe=Timeframe.H1,
                ts=_ts(),
                open=Decimal("2000"),
                high=Decimal("2010"),
                low=Decimal("1990"),
                close=Decimal("nan"),
            )

    def test_inf_open_rejected(self) -> None:
        with pytest.raises(ValueError, match="open invalid"):
            Candle(
                symbol="XAUUSD",
                timeframe=Timeframe.H1,
                ts=_ts(),
                open=Decimal("inf"),
                high=Decimal("2010"),
                low=Decimal("1990"),
                close=Decimal("2005"),
            )

    def test_zero_price_rejected(self) -> None:
        with pytest.raises(ValueError, match="open invalid"):
            Candle(
                symbol="XAUUSD",
                timeframe=Timeframe.H1,
                ts=_ts(),
                open=Decimal("0"),
                high=Decimal("2010"),
                low=Decimal("1990"),
                close=Decimal("2005"),
            )

    def test_negative_price_rejected(self) -> None:
        with pytest.raises(ValueError, match="low invalid"):
            Candle(
                symbol="XAUUSD",
                timeframe=Timeframe.H1,
                ts=_ts(),
                open=Decimal("2000"),
                high=Decimal("2010"),
                low=Decimal("-1"),
                close=Decimal("2005"),
            )

    def test_inf_volume_rejected(self) -> None:
        with pytest.raises(ValueError, match="volume invalid"):
            Candle(
                symbol="XAUUSD",
                timeframe=Timeframe.H1,
                ts=_ts(),
                open=Decimal("2000"),
                high=Decimal("2010"),
                low=Decimal("1990"),
                close=Decimal("2005"),
                volume=Decimal("inf"),
            )

    def test_zero_volume_ok(self) -> None:
        c = Candle(
            symbol="XAUUSD",
            timeframe=Timeframe.H1,
            ts=_ts(),
            open=Decimal("2000"),
            high=Decimal("2010"),
            low=Decimal("1990"),
            close=Decimal("2005"),
            volume=Decimal("0"),
        )
        assert c.volume == Decimal("0")
