"""Event-loop bar-by-bar backtester (PLAN §8.11.1).

The "official" backtester for Robil Trade. Key rules:
- Signal computed at close of bar i.
- Limit order active starting bar i+1.
- Fill if low[i+n] ≤ limit ≤ high[i+n] (BUY), or high[i+n] ≥ limit ≥ low[i+n] (SELL).
- SL/TP evaluated intra-bar. If BOTH hit in same bar → SL first (worst-case).
- Expired if limit untouched within valid_until.
- Equity model: risk-based sizing same as live.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from rtrade.backtest.costs import CostModel, compute_trade_cost
from rtrade.backtest.smart_exit import ExitState, SmartExitConfig, apply_smart_exit


@dataclass
class Trade:
    """A single backtest trade."""

    bar_index: int  # bar where signal was generated
    direction: str  # "BUY" or "SELL"
    entry_limit: float
    stop_loss: float
    take_profit: float
    valid_bars: int  # how many bars the limit order is valid

    # Filled by the engine:
    fill_bar: int | None = None
    fill_price: float | None = None
    exit_bar: int | None = None
    exit_price: float | None = None
    exit_reason: str | None = None  # "TP", "SL", "EXPIRED"
    pnl: float | None = None
    r_multiple: float | None = None
    cost: float = 0.0


@dataclass
class BacktestResult:
    """Results from a backtest run."""

    trades: list[Trade]
    equity_curve: list[float]
    initial_equity: float
    final_equity: float

    @property
    def n_trades(self) -> int:
        return len([t for t in self.trades if t.fill_bar is not None])

    @property
    def win_rate(self) -> float:
        filled = [t for t in self.trades if t.r_multiple is not None]
        if not filled:
            return 0.0
        wins = sum(1 for t in filled if t.r_multiple is not None and t.r_multiple > 0)
        return wins / len(filled)


def run_backtest(
    df: pd.DataFrame,
    signals: list[dict[str, object]],
    *,
    initial_equity: float = 10_000.0,
    risk_pct: float = 1.0,
    cost_model: CostModel | None = None,
    smart_exit: SmartExitConfig | None = None,
) -> BacktestResult:
    """Run event-loop backtest on a DataFrame with pre-computed signals.

    Args:
        df: OHLCV DataFrame indexed by datetime, sorted ascending.
        signals: List of dicts with keys:
            bar_index, direction, entry_limit, stop_loss, take_profit, valid_bars
        initial_equity: Starting equity.
        risk_pct: Risk per trade as percentage.
        cost_model: Transaction cost model (spread, commission, slippage).
        smart_exit: If provided, use smart exit logic (partial TP, BE, trail).

    Returns:
        BacktestResult with trade log and equity curve.
    """
    highs = df["high"].astype(float).values
    lows = df["low"].astype(float).values
    opens = df["open"].astype(float).values
    n_bars = len(df)

    # ``equity`` is the RUNNING basis used for risk sizing. Sizing intentionally
    # stays in signal order (each trade risks ``risk_pct`` of the equity standing
    # when its signal is processed), exactly as before — this keeps the
    # single-position / non-overlapping case byte-for-byte unchanged. The equity
    # CURVE, however, is rebuilt in chronological (exit-time) order after the loop
    # (A10) so that drawdown/return reflect real time order rather than signal
    # order. PnL is order-independent, so ``final_equity`` is identical either way.
    equity = initial_equity
    trades: list[Trade] = []

    # Create Trade objects from signals.
    for sig in signals:
        trades.append(
            Trade(
                bar_index=_as_int(sig["bar_index"], "bar_index"),
                direction=str(sig["direction"]),
                entry_limit=_as_float(sig["entry_limit"], "entry_limit"),
                stop_loss=_as_float(sig["stop_loss"], "stop_loss"),
                take_profit=_as_float(sig["take_profit"], "take_profit"),
                valid_bars=_as_int(sig.get("valid_bars", 6), "valid_bars"),
            )
        )

    # Process each trade.
    for trade in trades:
        start_bar = trade.bar_index + 1  # limit active from NEXT bar
        end_bar = min(trade.bar_index + trade.valid_bars + 1, n_bars)

        if start_bar >= n_bars:
            trade.exit_reason = "EXPIRED"
            continue

        # Phase 1: Check if limit order fills.
        filled = False
        for i in range(start_bar, end_bar):
            if trade.direction == "BUY":
                if lows[i] <= trade.entry_limit <= highs[i]:
                    trade.fill_bar = i
                    trade.fill_price = trade.entry_limit
                    filled = True
                    break
            else:  # SELL
                if lows[i] <= trade.entry_limit <= highs[i]:
                    trade.fill_bar = i
                    trade.fill_price = trade.entry_limit
                    filled = True
                    break

        if not filled:
            trade.exit_reason = "EXPIRED"
            continue

        # Phase 2: After fill, check SL/TP on subsequent bars.
        # If smart_exit is configured, use apply_smart_exit per bar.
        # Realized-leg accounting (A1): partials close part of the position early;
        # the remaining fraction exits at the final exit price. Defaults describe a
        # plain full-position trade (no partial) so the non-smart path is unchanged.
        realized_r = 0.0
        remaining_pct = 1.0
        partial_taken = False
        if smart_exit is not None:
            fill_price = trade.fill_price
            assert fill_price is not None  # guaranteed by the fill phase above
            atr_col = df["atr"].astype(float).values if "atr" in df.columns else None
            exit_state = ExitState(
                current_sl=trade.stop_loss,
            )
            for i in range(trade.fill_bar + 1, n_bars):  # type: ignore[operator]
                atr_val = (
                    float(atr_col[i]) if atr_col is not None else abs(fill_price - trade.stop_loss)
                )
                exit_state, exit_reason = apply_smart_exit(
                    exit_state,
                    smart_exit,
                    direction=trade.direction,
                    entry=fill_price,
                    original_sl=trade.stop_loss,
                    take_profit=trade.take_profit,
                    bar_high=highs[i],
                    bar_low=lows[i],
                    atr=atr_val,
                )
                if exit_reason is not None:
                    trade.exit_bar = i
                    if exit_reason == "SL":
                        # A9: a stop order slips with a gap. If the bar OPENED
                        # beyond the stop, fill at the worse of the stop and open.
                        sl_level = exit_state.current_sl
                        bar_open = float(opens[i])
                        if trade.direction == "BUY":
                            trade.exit_price = min(sl_level, bar_open)
                        else:
                            trade.exit_price = max(sl_level, bar_open)
                    else:
                        trade.exit_price = trade.take_profit
                    trade.exit_reason = exit_reason
                    break
            # Capture realized-leg accounting from the final smart-exit state.
            realized_r = exit_state.realized_r
            remaining_pct = exit_state.remaining_pct
            partial_taken = exit_state.partial_taken
        else:
            for i in range(trade.fill_bar, n_bars):  # type: ignore[arg-type]
                if i == trade.fill_bar:
                    # Don't exit on the fill bar itself (conservative).
                    continue

                bar_high = highs[i]
                bar_low = lows[i]

                sl_hit = False
                tp_hit = False

                if trade.direction == "BUY":
                    sl_hit = bar_low <= trade.stop_loss
                    tp_hit = bar_high >= trade.take_profit
                else:  # SELL
                    sl_hit = bar_high >= trade.stop_loss
                    tp_hit = bar_low <= trade.take_profit

                # If BOTH hit in same bar → SL first (worst-case assumption).
                if (sl_hit and tp_hit) or sl_hit:
                    trade.exit_bar = i
                    # A9: a stop order slips with a gap. If the bar OPENED beyond
                    # the stop, fill at the worse of the stop level and the open.
                    bar_open = float(opens[i])
                    if trade.direction == "BUY":
                        trade.exit_price = min(trade.stop_loss, bar_open)
                    else:
                        trade.exit_price = max(trade.stop_loss, bar_open)
                    trade.exit_reason = "SL"
                    break
                elif tp_hit:
                    trade.exit_bar = i
                    # A9: a take-profit is a limit order — a gap THROUGH the level
                    # does not give a better fill, so it fills at exactly the TP.
                    trade.exit_price = trade.take_profit
                    trade.exit_reason = "TP"
                    break

        # If still no exit (data ended), mark as open.
        if trade.exit_reason is None:
            trade.exit_reason = "OPEN"
            trade.exit_bar = n_bars - 1
            trade.exit_price = float(df.iloc[-1]["close"])

        # Calculate PnL and R-multiple.
        if trade.fill_price is not None and trade.exit_price is not None:
            # Final leg R: the remaining (unclosed) fraction exits at exit_price.
            if trade.direction == "BUY":
                final_leg_r_num = trade.exit_price - trade.fill_price
            else:
                final_leg_r_num = trade.fill_price - trade.exit_price

            # Apply costs.
            trade.cost = 0.0
            if cost_model is not None:
                trade.cost = compute_trade_cost(cost_model, trade.fill_price, trade.direction)

            sl_dist = abs(trade.fill_price - trade.stop_loss)
            if sl_dist > 0:
                # Total realized R = closed partial legs + remaining leg at exit.
                final_leg_r = final_leg_r_num / sl_dist
                gross_r = realized_r + remaining_pct * final_leg_r

                # Costs (conservative): a full round-turn is charged on the whole
                # position (cost / sl_dist in R terms). A partial fill adds an
                # extra exit-side crossing on the closed fraction, so we charge an
                # additional half round-turn on that fraction. This never
                # under-counts costs relative to the plain full-position trade.
                cost_r = trade.cost / sl_dist
                if partial_taken:
                    closed_fraction = 1.0 - remaining_pct
                    cost_r += (trade.cost / 2.0) * closed_fraction / sl_dist

                net_r = gross_r - cost_r

                # Risk-based sizing: full position risking sl_dist == risk_amount,
                # so PnL scales as net_r * risk_amount.
                risk_amount = equity * (risk_pct / 100)
                trade.r_multiple = net_r
                trade.pnl = net_r * risk_amount
            else:
                trade.pnl = 0.0
                trade.r_multiple = 0.0

            equity += trade.pnl or 0.0

    # A10: build the equity curve in chronological (exit-time) order. Sizing/PnL
    # above is unchanged; only the order in which realized PnL is accumulated into
    # the curve changes. Sort filled trades by exit_bar (then fill_bar as a stable
    # tie-break) so overlapping trades book in the order they actually closed. For
    # non-overlapping trades this order equals signal order, so the curve is
    # identical to before.
    curve_equity = initial_equity
    equity_curve = [curve_equity]
    for trade in sorted(
        (t for t in trades if t.fill_price is not None and t.pnl is not None),
        key=_curve_order_key,
    ):
        curve_equity += trade.pnl or 0.0
        equity_curve.append(curve_equity)

    return BacktestResult(
        trades=trades,
        equity_curve=equity_curve,
        initial_equity=initial_equity,
        final_equity=equity,
    )


def _curve_order_key(trade: Trade) -> tuple[int, int]:
    """Chronological sort key for equity-curve accumulation (exit_bar, fill_bar).

    Only invoked for filled trades, whose ``exit_bar`` and ``fill_bar`` are always
    set by the time the curve is built.
    """
    assert trade.exit_bar is not None
    assert trade.fill_bar is not None
    return (trade.exit_bar, trade.fill_bar)


def _as_int(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"signal field {field_name!r} must be int-compatible") from exc


def _as_float(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"signal field {field_name!r} must be float-compatible") from exc
