# Regime classification — rule-based (P1) + HMM (P3).

from rtrade.regime.rules import RegimeClassifier, RegimeState

__all__ = ["RegimeClassifier", "RegimeState"]

# HMM detector imported lazily (requires hmmlearn).
# from rtrade.regime.hmm import HMMRegimeDetector
