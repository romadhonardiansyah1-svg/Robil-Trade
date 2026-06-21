"""V1: Tests for backtest harness (generate_signals, run_harness, anti-lookahead)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from rtrade.backtest.harness import generate_signals, run_harness, run_walkforward_harness
from rtrade.indicators.engine import compute as compute_indicators
from rtrade.strategies import STRATEGY_REGISTRY, StrategyConfig


def _load_s1_cfg() -> StrategyConfig:
    """Load S1 config from YAML."""
    from pathlib import Path

    import yaml

    path = Path("config/strategies/s1_trend_pullback.yaml")
    if not path.exists():
        pytest.skip("S1 config not found")
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return StrategyConfig(raw=raw)


def _make_strong_trend_df(n: int = 600) -> pd.DataFrame:
    """Generate a synthetic strong-uptrend DataFrame suitable for S1.

    Creates a steady uptrend with pullbacks to trigger S1_trend_pullback:
    - Overall trend: +0.3 per bar (strong uptrend)
    - Periodic pullbacks every ~30 bars (dip then resume)
    - Volume consistent, moderate volatility
    """
    np.random.seed(42)
    base = 100.0
    close = np.zeros(n)
    high = np.zeros(n)
    low = np.zeros(n)
    open_ = np.zeros(n)
    volume = np.zeros(n)

    for i in range(n):
        # Strong uptrend with periodic pullbacks.
        trend = base + i * 0.3
        cycle = 3.0 * np.sin(2 * np.pi * i / 30)  # 30-bar cycle for pullbacks
        noise = np.random.randn() * 0.5
        close[i] = trend + cycle + noise
        spread = abs(np.random.randn()) * 1.5 + 0.5
        high[i] = close[i] + spread
        low[i] = close[i] - spread
        open_[i] = close[i] + np.random.randn() * 0.3
        # Ensure OHLC consistency.
        high[i] = max(high[i], open_[i], close[i])
        low[i] = min(low[i], open_[i], close[i])
        volume[i] = 1000 + np.random.randint(0, 500)

    dates = pd.date_range("2023-01-01", periods=n, freq="1h")
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )
    return df


class TestGenerateSignals:
    def test_produces_at_least_one_signal(self) -> None:
        """S1 on a strong uptrend should produce >= 1 signal."""
        df = _make_strong_trend_df(600)
        df = compute_indicators(df)
        strategy = STRATEGY_REGISTRY["s1_trend_pullback"]()
        cfg = _load_s1_cfg()

        signals = generate_signals(strategy, cfg, df, warmup_bars=250)
        assert len(signals) >= 1, f"expected >= 1 signal, got {len(signals)}"

    def test_all_signals_after_warmup(self) -> None:
        """All signal bar_index must be >= warmup_bars."""
        df = _make_strong_trend_df(600)
        df = compute_indicators(df)
        strategy = STRATEGY_REGISTRY["s1_trend_pullback"]()
        cfg = _load_s1_cfg()

        signals = generate_signals(strategy, cfg, df, warmup_bars=250)
        for sig in signals:
            assert int(sig["bar_index"]) >= 250, f"signal at bar {sig['bar_index']} < 250"

    def test_all_signals_rr_valid(self) -> None:
        """All signals must have RR >= 1.5."""
        df = _make_strong_trend_df(600)
        df = compute_indicators(df)
        strategy = STRATEGY_REGISTRY["s1_trend_pullback"]()
        cfg = _load_s1_cfg()

        signals = generate_signals(strategy, cfg, df, warmup_bars=250)
        for sig in signals:
            entry = float(sig["entry_limit"])
            sl = float(sig["stop_loss"])
            tp = float(sig["take_profit"])
            sl_dist = abs(entry - sl)
            tp_dist = abs(tp - entry)
            if sl_dist > 0:
                rr = tp_dist / sl_dist
                assert rr >= 1.5, f"RR {rr:.2f} < 1.5 at bar {sig['bar_index']}"


class TestAntiLookahead:
    def test_truncated_df_same_signals(self) -> None:
        """Signals with bar_index < 500 must be identical whether we use
        df[:500] or the full df. This proves no look-ahead bias."""
        df_full = _make_strong_trend_df(600)
        df_full = compute_indicators(df_full)
        df_trunc = compute_indicators(_make_strong_trend_df(600).iloc[:500].copy())

        strategy = STRATEGY_REGISTRY["s1_trend_pullback"]()
        cfg = _load_s1_cfg()

        sigs_full = generate_signals(strategy, cfg, df_full, warmup_bars=250)
        sigs_trunc = generate_signals(strategy, cfg, df_trunc, warmup_bars=250)

        # Filter full signals to bar_index < 500.
        sigs_full_filtered = [s for s in sigs_full if int(s["bar_index"]) < 500]

        # Both should produce the same signals for bars < 500.
        assert len(sigs_full_filtered) == len(sigs_trunc), (
            f"full has {len(sigs_full_filtered)} signals < 500, truncated has {len(sigs_trunc)}"
        )

        for sf, st in zip(sigs_full_filtered, sigs_trunc, strict=True):
            assert sf["bar_index"] == st["bar_index"]
            assert sf["direction"] == st["direction"]
            assert float(sf["entry_limit"]) == pytest.approx(float(st["entry_limit"]), abs=0.01)


def _make_daily_trend_df(n: int = 500) -> pd.DataFrame:
    """Generate a synthetic DAILY-timeframe uptrend DataFrame.

    Identical shape to `_make_strong_trend_df` but indexed by calendar DAYS,
    so one bar == one day (not one hour). Used to prove that walk-forward
    warmup is sized by real bar duration, not assumed hourly bars.
    """
    np.random.seed(42)
    base = 100.0
    close = np.zeros(n)
    high = np.zeros(n)
    low = np.zeros(n)
    open_ = np.zeros(n)
    volume = np.zeros(n)

    for i in range(n):
        trend = base + i * 0.3
        cycle = 3.0 * np.sin(2 * np.pi * i / 30)
        noise = np.random.randn() * 0.5
        close[i] = trend + cycle + noise
        spread = abs(np.random.randn()) * 1.5 + 0.5
        high[i] = close[i] + spread
        low[i] = close[i] - spread
        open_[i] = close[i] + np.random.randn() * 0.3
        high[i] = max(high[i], open_[i], close[i])
        low[i] = min(low[i], open_[i], close[i])
        volume[i] = 1000 + np.random.randint(0, 500)

    dates = pd.date_range("2022-01-01", periods=n, freq="1D", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


class TestWalkForwardWarmupTimeframeAware:
    def test_daily_warmup_reserves_days_not_hours(self) -> None:
        """A8: on DAILY data, warmup_bars must reserve that many DAYS of warmup.

        With 500 daily bars (~16 months) and a 12mo/3mo window, exactly one
        walk-forward window fits. Sizing warmup as ``warmup_bars`` hours reserves
        only ~10 days, leaving the combined warmup+test slice shorter than
        ``warmup_bars + 10`` rows, so the window is skipped entirely and no
        per-window result is produced. Sizing warmup by the real bar duration
        (1 day) reserves 250 days, so the window is processed.
        """
        df = _make_daily_trend_df(500)
        df = compute_indicators(df)
        strategy = STRATEGY_REGISTRY["s1_trend_pullback"]()
        cfg = _load_s1_cfg()

        result = run_walkforward_harness(strategy, cfg, df, cost_model=None, warmup_bars=250)

        assert len(result.per_window_metrics) >= 1, (
            "expected at least one processed walk-forward window on daily data; "
            "hours-based warmup wrongly skips it"
        )


class TestRunHarness:
    def test_end_to_end(self) -> None:
        """run_harness returns a complete HarnessResult with gates dict filled."""
        df = _make_strong_trend_df(600)
        df = compute_indicators(df)
        strategy = STRATEGY_REGISTRY["s1_trend_pullback"]()
        cfg = _load_s1_cfg()

        result = run_harness(strategy, cfg, df, cost_model=None)

        assert result.signals is not None
        assert result.backtest is not None
        assert result.metrics is not None
        assert result.gates is not None
        assert len(result.gates.gate_results) >= 6  # at least 6 gates
        assert isinstance(result.permutation_p, float)
