"""T18: Smart exit tests — partial TP, breakeven, trailing."""

import pytest

from rtrade.backtest.smart_exit import (
    ExitState,
    SmartExitConfig,
    apply_smart_exit,
)


def _default_cfg() -> SmartExitConfig:
    return SmartExitConfig()


class TestPartialTP:
    def test_partial_taken_at_1r(self) -> None:
        cfg = _default_cfg()
        state = ExitState(current_sl=95.0)
        # BUY at 100, SL=95, TP=110, bar goes to 105 (1R = 5 pts).
        # bar_low=100.5 stays above breakeven level (entry=100).
        state, reason = apply_smart_exit(
            state,
            cfg,
            direction="BUY",
            entry=100.0,
            original_sl=95.0,
            take_profit=110.0,
            bar_high=105.0,
            bar_low=100.5,
            atr=3.0,
        )
        assert state.partial_taken is True
        assert state.remaining_pct == pytest.approx(0.50)
        assert state.realized_r == pytest.approx(0.50)
        assert reason is None  # trade continues

    def test_no_partial_below_threshold(self) -> None:
        cfg = _default_cfg()
        state = ExitState(current_sl=95.0)
        state, reason = apply_smart_exit(
            state,
            cfg,
            direction="BUY",
            entry=100.0,
            original_sl=95.0,
            take_profit=110.0,
            bar_high=104.5,
            bar_low=100.0,
            atr=3.0,
        )
        assert state.partial_taken is False
        assert reason is None


class TestBreakeven:
    def test_sl_moves_to_entry(self) -> None:
        cfg = _default_cfg()
        state = ExitState(current_sl=95.0)
        state, _ = apply_smart_exit(
            state,
            cfg,
            direction="BUY",
            entry=100.0,
            original_sl=95.0,
            take_profit=110.0,
            bar_high=105.0,
            bar_low=100.0,
            atr=3.0,
        )
        assert state.be_moved is True
        assert state.current_sl == 100.0


class TestTrailing:
    def test_trailing_activates(self) -> None:
        cfg = _default_cfg()
        state = ExitState(
            current_sl=100.0, be_moved=True, partial_taken=True, remaining_pct=0.5, realized_r=0.5
        )
        state, _ = apply_smart_exit(
            state,
            cfg,
            direction="BUY",
            entry=100.0,
            original_sl=95.0,
            take_profit=115.0,
            bar_high=108.0,
            bar_low=106.0,
            atr=2.0,
        )
        assert state.trailing_active is True
        # trail_sl = 108 - 2*2 = 104
        assert state.current_sl == pytest.approx(104.0)


class TestExitHit:
    def test_sl_hit_exits(self) -> None:
        cfg = SmartExitConfig(
            partial_tp_enabled=False,
            breakeven_enabled=False,
            trailing_enabled=False,
        )
        state = ExitState(current_sl=95.0)
        state, reason = apply_smart_exit(
            state,
            cfg,
            direction="BUY",
            entry=100.0,
            original_sl=95.0,
            take_profit=110.0,
            bar_high=100.0,
            bar_low=94.0,
            atr=3.0,
        )
        assert reason == "SL"

    def test_tp_hit_exits(self) -> None:
        cfg = SmartExitConfig(
            partial_tp_enabled=False,
            breakeven_enabled=False,
            trailing_enabled=False,
        )
        state = ExitState(current_sl=95.0)
        state, reason = apply_smart_exit(
            state,
            cfg,
            direction="BUY",
            entry=100.0,
            original_sl=95.0,
            take_profit=110.0,
            bar_high=111.0,
            bar_low=100.0,
            atr=3.0,
        )
        assert reason == "TP"


class TestIntrabarPessimism:
    """A2: adverse extreme is assumed to occur BEFORE the favorable extreme.

    On a single post-fill bar whose favorable extreme would otherwise raise the
    stop to breakeven (and take a partial), but whose adverse extreme is below
    the ORIGINAL stop, the trade must be stopped at the original stop with NO
    partial credited and NO breakeven move that bar.
    """

    def test_adverse_extreme_stops_before_partial_and_be(self) -> None:
        cfg = _default_cfg()
        state = ExitState(current_sl=95.0)
        # BUY at 100, SL=95, TP=110. bar_high=106 (+1.2R favorable) would take a
        # partial and move BE to 100; bar_low=94 is below the ORIGINAL stop (95).
        state, reason = apply_smart_exit(
            state,
            cfg,
            direction="BUY",
            entry=100.0,
            original_sl=95.0,
            take_profit=110.0,
            bar_high=106.0,
            bar_low=94.0,
            atr=3.0,
        )
        assert reason == "SL"
        # Exit at the pre-update (original) stop, NOT the raised breakeven stop.
        assert state.current_sl == pytest.approx(95.0)
        # No partial credited, no breakeven move on the bar that stopped out.
        assert state.partial_taken is False
        assert state.be_moved is False
        assert state.realized_r == pytest.approx(0.0)
        assert state.remaining_pct == pytest.approx(1.0)

    def test_sell_adverse_extreme_stops_before_partial_and_be(self) -> None:
        cfg = _default_cfg()
        state = ExitState(current_sl=105.0)
        # SELL at 100, SL=105, TP=90. bar_low=94 (+1.2R favorable) would take a
        # partial and move BE; bar_high=106 is above the ORIGINAL stop (105).
        state, reason = apply_smart_exit(
            state,
            cfg,
            direction="SELL",
            entry=100.0,
            original_sl=105.0,
            take_profit=90.0,
            bar_high=106.0,
            bar_low=94.0,
            atr=3.0,
        )
        assert reason == "SL"
        assert state.current_sl == pytest.approx(105.0)
        assert state.partial_taken is False
        assert state.be_moved is False
        assert state.realized_r == pytest.approx(0.0)
        assert state.remaining_pct == pytest.approx(1.0)
