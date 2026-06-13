"""Unit tests for signal schemas and level validation (PLAN §9, §8.5)."""

from datetime import UTC, datetime

from pydantic import ValidationError
import pytest

from rtrade.core.constants import Action, Timeframe
from rtrade.signals.levels import round_to_tick, validate_and_round_levels
from rtrade.signals.schemas import ConfluenceBreakdown, LevelSet, SignalCandidate


class TestLevelSet:
    def test_valid_levels(self) -> None:
        ls = LevelSet(entry_limit=100.0, stop_loss=95.0, take_profit=110.0, atr_at_signal=5.0)
        assert ls.entry_limit == 100.0

    def test_all_same_rejected(self) -> None:
        with pytest.raises(ValidationError, match="distinct"):
            LevelSet(entry_limit=100.0, stop_loss=100.0, take_profit=100.0, atr_at_signal=5.0)


class TestSignalCandidate:
    def _make(self, **overrides) -> SignalCandidate:  # type: ignore[no-untyped-def]
        defaults = {
            "candidate_id": "test",
            "symbol": "XAUUSD",
            "timeframe": Timeframe.H1,
            "strategy": "s1",
            "action": Action.BUY,
            "levels": LevelSet(
                entry_limit=100.0,
                stop_loss=95.0,
                take_profit=110.0,
                atr_at_signal=5.0,
            ),
            "confluence_score": 70,
            "confluence_breakdown": ConfluenceBreakdown(
                trend=20,
                momentum=15,
                structure=15,
                volume=10,
                macro=10,
            ),
            "risk_pct": 1.0,
            "position_size": 0.5,
            "valid_until": datetime(2026, 7, 1, tzinfo=UTC),
            "bar_ts": datetime(2026, 6, 30, tzinfo=UTC),
            "created_at": datetime(2026, 6, 30, tzinfo=UTC),
        }
        defaults.update(overrides)
        return SignalCandidate(**defaults)

    def test_valid_buy(self) -> None:
        c = self._make()
        assert c.action == Action.BUY

    def test_gr02_buy_sl_above_entry(self) -> None:
        with pytest.raises(ValidationError, match="GR-02"):
            self._make(
                levels=LevelSet(entry_limit=100, stop_loss=105, take_profit=120, atr_at_signal=5)
            )

    def test_gr02_sell_valid(self) -> None:
        c = self._make(
            action=Action.SELL,
            levels=LevelSet(entry_limit=100, stop_loss=105, take_profit=85, atr_at_signal=5),
        )
        assert c.action == Action.SELL

    def test_gr03_rr_below_floor(self) -> None:
        # RR = |103-100| / |100-97| = 1.0 < 1.5
        with pytest.raises(ValidationError, match="GR-03"):
            self._make(
                levels=LevelSet(entry_limit=100, stop_loss=97, take_profit=103, atr_at_signal=5)
            )

    def test_gr04_sl_outside_atr_bounds(self) -> None:
        # SL dist = 100 - 80 = 20. ATR = 5. 20/5 = 4.0 > 3.0
        with pytest.raises(ValidationError, match="GR-04"):
            self._make(
                levels=LevelSet(entry_limit=100, stop_loss=80, take_profit=140, atr_at_signal=5)
            )

    def test_gr05_risk_cap(self) -> None:
        with pytest.raises(ValidationError):
            self._make(risk_pct=3.0)

    def test_frozen(self) -> None:
        c = self._make()
        with pytest.raises(ValidationError):
            c.confluence_score = 90  # type: ignore[misc]


class TestRoundToTick:
    def test_xauusd_pip(self) -> None:
        assert round_to_tick(2705.123, 0.01) == 2705.12

    def test_eurusd_pip(self) -> None:
        assert round_to_tick(1.08765, 0.0001) == 1.0876  # Python banker's rounding
        assert round_to_tick(1.08755, 0.0001) == 1.0876  # rounds to even
        assert round_to_tick(1.08764, 0.0001) == 1.0876

    def test_btcusdt_pip(self) -> None:
        assert round_to_tick(65432.7, 0.1) == 65432.7

    def test_integer_pip(self) -> None:
        assert round_to_tick(105.7, 1.0) == 106.0


class TestValidateAndRoundLevels:
    def test_valid_buy(self) -> None:
        ls = LevelSet(
            entry_limit=2700.123,
            stop_loss=2693.456,
            take_profit=2714.789,
            atr_at_signal=5.0,
        )
        result = validate_and_round_levels(ls, Action.BUY, pip_size=0.01)
        assert result is not None
        assert result.entry_limit == 2700.12
        assert result.stop_loss == 2693.46
        assert result.take_profit == 2714.79

    def test_invalid_direction_returns_none(self) -> None:
        ls = LevelSet(entry_limit=100, stop_loss=105, take_profit=120, atr_at_signal=5)
        result = validate_and_round_levels(ls, Action.BUY, pip_size=0.01)
        assert result is None  # SL > entry for BUY

    def test_rr_too_low_returns_none(self) -> None:
        ls = LevelSet(entry_limit=100, stop_loss=97, take_profit=103, atr_at_signal=5)
        result = validate_and_round_levels(ls, Action.BUY, pip_size=0.01)
        assert result is None  # RR = 1.0 < 1.5
