# Strategy engine — Freqtrade-pattern callbacks (ADR-02).

from rtrade.strategies.base import EntryIntent, Strategy, StrategyConfig
from rtrade.strategies.s1_trend_pullback import S1TrendPullback
from rtrade.strategies.s2_range_mr import S2RangeMR
from rtrade.strategies.s3_mtf_scalper import S3MtfScalper
from rtrade.strategies.s4_smc_scalper import S4SmcScalper

__all__ = [
    "EntryIntent",
    "S1TrendPullback",
    "S2RangeMR",
    "S3MtfScalper",
    "S4SmcScalper",
    "Strategy",
    "StrategyConfig",
]

# Registry of all available strategies.
STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "s1_trend_pullback": S1TrendPullback,
    "s2_range_mr": S2RangeMR,
    "s3_mtf_scalper": S3MtfScalper,
    "s4_smc_scalper": S4SmcScalper,
}
