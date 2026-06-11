"""Abstract strategy interface — Freqtrade callback pattern (PLAN §8.4, ADR-02).

One engine for all markets (crypto/XAUUSD/forex) with pluggable data providers.
Each concrete strategy implements the callback methods.

The interface is designed so that:
- populate_indicators() augments the DataFrame (pure).
- entry_signal() evaluates the LAST closed bar only.
- custom_entry_price() computes deterministic levels.
- confirm_signal() is a last sanity check before candidacy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd

from rtrade.core.constants import Action, Regime
from rtrade.signals.schemas import LevelSet


@dataclass(frozen=True, slots=True)
class EntryIntent:
    """Output of entry_signal(): direction + context, NO prices."""

    action: Action  # BUY or SELL
    reason: str  # short description of the setup


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    """Strategy parameters loaded from YAML (config/strategies/*.yaml)."""

    raw: dict[str, object]

    def get(self, dotted_key: str, default: object = None) -> object:
        """Get a nested key like 'trend.adx_min'."""
        parts = dotted_key.split(".")
        current: object = self.raw
        for part in parts:
            if not isinstance(current, dict):
                return default
            current = current.get(part, default)
        return current

    def get_int(self, key: str, default: int = 0) -> int:
        value = self.get(key, default)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int | float | str):
            return int(value)
        raise ValueError(f"strategy config {key!r} must be int-compatible")

    def get_float(self, key: str, default: float = 0.0) -> float:
        value = self.get(key, default)
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, int | float | str):
            return float(value)
        raise ValueError(f"strategy config {key!r} must be float-compatible")


class Strategy(ABC):
    """Base class for trading strategies (PLAN §8.4).

    Concrete implementations: S1TrendPullback (P1), S2RangeMR (P3).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique strategy name (e.g. 's1_trend_pullback')."""

    @property
    @abstractmethod
    def required_regime(self) -> Regime:
        """Strategy only activates in this regime."""

    @abstractmethod
    def populate_indicators(self, df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
        """Add strategy-specific indicators to the DataFrame.

        Called once per scan cycle. The returned DataFrame may have
        extra columns beyond what the indicator engine provides.
        """

    @abstractmethod
    def entry_signal(self, df: pd.DataFrame) -> EntryIntent | None:
        """Evaluate the LAST (closed) bar for an entry signal.

        Returns EntryIntent if setup conditions are met, None otherwise.
        MUST only use data up to and including the last bar (anti look-ahead).
        """

    @abstractmethod
    def custom_entry_price(self, df: pd.DataFrame, intent: EntryIntent) -> LevelSet:
        """Compute deterministic entry LIMIT + SL + TP levels.

        Prices are rounded to the instrument's pip_size by the caller.
        """

    def confirm_signal(self, df: pd.DataFrame, levels: LevelSet) -> bool:
        """Last sanity gate before candidacy (default: True).

        Override to add strategy-specific discard conditions (e.g., SL
        structure violation after ATR clamping).
        """
        return True
