# ADR-013: HMM Regime Detection Evaluation

**Status:** IMPLEMENTED — evaluation pending data  
**Date:** 2026-06-11  
**Context:** IMPLEMENTATION_PLAN §8.3 P3

## Decision

Implement HMM-based regime detection (`rtrade/regime/hmm.py`) as an
alternative to the rule-based classifier (`rtrade/regime/rules.py`).

HMM only REPLACES rule-based if backtest comparison shows higher
classification accuracy. Otherwise, rule-based remains the default.

## Implementation

- **Model:** `hmmlearn.GaussianHMM` with 3 states
- **Features:** log-return, ATR-normalized range, volume z-score
- **Training:** 2-year rolling window, retrain weekly (walk-forward)
- **State mapping:** Emission means → TREND/RANGE/CRISIS

## Evaluation Method

1. Train HMM on 2 years of data per instrument
2. Compare classifications with rule-based on the same period
3. Measure:
   - Agreement rate (%)
   - Per-regime accuracy
   - Strategy performance under each classifier

## Evaluation Results

> **TO BE FILLED** after running `compare_with_rule_based()` on real data.

| Metric | Rule-Based | HMM | Winner |
|--------|-----------|-----|--------|
| Agreement rate | — | — | — |
| TREND accuracy | — | — | — |
| RANGE accuracy | — | — | — |
| CRISIS detection | — | — | — |
| S1 expectancy | — | — | — |

## Decision Criteria

- HMM adopted if agreement rate > 70% AND does not miss any CRISIS event
- If HMM accuracy < rule-based → keep rule-based, document results
- Negative result is a valid result

## Consequences

- Both classifiers available; selectable via config
- HMM adds `hmmlearn` dependency (~200KB)
- Weekly retraining adds minor compute overhead
