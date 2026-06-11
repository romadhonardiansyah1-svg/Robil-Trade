"""Unit tests for regime classifier (PLAN §8.3)."""

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from rtrade.core.constants import Regime
from rtrade.regime.rules import RegimeClassifier


def _make_indicator_df(
    adx: float = 30.0,
    atr_percentile: float = 50.0,
    n: int = 100,
    base_close: float = 2700.0,
) -> pd.DataFrame:
    """Create a mock indicator DataFrame."""
    np.random.seed(42)
    dates = pd.date_range(start=datetime(2026, 1, 1, tzinfo=UTC), periods=n, freq="1h")
    close = np.cumsum(np.random.randn(n) * 0.5) + base_close

    df = pd.DataFrame(
        {
            "close": close,
            "adx": adx,
            "atr_percentile": atr_percentile,
        },
        index=dates,
    )
    return df


class TestRegimeClassifier:
    def test_trend_above_25(self) -> None:
        clf = RegimeClassifier()
        df = _make_indicator_df(adx=30.0)
        state = clf.classify("XAUUSD", df)
        assert state.regime == Regime.TREND
        assert state.adx == 30.0

    def test_range_below_20(self) -> None:
        clf = RegimeClassifier()
        df = _make_indicator_df(adx=15.0)
        state = clf.classify("XAUUSD", df)
        assert state.regime == Regime.RANGE

    def test_crisis_high_atr_percentile(self) -> None:
        clf = RegimeClassifier()
        df = _make_indicator_df(adx=30.0, atr_percentile=96.0)
        state = clf.classify("XAUUSD", df)
        assert state.regime == Regime.CRISIS

    def test_hysteresis_transition_zone(self) -> None:
        """In ADX 20-25 zone, uses previous regime."""
        clf = RegimeClassifier()

        # Start in TREND.
        df_trend = _make_indicator_df(adx=30.0)
        state1 = clf.classify("XAUUSD", df_trend)
        assert state1.regime == Regime.TREND

        # Move to transition zone — should stay TREND.
        df_transition = _make_indicator_df(adx=22.0)
        state2 = clf.classify("XAUUSD", df_transition)
        assert state2.regime == Regime.TREND

    def test_hysteresis_from_range(self) -> None:
        clf = RegimeClassifier()

        # Start in RANGE.
        df_range = _make_indicator_df(adx=15.0)
        clf.classify("XAUUSD", df_range)

        # Move to transition zone — should stay RANGE.
        df_transition = _make_indicator_df(adx=22.0)
        state = clf.classify("XAUUSD", df_transition)
        assert state.regime == Regime.RANGE

    def test_crisis_overrides_adx(self) -> None:
        """CRISIS detection takes priority over ADX-based classification."""
        clf = RegimeClassifier()
        df = _make_indicator_df(adx=30.0, atr_percentile=96.0)
        state = clf.classify("XAUUSD", df)
        assert state.regime == Regime.CRISIS

    def test_per_instrument_state(self) -> None:
        clf = RegimeClassifier()
        df_trend = _make_indicator_df(adx=30.0)
        df_range = _make_indicator_df(adx=15.0)

        clf.classify("XAUUSD", df_trend)
        clf.classify("EURUSD", df_range)

        assert clf.get_previous("XAUUSD") is not None
        assert clf.get_previous("XAUUSD").regime == Regime.TREND  # type: ignore[union-attr]
        assert clf.get_previous("EURUSD").regime == Regime.RANGE  # type: ignore[union-attr]

    def test_reset(self) -> None:
        clf = RegimeClassifier()
        df = _make_indicator_df(adx=30.0)
        clf.classify("XAUUSD", df)
        clf.reset("XAUUSD")
        assert clf.get_previous("XAUUSD") is None
