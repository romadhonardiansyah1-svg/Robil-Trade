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
    df["open"].astype(float).values
    n_bars = len(df)

    equity = initial_equity
    equity_curve = [equity]
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
        if smart_exit is not None:
            atr_col = df["atr"].astype(float).values if "atr" in df.columns else None
            exit_state = ExitState(
                current_sl=trade.stop_loss,
            )
            for i in range(trade.fill_bar + 1, n_bars):  # type: ignore[operator]
                atr_val = (
                    float(atr_col[i])
                    if atr_col is not None
                    else abs(trade.fill_price - trade.stop_loss)
                )  # type: ignore[index]
                exit_state, exit_reason = apply_smart_exit(
                    exit_state,
                    smart_exit,
                    direction=trade.direction,
                    entry=trade.fill_price,  # type: ignore[arg-type]
                    original_sl=trade.stop_loss,
                    take_profit=trade.take_profit,
                    bar_high=highs[i],
                    bar_low=lows[i],
                    atr=atr_val,
                )
                if exit_reason is not None:
                    trade.exit_bar = i
                    if exit_reason == "SL":
                        trade.exit_price = exit_state.current_sl
                    else:
                        trade.exit_price = trade.take_profit
                    trade.exit_reason = exit_reason
                    break
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
                    trade.exit_price = trade.stop_loss
                    trade.exit_reason = "SL"
                    break
                elif tp_hit:
                    trade.exit_bar = i
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
            if trade.direction == "BUY":
                raw_pnl = trade.exit_price - trade.fill_price
            else:
                raw_pnl = trade.fill_price - trade.exit_price

            # Apply costs.
            trade.cost = 0.0
            if cost_model is not None:
                trade.cost = compute_trade_cost(cost_model, trade.fill_price, trade.direction)

            sl_dist = abs(trade.fill_price - trade.stop_loss)
            if sl_dist > 0:
                # Risk-based sizing.
                risk_amount = equity * (risk_pct / 100)
                position_size = risk_amount / sl_dist
                trade.pnl = (raw_pnl - trade.cost) * position_size
                trade.r_multiple = (raw_pnl - trade.cost) / sl_dist
            else:
                trade.pnl = 0.0
                trade.r_multiple = 0.0

            equity += trade.pnl or 0.0

        equity_curve.append(equity)

    return BacktestResult(
        trades=trades,
        equity_curve=equity_curve,
        initial_equity=initial_equity,
        final_equity=equity,
    )


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
