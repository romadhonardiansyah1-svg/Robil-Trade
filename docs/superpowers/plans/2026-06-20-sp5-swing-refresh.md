# SP-5: Swing Strategy Refresh (S1/S2 opt-in confirmations) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add researched, OPT-IN confirmation predicates (SuperTrend, Choppiness, ADX, Bollinger/Keltner touch, RSI divergence) as a new pure `rtrade.strategies.filters` module and wire them into S1 (trend-pullback) and S2 (range mean-reversion) behind default-OFF config keys, so existing S1/S2 behavior is byte-identical until a key is explicitly enabled.

**Architecture:** One new pure-function module `src/rtrade/strategies/filters.py` holds every predicate (no I/O, deterministic, operates on an OHLC(V) `pd.DataFrame`). S1 and S2 each gain a private `_passes_confirmations(df, action)` gate that is called *after* their existing entry logic and returns `True` trivially when no new key is enabled. New keys live in `config/strategies/s1_trend_pullback.yaml` and `config/strategies/s2_range_mr.yaml` under a new `confirmation:` section, all defaulting to `false`/disabled. Existing ADX columns from the indicator engine are reused; SuperTrend / Choppiness / Keltner are genuinely missing and are added as self-contained pure functions. The `STRATEGY_REGISTRY` names (`s1_trend_pullback`, `s2_range_mr`) are unchanged so SP-6 validation can reference them.

**Tech Stack:** Python 3.12, pandas, numpy, pandas_ta (already a dependency, only reused indirectly via reused engine columns), pytest. Pure synchronous functions — no async, no network.

## Global Constraints

(Copied verbatim from the design spec §5 — every task's requirements implicitly include this section.)

- **Signal-only** — no order/broker placement, ever.
- **Hard risk floors (config-loader enforced, never weakened):** GR-03 `rr_min ≥ 1.5`; GR-04 `sl_atr ∈ [0.5, 3.0]`; GR-05 `risk_per_trade_pct ≤ 2.0`. SP-5 adds NO hard-floor changes.
- **News blackout (GR-07)** applies to ALL timeframes incl. M5/M15.
- **Calendar fail-CLOSE:** `calendar.fail_open_when_stale = false`.
- **`llm.enabled = false`**; GI-5: no `model_construct` on the production path.
- **Warmup guarantee (P1-7):** abstain (`abstain_warmup`) until a full warmup window exists, per entry timeframe.
- **Determinism in tests:** `freezegun`/`respx`, no live network; integration tests skip when the live stack (DB/Redis/OANDA) is unreachable. SP-5 predicates are pure → tested on hand-built synthetic frames only.
- **Toolchain (run via venv):** `.venv\Scripts\python.exe -m <tool>`. Gate per phase: `ruff check src tests migrations` ; `ruff format src tests migrations` ; `mypy --strict src` ; `pytest tests -q`.
- **Commits:** message via `COMMIT_MSG_TMP.txt` + `git commit -F COMMIT_MSG_TMP.txt`, then delete the temp file. Before commit run `cmd /c 'if exist nul del "\\?\%CD%\nul"'`. No push unless explicitly requested.
- **Min trades floor:** `backtest.min_trades_for_validation ≥ 100` (never lower).
- **PRESERVATION discipline (SP-5 specific):** every new behavior is opt-in. With the shipped default config (all `confirmation.*_enabled: false`), S1 and S2 produce byte-identical output to today. This is asserted by keeping `tests/unit/test_s2_range_mr.py` and `tests/unit/test_harness.py` green unmodified, plus explicit default-OFF unit tests.

---

## File Structure

- Create: `src/rtrade/strategies/filters.py` — all pure confirmation predicates (SuperTrend, Choppiness, ADX gate, Bollinger/Keltner touch, RSI divergence) + shared `_true_range`/`_wilder_atr` helpers.
- Create: `tests/unit/test_filters.py` — deterministic synthetic-frame tests for every predicate.
- Modify: `src/rtrade/strategies/s1_trend_pullback.py` — import filters; store `confirmation.*` flags into `df.attrs` in `populate_indicators`; add `_passes_confirmations`/`_mtf_bias_ok`; AND the gate into `entry_signal`.
- Modify: `src/rtrade/strategies/s2_range_mr.py` — same pattern (Bollinger/Keltner/RSI-divergence/Choppiness gate).
- Modify: `config/strategies/s1_trend_pullback.yaml` — add default-OFF `confirmation:` section.
- Modify: `config/strategies/s2_range_mr.yaml` — add default-OFF `confirmation:` section.
- Create: `tests/unit/test_s1_confirmations.py` — S1 gate + preservation (default-OFF) tests.
- Create: `tests/unit/test_s2_confirmations.py` — S2 gate + preservation (default-OFF) tests.

**Reuse decision (justification):**
- `adx_ok` REUSES the existing engine column `adx` (computed in `indicators/engine.py::compute`) — it does not recompute ADX.
- `bollinger_touch` computes its own bands from `close` (rolling mean ± `std`·rolling-std, `ddof=0`). Rationale: the predicate is parameterized by `period`/`std`, must be a self-contained pure function testable on any synthetic frame without first running `engine.compute`, and avoids coupling to pandas_ta's version-dependent `BBU_/BBM_/BBL_` column naming (engine.py already works around that quirk).
- `supertrend`, `choppiness_index`, `keltner_touch` are genuinely absent from the engine → added as new pure functions. SuperTrend and Keltner share a Wilder-ATR helper (`_wilder_atr`) so ATR logic lives in one place.

---

## Task 1: `filters.py` — SuperTrend direction + flip helper

**Files:**
- Create: `src/rtrade/strategies/filters.py`
- Test: `tests/unit/test_filters.py`

**Interfaces:**
- Consumes: `pandas`, `numpy` only.
- Produces:
  - `_true_range(df: pd.DataFrame) -> pd.Series` (private helper).
  - `_wilder_atr(df: pd.DataFrame, period: int) -> pd.Series` (private helper; Wilder smoothing via `ewm(alpha=1/period, adjust=False)`).
  - `supertrend(df: pd.DataFrame, *, period: int = 10, multiplier: float = 3.0) -> pd.Series` — integer direction Series, `+1` uptrend / `-1` downtrend, same index as `df`.
  - `supertrend_flip(df: pd.DataFrame, *, period: int = 10, multiplier: float = 3.0) -> pd.Series` — bool Series, `True` where direction changed vs the previous bar (first bar `False`).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_filters.py
from __future__ import annotations

import numpy as np
import pandas as pd

from rtrade.strategies.filters import supertrend, supertrend_flip


def _ohlc(closes: list[float], *, spread: float = 0.5) -> pd.DataFrame:
    """Build an OHLC frame from a close path; high/low straddle close by `spread`."""
    close = pd.Series(closes, dtype="float64")
    return pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close + spread,
            "low": close - spread,
            "close": close,
        }
    )


def _down_then_up() -> pd.DataFrame:
    # 30 bars declining 130 -> 101 (step -1), then 15 bars rallying 103 -> 131 (step +2).
    down = [130.0 - i for i in range(30)]  # 130 .. 101
    up = [103.0 + 2.0 * i for i in range(15)]  # 103 .. 131
    return _ohlc(down + up)


def test_supertrend_direction_flips_down_to_up() -> None:
    df = _down_then_up()
    direction = supertrend(df, period=10, multiplier=3.0)
    assert int(direction.iloc[5]) == -1  # mid-downtrend
    assert int(direction.iloc[-1]) == 1  # after the rally
    assert set(direction.unique()) == {-1, 1}


def test_supertrend_flip_marks_the_reversal() -> None:
    df = _down_then_up()
    flips = supertrend_flip(df, period=10, multiplier=3.0)
    assert bool(flips.iloc[0]) is False  # first bar can never be a flip
    assert int(flips.iloc[1:].sum()) >= 1  # at least one -1 -> +1 reversal


def test_supertrend_steady_uptrend_is_all_plus_one_after_warmup() -> None:
    df = _ohlc([100.0 + i for i in range(40)])
    direction = supertrend(df, period=10, multiplier=3.0)
    assert int(direction.iloc[-1]) == 1
    assert not np.isnan(float(direction.iloc[-1]))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_filters.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rtrade.strategies.filters'`.

- [ ] **Step 3: Create `filters.py` with helpers + SuperTrend**

```python
# src/rtrade/strategies/filters.py
"""Pure confirmation predicates for swing strategies (SP-5).

Every function here is deterministic, side-effect free, and operates on an
OHLC(V) ``pd.DataFrame`` whose last row is the most recent CLOSED bar. These
predicates are opt-in confirmations layered on top of the S1/S2 entry logic;
they never relax a hard risk floor.

Reuse policy:
- ``adx_ok`` reuses the engine's existing ``adx`` column (no recompute).
- ``bollinger_touch`` computes its own bands so it stays self-contained.
- ``supertrend`` / ``choppiness_index`` / ``keltner_touch`` are not provided by
  ``indicators/engine.py`` and are implemented here.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd


def _true_range(df: pd.DataFrame) -> pd.Series:
    """Wilder True Range = max(H-L, |H-prevC|, |L-prevC|)."""
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    )
    return ranges.max(axis=1)


def _wilder_atr(df: pd.DataFrame, period: int) -> pd.Series:
    """ATR via Wilder smoothing (equivalent to ewm with alpha = 1/period)."""
    tr = _true_range(df)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def supertrend(df: pd.DataFrame, *, period: int = 10, multiplier: float = 3.0) -> pd.Series:
    """SuperTrend direction: +1 (uptrend) / -1 (downtrend), aligned to df.index.

    Standard formulation: basic bands = HL2 ± multiplier*ATR, carried forward,
    direction flips when close crosses the opposing final band.
    """
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    hl2 = (high + low) / 2.0
    atr = _wilder_atr(df, period)

    upper = (hl2 + multiplier * atr).tolist()
    lower = (hl2 - multiplier * atr).tolist()
    closes = df["close"].astype(float).tolist()
    n = len(closes)

    final_upper: list[float] = [0.0] * n
    final_lower: list[float] = [0.0] * n
    direction: list[int] = [1] * n
    if n > 0:
        final_upper[0] = upper[0]
        final_lower[0] = lower[0]

    for i in range(1, n):
        final_upper[i] = (
            upper[i]
            if (upper[i] < final_upper[i - 1] or closes[i - 1] > final_upper[i - 1])
            else final_upper[i - 1]
        )
        final_lower[i] = (
            lower[i]
            if (lower[i] > final_lower[i - 1] or closes[i - 1] < final_lower[i - 1])
            else final_lower[i - 1]
        )
        if closes[i] > final_upper[i - 1]:
            direction[i] = 1
        elif closes[i] < final_lower[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]

    return pd.Series(direction, index=df.index, dtype="int64")


def supertrend_flip(df: pd.DataFrame, *, period: int = 10, multiplier: float = 3.0) -> pd.Series:
    """True where SuperTrend direction changed vs the prior bar (first bar False)."""
    direction = supertrend(df, period=period, multiplier=multiplier)
    flipped = direction.ne(direction.shift(1)) & direction.shift(1).notna()
    return flipped.astype(bool)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_filters.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Gate + commit**

```
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src ; .venv\Scripts\python.exe -m pytest tests -q
```
Expected: ruff clean, mypy `Success`, pytest all green.

```
# write COMMIT_MSG_TMP.txt then:
cmd /c 'if exist nul del "\\?\%CD%\nul"'
git add src/rtrade/strategies/filters.py tests/unit/test_filters.py
git commit -F COMMIT_MSG_TMP.txt
del COMMIT_MSG_TMP.txt
```
Message: `feat(sp5): filters.py — SuperTrend direction + flip helper`

---

## Task 2: `choppiness_index` predicate

**Files:**
- Modify: `src/rtrade/strategies/filters.py` (add function)
- Test: `tests/unit/test_filters.py` (add tests)

**Interfaces:**
- Consumes: `_true_range` (Task 1).
- Produces: `choppiness_index(df: pd.DataFrame, *, period: int = 14) -> pd.Series` — Choppiness Index 0–100; low (<38.2) ≈ trending, high (>61.8) ≈ choppy/range. First `period-1` bars are NaN.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_filters.py  (append)
from rtrade.strategies.filters import choppiness_index  # add to existing imports


def test_choppiness_low_in_clean_trend() -> None:
    # Monotonic ramp = clean trend -> low CI.
    df = _ohlc([100.0 + i for i in range(50)])
    ci = choppiness_index(df, period=14)
    assert float(ci.iloc[-1]) < 38.2


def test_choppiness_high_in_oscillation() -> None:
    # Price ping-pongs 100/102 -> lots of travel, tiny net range -> high CI.
    closes = [100.0 if i % 2 == 0 else 102.0 for i in range(50)]
    df = _ohlc(closes)
    ci = choppiness_index(df, period=14)
    assert float(ci.iloc[-1]) > 61.8


def test_choppiness_warmup_is_nan() -> None:
    df = _ohlc([100.0 + i for i in range(50)])
    ci = choppiness_index(df, period=14)
    assert np.isnan(float(ci.iloc[5]))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_filters.py -q`
Expected: FAIL — `ImportError: cannot import name 'choppiness_index'`.

- [ ] **Step 3: Add to `filters.py`**

```python
def choppiness_index(df: pd.DataFrame, *, period: int = 14) -> pd.Series:
    """Choppiness Index (0–100). Low = trending, high = choppy/range-bound."""
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    tr_sum = _true_range(df).rolling(period).sum()
    highest = high.rolling(period).max()
    lowest = low.rolling(period).min()
    span = (highest - lowest).replace(0.0, np.nan)
    return 100.0 * np.log10(tr_sum / span) / np.log10(period)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_filters.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Gate + commit**

```
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src ; .venv\Scripts\python.exe -m pytest tests -q
```
Expected: clean + green.

```
cmd /c 'if exist nul del "\\?\%CD%\nul"'
git add src/rtrade/strategies/filters.py tests/unit/test_filters.py
git commit -F COMMIT_MSG_TMP.txt
del COMMIT_MSG_TMP.txt
```
Message: `feat(sp5): filters — Choppiness Index regime/whipsaw predicate`

---

## Task 3: `adx_ok` predicate (reuses engine `adx` column)

**Files:**
- Modify: `src/rtrade/strategies/filters.py` (add function)
- Test: `tests/unit/test_filters.py` (add tests)

**Interfaces:**
- Consumes: the existing engine column `adx` (from `indicators/engine.py::compute`).
- Produces: `adx_ok(df: pd.DataFrame, *, threshold: float) -> bool` — `True` iff the last bar's `adx ≥ threshold`. Missing column / empty frame / NaN → `False`. Does NOT recompute ADX.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_filters.py  (append)
from rtrade.strategies.filters import adx_ok  # add to existing imports


def _adx_df(value: float) -> pd.DataFrame:
    df = _ohlc([100.0, 101.0, 102.0])
    df["adx"] = [10.0, 20.0, value]
    return df


def test_adx_ok_true_above_threshold() -> None:
    assert adx_ok(_adx_df(30.0), threshold=25.0) is True


def test_adx_ok_false_below_threshold() -> None:
    assert adx_ok(_adx_df(20.0), threshold=25.0) is False


def test_adx_ok_false_when_column_missing() -> None:
    assert adx_ok(_ohlc([100.0, 101.0]), threshold=25.0) is False


def test_adx_ok_false_when_nan() -> None:
    assert adx_ok(_adx_df(float("nan")), threshold=25.0) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_filters.py -q`
Expected: FAIL — `ImportError: cannot import name 'adx_ok'`.

- [ ] **Step 3: Add to `filters.py`**

```python
def adx_ok(df: pd.DataFrame, *, threshold: float) -> bool:
    """True iff last-bar ADX (engine column) is >= threshold. Reuses, never recomputes."""
    if df.empty or "adx" not in df.columns:
        return False
    value = df["adx"].iloc[-1]
    if value is None:
        return False
    fvalue = float(value)
    if np.isnan(fvalue):
        return False
    return fvalue >= threshold
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_filters.py -q`
Expected: PASS (10 passed).

- [ ] **Step 5: Gate + commit**

```
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src ; .venv\Scripts\python.exe -m pytest tests -q
```
Expected: clean + green.

```
cmd /c 'if exist nul del "\\?\%CD%\nul"'
git add src/rtrade/strategies/filters.py tests/unit/test_filters.py
git commit -F COMMIT_MSG_TMP.txt
del COMMIT_MSG_TMP.txt
```
Message: `feat(sp5): filters — adx_ok gate reusing engine ADX column`

---

## Task 4: `bollinger_touch` predicate

**Files:**
- Modify: `src/rtrade/strategies/filters.py` (add function)
- Test: `tests/unit/test_filters.py` (add tests)

**Interfaces:**
- Consumes: `pandas` only.
- Produces: `bollinger_touch(df: pd.DataFrame, *, period: int = 20, std: float = 2.0, side: Literal["upper", "lower"]) -> bool` — bands computed from `close` (rolling mean ± `std`·rolling-std, `ddof=0`). `side="lower"` → `True` iff last `low ≤ lower band`; `side="upper"` → `True` iff last `high ≥ upper band`. Frames shorter than `period` → `False`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_filters.py  (append)
from rtrade.strategies.filters import bollinger_touch  # add to existing imports


def _bb_frame() -> pd.DataFrame:
    # 20 closes alternating 95/105 -> mean=100, population std (ddof=0)=5 exactly.
    # With std mult 2.0 -> lower=90, upper=110.
    closes = [95.0 if i % 2 == 0 else 105.0 for i in range(20)]
    close = pd.Series(closes, dtype="float64")
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
        }
    )


def test_bollinger_touch_lower_true() -> None:
    df = _bb_frame()
    df.iloc[-1, df.columns.get_loc("low")] = 89.0  # below lower band (90)
    assert bollinger_touch(df, period=20, std=2.0, side="lower") is True


def test_bollinger_touch_lower_false() -> None:
    df = _bb_frame()
    df.iloc[-1, df.columns.get_loc("low")] = 95.0  # above lower band (90)
    assert bollinger_touch(df, period=20, std=2.0, side="lower") is False


def test_bollinger_touch_upper_true() -> None:
    df = _bb_frame()
    df.iloc[-1, df.columns.get_loc("high")] = 111.0  # above upper band (110)
    assert bollinger_touch(df, period=20, std=2.0, side="upper") is True


def test_bollinger_touch_too_short_false() -> None:
    df = _ohlc([100.0, 101.0, 102.0])
    assert bollinger_touch(df, period=20, std=2.0, side="lower") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_filters.py -q`
Expected: FAIL — `ImportError: cannot import name 'bollinger_touch'`.

- [ ] **Step 3: Add to `filters.py`**

```python
def bollinger_touch(
    df: pd.DataFrame,
    *,
    period: int = 20,
    std: float = 2.0,
    side: Literal["upper", "lower"],
) -> bool:
    """True iff the last bar pierces the requested Bollinger band.

    Bands are self-computed from close: SMA(period) ± std * rolling-std(ddof=0).
    """
    if len(df) < period:
        return False
    close = df["close"].astype(float)
    sma = close.rolling(period).mean()
    sd = close.rolling(period).std(ddof=0)
    last = df.iloc[-1]
    if side == "lower":
        lower = float(sma.iloc[-1] - std * sd.iloc[-1])
        return float(last["low"]) <= lower
    upper = float(sma.iloc[-1] + std * sd.iloc[-1])
    return float(last["high"]) >= upper
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_filters.py -q`
Expected: PASS (14 passed).

- [ ] **Step 5: Gate + commit**

```
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src ; .venv\Scripts\python.exe -m pytest tests -q
```
Expected: clean + green.

```
cmd /c 'if exist nul del "\\?\%CD%\nul"'
git add src/rtrade/strategies/filters.py tests/unit/test_filters.py
git commit -F COMMIT_MSG_TMP.txt
del COMMIT_MSG_TMP.txt
```
Message: `feat(sp5): filters — bollinger_touch band-pierce predicate`

---

## Task 5: `keltner_touch` predicate

**Files:**
- Modify: `src/rtrade/strategies/filters.py` (add function)
- Test: `tests/unit/test_filters.py` (add tests)

**Interfaces:**
- Consumes: `_wilder_atr` (Task 1).
- Produces: `keltner_touch(df: pd.DataFrame, *, period: int = 20, multiplier: float = 1.5, side: Literal["upper", "lower"]) -> bool` — channel = EMA(period, `adjust=False`) ± `multiplier`·ATR(period). `side="lower"` → last `low ≤ lower`; `side="upper"` → last `high ≥ upper`. Frames shorter than `period` → `False`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_filters.py  (append)
from rtrade.strategies.filters import keltner_touch  # add to existing imports


def _kc_frame() -> pd.DataFrame:
    # close flat at 100, high/low ±2 -> TR=4 every bar -> ATR=4, EMA=100.
    # mult 1.5 -> lower=94, upper=106.
    close = pd.Series([100.0] * 20, dtype="float64")
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
        }
    )


def test_keltner_touch_lower_true() -> None:
    df = _kc_frame()
    df.iloc[-1, df.columns.get_loc("low")] = 93.0  # below lower (94)
    assert keltner_touch(df, period=20, multiplier=1.5, side="lower") is True


def test_keltner_touch_lower_false() -> None:
    df = _kc_frame()
    df.iloc[-1, df.columns.get_loc("low")] = 96.0  # above lower (94)
    assert keltner_touch(df, period=20, multiplier=1.5, side="lower") is False


def test_keltner_touch_upper_true() -> None:
    df = _kc_frame()
    df.iloc[-1, df.columns.get_loc("high")] = 107.0  # above upper (106)
    assert keltner_touch(df, period=20, multiplier=1.5, side="upper") is True


def test_keltner_touch_too_short_false() -> None:
    df = _ohlc([100.0, 101.0])
    assert keltner_touch(df, period=20, multiplier=1.5, side="lower") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_filters.py -q`
Expected: FAIL — `ImportError: cannot import name 'keltner_touch'`.

- [ ] **Step 3: Add to `filters.py`**

```python
def keltner_touch(
    df: pd.DataFrame,
    *,
    period: int = 20,
    multiplier: float = 1.5,
    side: Literal["upper", "lower"],
) -> bool:
    """True iff the last bar pierces the requested Keltner channel band.

    Channel = EMA(period) ± multiplier * ATR(period), ATR via Wilder smoothing.
    """
    if len(df) < period:
        return False
    close = df["close"].astype(float)
    ema = close.ewm(span=period, adjust=False).mean()
    atr = _wilder_atr(df, period)
    last = df.iloc[-1]
    if side == "lower":
        lower = float(ema.iloc[-1] - multiplier * atr.iloc[-1])
        return float(last["low"]) <= lower
    upper = float(ema.iloc[-1] + multiplier * atr.iloc[-1])
    return float(last["high"]) >= upper
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_filters.py -q`
Expected: PASS (18 passed).

- [ ] **Step 5: Gate + commit**

```
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src ; .venv\Scripts\python.exe -m pytest tests -q
```
Expected: clean + green.

```
cmd /c 'if exist nul del "\\?\%CD%\nul"'
git add src/rtrade/strategies/filters.py tests/unit/test_filters.py
git commit -F COMMIT_MSG_TMP.txt
del COMMIT_MSG_TMP.txt
```
Message: `feat(sp5): filters — keltner_touch channel-pierce predicate`

---

## Task 6: `rsi_divergence` predicate

**Files:**
- Modify: `src/rtrade/strategies/filters.py` (add function)
- Test: `tests/unit/test_filters.py` (add tests)

**Interfaces:**
- Consumes: the existing engine column `rsi` plus `high`/`low`.
- Produces: `rsi_divergence(df: pd.DataFrame, *, lookback: int) -> Literal["bullish", "bearish", "none"]`. Splits the last `lookback` bars into an older and a recent half. **Bullish** = recent half makes a lower `low` than the older half while RSI at that low is *higher* (price lower-low, momentum higher-low). **Bearish** = recent half makes a higher `high` while RSI at that high is *lower*. Otherwise (or if `rsi` missing / too few bars / both signals) → `"none"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_filters.py  (append)
from rtrade.strategies.filters import rsi_divergence  # add to existing imports


def _div_frame(
    lows: list[float], highs: list[float], rsis: list[float]
) -> pd.DataFrame:
    n = len(lows)
    close = pd.Series([100.0] * n, dtype="float64")
    return pd.DataFrame(
        {
            "open": close,
            "high": pd.Series(highs, dtype="float64"),
            "low": pd.Series(lows, dtype="float64"),
            "close": close,
            "rsi": pd.Series(rsis, dtype="float64"),
        }
    )


def test_rsi_bullish_divergence() -> None:
    # older-half min low = 100 @ idx5 (rsi 30); recent-half min low = 98 @ idx15 (rsi 35).
    lows = [105.0] * 20
    lows[5] = 100.0
    lows[15] = 98.0  # lower low
    highs = [110.0] * 20
    rsis = [50.0] * 20
    rsis[5] = 30.0
    rsis[15] = 35.0  # higher low in RSI
    assert rsi_divergence(_div_frame(lows, highs, rsis), lookback=20) == "bullish"


def test_rsi_bearish_divergence() -> None:
    # older-half max high = 110 @ idx5 (rsi 70); recent-half max high = 112 @ idx15 (rsi 65).
    highs = [105.0] * 20
    highs[5] = 110.0
    highs[15] = 112.0  # higher high
    lows = [100.0] * 20
    rsis = [50.0] * 20
    rsis[5] = 70.0
    rsis[15] = 65.0  # lower high in RSI
    assert rsi_divergence(_div_frame(lows, highs, rsis), lookback=20) == "bearish"


def test_rsi_no_divergence_when_flat() -> None:
    lows = [100.0] * 20
    highs = [110.0] * 20
    rsis = [50.0] * 20
    assert rsi_divergence(_div_frame(lows, highs, rsis), lookback=20) == "none"


def test_rsi_divergence_missing_column_is_none() -> None:
    df = _ohlc([100.0 + i for i in range(20)])
    assert rsi_divergence(df, lookback=20) == "none"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_filters.py -q`
Expected: FAIL — `ImportError: cannot import name 'rsi_divergence'`.

- [ ] **Step 3: Add to `filters.py`**

```python
def rsi_divergence(df: pd.DataFrame, *, lookback: int) -> Literal["bullish", "bearish", "none"]:
    """Detect RSI/price divergence over the last `lookback` bars.

    Bullish: recent-half lower price-low + higher RSI-low.
    Bearish: recent-half higher price-high + lower RSI-high.
    """
    if "rsi" not in df.columns or len(df) < lookback or lookback < 2:
        return "none"
    window = df.iloc[-lookback:]
    half = lookback // 2
    older = window.iloc[:half]
    recent = window.iloc[half:]

    older_low_idx = older["low"].astype(float).idxmin()
    recent_low_idx = recent["low"].astype(float).idxmin()
    bullish = float(recent.loc[recent_low_idx, "low"]) < float(
        older.loc[older_low_idx, "low"]
    ) and float(recent.loc[recent_low_idx, "rsi"]) > float(older.loc[older_low_idx, "rsi"])

    older_high_idx = older["high"].astype(float).idxmax()
    recent_high_idx = recent["high"].astype(float).idxmax()
    bearish = float(recent.loc[recent_high_idx, "high"]) > float(
        older.loc[older_high_idx, "high"]
    ) and float(recent.loc[recent_high_idx, "rsi"]) < float(older.loc[older_high_idx, "rsi"])

    if bullish and not bearish:
        return "bullish"
    if bearish and not bullish:
        return "bearish"
    return "none"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_filters.py -q`
Expected: PASS (22 passed).

- [ ] **Step 5: Gate + commit**

```
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src ; .venv\Scripts\python.exe -m pytest tests -q
```
Expected: clean + green.

```
cmd /c 'if exist nul del "\\?\%CD%\nul"'
git add src/rtrade/strategies/filters.py tests/unit/test_filters.py
git commit -F COMMIT_MSG_TMP.txt
del COMMIT_MSG_TMP.txt
```
Message: `feat(sp5): filters — rsi_divergence predicate`

---

## Task 7: S1 integration — opt-in SuperTrend + ADX/Choppiness + MTF EMA bias gate

**Files:**
- Modify: `src/rtrade/strategies/s1_trend_pullback.py`
- Modify: `config/strategies/s1_trend_pullback.yaml`
- Test: `tests/unit/test_s1_confirmations.py`

**Interfaces:**
- Consumes: `supertrend`, `adx_ok`, `choppiness_index` (Tasks 1–3).
- Produces:
  - New `df.attrs` keys set in `populate_indicators`: `s1_st_enabled`, `s1_st_period`, `s1_st_mult`, `s1_adx_filter_enabled`, `s1_adx_threshold`, `s1_chop_enabled`, `s1_chop_period`, `s1_chop_max`, `s1_mtf_enabled`.
  - `S1TrendPullback._passes_confirmations(df: pd.DataFrame, action: Action) -> bool` — returns `True` when no flag enabled (preservation).
  - `S1TrendPullback._mtf_bias_ok(df: pd.DataFrame, action: Action) -> bool` — reads optional `df.attrs["s1_htf_bias"]` (`"UP"`/`"DOWN"`/`None`), injected by the SP-2 MTF engine; permissive (returns `True`) when absent.
- **Preservation contract:** with the shipped default YAML all four `*_enabled` flags are `false` → `_passes_confirmations` returns `True` → `entry_signal` is byte-identical to today. Asserted by keeping `tests/unit/test_harness.py` green unmodified.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_s1_confirmations.py
from __future__ import annotations

import pandas as pd

from rtrade.core.constants import Action
from rtrade.strategies.base import StrategyConfig
from rtrade.strategies.s1_trend_pullback import S1TrendPullback


def _ohlc(closes: list[float], *, spread: float = 0.5) -> pd.DataFrame:
    close = pd.Series(closes, dtype="float64")
    df = pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close + spread,
            "low": close - spread,
            "close": close,
        }
    )
    # EMA columns required by populate_indicators.
    df["ema21"] = close
    df["ema50"] = close - 1.0
    df["ema200"] = close - 5.0
    df["adx"] = 30.0
    df["rsi"] = 50.0
    return df


def _downtrend() -> pd.DataFrame:
    return _ohlc([130.0 - i for i in range(40)])


def _uptrend() -> pd.DataFrame:
    return _ohlc([100.0 + i for i in range(40)])


def test_confirmations_default_off_returns_true() -> None:
    s1 = S1TrendPullback()
    df = _uptrend()  # no confirmation attrs set
    assert s1._passes_confirmations(df, Action.BUY) is True


def test_populate_indicators_defaults_all_disabled() -> None:
    s1 = S1TrendPullback()
    df = s1.populate_indicators(_uptrend(), StrategyConfig(raw={}))
    assert df.attrs["s1_st_enabled"] is False
    assert df.attrs["s1_adx_filter_enabled"] is False
    assert df.attrs["s1_chop_enabled"] is False
    assert df.attrs["s1_mtf_enabled"] is False


def test_supertrend_gate_blocks_buy_in_downtrend() -> None:
    s1 = S1TrendPullback()
    df = _downtrend()
    df.attrs["s1_st_enabled"] = True
    df.attrs["s1_st_period"] = 10
    df.attrs["s1_st_mult"] = 3.0
    assert s1._passes_confirmations(df, Action.BUY) is False


def test_supertrend_gate_allows_buy_in_uptrend() -> None:
    s1 = S1TrendPullback()
    df = _uptrend()
    df.attrs["s1_st_enabled"] = True
    df.attrs["s1_st_period"] = 10
    df.attrs["s1_st_mult"] = 3.0
    assert s1._passes_confirmations(df, Action.BUY) is True


def test_adx_gate_blocks_when_below_threshold() -> None:
    s1 = S1TrendPullback()
    df = _uptrend()
    df["adx"] = 18.0
    df.attrs["s1_adx_filter_enabled"] = True
    df.attrs["s1_adx_threshold"] = 25.0
    assert s1._passes_confirmations(df, Action.BUY) is False


def test_mtf_bias_blocks_misaligned_and_allows_aligned() -> None:
    s1 = S1TrendPullback()
    df = _uptrend()
    df.attrs["s1_mtf_enabled"] = True
    df.attrs["s1_htf_bias"] = "DOWN"
    assert s1._passes_confirmations(df, Action.BUY) is False
    df.attrs["s1_htf_bias"] = "UP"
    assert s1._passes_confirmations(df, Action.BUY) is True


def test_mtf_bias_permissive_when_absent() -> None:
    s1 = S1TrendPullback()
    df = _uptrend()
    df.attrs["s1_mtf_enabled"] = True  # no s1_htf_bias injected
    assert s1._passes_confirmations(df, Action.BUY) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_s1_confirmations.py -q`
Expected: FAIL — `AttributeError: 'S1TrendPullback' object has no attribute '_passes_confirmations'`.

- [ ] **Step 3: Add the filters import to `s1_trend_pullback.py`**

In the import block, after `from rtrade.indicators.structure import detect_swing_points`, add:

```python
from rtrade.strategies.filters import adx_ok, choppiness_index, supertrend
```

- [ ] **Step 4: Store confirmation flags in `populate_indicators`**

In `S1TrendPullback.populate_indicators`, immediately before `# Pullback zone boundaries.`, add:

```python
        # SP-5 opt-in confirmations (default OFF → preserves current behavior).
        df.attrs["s1_st_enabled"] = bool(cfg.get("confirmation.supertrend_enabled", False))
        df.attrs["s1_st_period"] = cfg.get_int("confirmation.supertrend_period", 10)
        df.attrs["s1_st_mult"] = cfg.get_float("confirmation.supertrend_multiplier", 3.0)
        df.attrs["s1_adx_filter_enabled"] = bool(
            cfg.get("confirmation.adx_filter_enabled", False)
        )
        df.attrs["s1_adx_threshold"] = cfg.get_float("confirmation.adx_threshold", 25.0)
        df.attrs["s1_chop_enabled"] = bool(cfg.get("confirmation.choppiness_enabled", False))
        df.attrs["s1_chop_period"] = cfg.get_int("confirmation.choppiness_period", 14)
        df.attrs["s1_chop_max"] = cfg.get_float("confirmation.choppiness_max", 38.2)
        df.attrs["s1_mtf_enabled"] = bool(cfg.get("confirmation.mtf_ema_bias_enabled", False))
```

- [ ] **Step 5: AND the gate into `entry_signal`**

Replace the condition inside the loop in `entry_signal`:

```python
            if self._check_trend_filter(df, action) and self._check_pullback_setup(df, action):
```

with:

```python
            if (
                self._check_trend_filter(df, action)
                and self._check_pullback_setup(df, action)
                and self._passes_confirmations(df, action)
            ):
```

- [ ] **Step 6: Add the gate methods**

Add these methods to `S1TrendPullback` (e.g. directly after `_check_pullback_setup`):

```python
    def _passes_confirmations(self, df: pd.DataFrame, action: Action) -> bool:
        """SP-5 opt-in confirmations. Returns True when nothing is enabled."""
        if bool(df.attrs.get("s1_st_enabled", False)):
            direction = supertrend(
                df,
                period=int(df.attrs.get("s1_st_period", 10)),
                multiplier=float(df.attrs.get("s1_st_mult", 3.0)),
            )
            want = 1 if action == Action.BUY else -1
            if int(direction.iloc[-1]) != want:
                return False

        if bool(df.attrs.get("s1_adx_filter_enabled", False)) and not adx_ok(
            df, threshold=float(df.attrs.get("s1_adx_threshold", 25.0))
        ):
            return False

        if bool(df.attrs.get("s1_chop_enabled", False)):
            ci = choppiness_index(df, period=int(df.attrs.get("s1_chop_period", 14)))
            last_ci = float(ci.iloc[-1])
            if np.isnan(last_ci) or last_ci > float(df.attrs.get("s1_chop_max", 38.2)):
                return False

        if bool(df.attrs.get("s1_mtf_enabled", False)) and not self._mtf_bias_ok(df, action):
            return False

        return True

    @staticmethod
    def _mtf_bias_ok(df: pd.DataFrame, action: Action) -> bool:
        """Higher-TF EMA bias gate. Permissive when no bias is injected (SP-2 fills it)."""
        bias = df.attrs.get("s1_htf_bias")
        if bias is None:
            return True
        if action == Action.BUY:
            return bool(bias == "UP")
        return bool(bias == "DOWN")
```

(`np` is already imported in `s1_trend_pullback.py`.)

- [ ] **Step 7: Add default-OFF `confirmation:` section to the YAML**

In `config/strategies/s1_trend_pullback.yaml`, after the `levels:` block, add:

```yaml
# SP-5 opt-in confirmations. ALL default OFF — enabling any requires a fresh
# walk-forward backtest pass (SP-6) before going live. Disabled = behavior
# identical to the original S1.
confirmation:
  supertrend_enabled: false
  supertrend_period: 10
  supertrend_multiplier: 3.0
  adx_filter_enabled: false
  adx_threshold: 25.0
  choppiness_enabled: false
  choppiness_period: 14
  choppiness_max: 38.2          # CI below this = trending enough to trade
  mtf_ema_bias_enabled: false   # HTF bias injected via df.attrs["s1_htf_bias"] by SP-2
```

- [ ] **Step 8: Run tests + gate**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_s1_confirmations.py tests/unit/test_harness.py -q`
Expected: PASS — new gate tests pass AND `test_harness.py` (real-YAML S1 preservation) stays green.

Then the full gate:

```
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src ; .venv\Scripts\python.exe -m pytest tests -q
```
Expected: clean + all green.

- [ ] **Step 9: Commit**

```
cmd /c 'if exist nul del "\\?\%CD%\nul"'
git add src/rtrade/strategies/s1_trend_pullback.py config/strategies/s1_trend_pullback.yaml tests/unit/test_s1_confirmations.py
git commit -F COMMIT_MSG_TMP.txt
del COMMIT_MSG_TMP.txt
```
Message: `feat(sp5): S1 opt-in SuperTrend/ADX/Choppiness + MTF EMA bias gate (default off)`

---

## Task 8: S2 integration — opt-in Bollinger/Keltner + RSI-divergence + Choppiness gate

**Files:**
- Modify: `src/rtrade/strategies/s2_range_mr.py`
- Modify: `config/strategies/s2_range_mr.yaml`
- Test: `tests/unit/test_s2_confirmations.py`

**Interfaces:**
- Consumes: `bollinger_touch`, `keltner_touch`, `rsi_divergence`, `choppiness_index` (Tasks 2,4,5,6).
- Produces:
  - New `df.attrs` keys set in `populate_indicators`: `s2_bb_enabled`, `s2_bb_period`, `s2_bb_std`, `s2_kc_enabled`, `s2_kc_period`, `s2_kc_mult`, `s2_rsidiv_enabled`, `s2_rsidiv_lookback`, `s2_chop_enabled`, `s2_chop_period`, `s2_chop_min`.
  - `S2RangeMR._passes_confirmations(df: pd.DataFrame, action: Action) -> bool` — returns `True` when no flag enabled (preservation).
- **Preservation contract:** with the shipped default YAML all `*_enabled` flags are `false` → `_passes_confirmations` returns `True` → `entry_signal` is byte-identical to today. Asserted by keeping `tests/unit/test_s2_range_mr.py` green unmodified.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_s2_confirmations.py
from __future__ import annotations

import pandas as pd

from rtrade.core.constants import Action
from rtrade.strategies.base import StrategyConfig
from rtrade.strategies.s2_range_mr import S2RangeMR


def _bb_frame() -> pd.DataFrame:
    # mean=100, population std=5 -> BB(20,2) lower=90 upper=110.
    closes = [95.0 if i % 2 == 0 else 105.0 for i in range(20)]
    close = pd.Series(closes, dtype="float64")
    df = pd.DataFrame(
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "rsi": pd.Series([50.0] * 20, dtype="float64"),
        }
    )
    return df


def test_confirmations_default_off_returns_true() -> None:
    s2 = S2RangeMR()
    assert s2._passes_confirmations(_bb_frame(), Action.BUY) is True


def test_populate_indicators_defaults_all_disabled() -> None:
    s2 = S2RangeMR()
    df = s2.populate_indicators(_bb_frame(), StrategyConfig(raw={"range": {"band_lookback": 20}}))
    assert df.attrs["s2_bb_enabled"] is False
    assert df.attrs["s2_kc_enabled"] is False
    assert df.attrs["s2_rsidiv_enabled"] is False
    assert df.attrs["s2_chop_enabled"] is False


def test_bollinger_gate_blocks_without_touch() -> None:
    s2 = S2RangeMR()
    df = _bb_frame()
    df.iloc[-1, df.columns.get_loc("low")] = 95.0  # above lower band (90) -> no touch
    df.attrs["s2_bb_enabled"] = True
    df.attrs["s2_bb_period"] = 20
    df.attrs["s2_bb_std"] = 2.0
    assert s2._passes_confirmations(df, Action.BUY) is False


def test_bollinger_gate_allows_on_touch() -> None:
    s2 = S2RangeMR()
    df = _bb_frame()
    df.iloc[-1, df.columns.get_loc("low")] = 89.0  # below lower band (90) -> touch
    df.attrs["s2_bb_enabled"] = True
    df.attrs["s2_bb_period"] = 20
    df.attrs["s2_bb_std"] = 2.0
    assert s2._passes_confirmations(df, Action.BUY) is True


def test_rsi_divergence_gate_requires_bullish_for_buy() -> None:
    s2 = S2RangeMR()
    df = _bb_frame()
    # Build a bullish divergence: recent lower low @ higher RSI.
    df["low"] = [105.0] * 20
    df.iloc[5, df.columns.get_loc("low")] = 100.0
    df.iloc[15, df.columns.get_loc("low")] = 98.0
    df["rsi"] = [50.0] * 20
    df.iloc[5, df.columns.get_loc("rsi")] = 30.0
    df.iloc[15, df.columns.get_loc("rsi")] = 35.0
    df.attrs["s2_rsidiv_enabled"] = True
    df.attrs["s2_rsidiv_lookback"] = 20
    assert s2._passes_confirmations(df, Action.BUY) is True
    # A SELL would need bearish divergence -> blocked here.
    assert s2._passes_confirmations(df, Action.SELL) is False


def test_choppiness_gate_requires_range() -> None:
    s2 = S2RangeMR()
    # Clean trend -> low CI -> below choppiness_min -> blocked (not a real range).
    close = pd.Series([100.0 + i for i in range(50)], dtype="float64")
    df = pd.DataFrame(
        {
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "rsi": pd.Series([50.0] * 50, dtype="float64"),
        }
    )
    df.attrs["s2_chop_enabled"] = True
    df.attrs["s2_chop_period"] = 14
    df.attrs["s2_chop_min"] = 61.8
    assert s2._passes_confirmations(df, Action.BUY) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_s2_confirmations.py -q`
Expected: FAIL — `AttributeError: 'S2RangeMR' object has no attribute '_passes_confirmations'`.

- [ ] **Step 3: Update imports in `s2_range_mr.py`**

Replace the imports block:

```python
from __future__ import annotations

import pandas as pd

from rtrade.core.constants import Action, Regime
from rtrade.signals.schemas import LevelSet
from rtrade.strategies.base import EntryIntent, Strategy, StrategyConfig
```

with:

```python
from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

from rtrade.core.constants import Action, Regime
from rtrade.signals.schemas import LevelSet
from rtrade.strategies.base import EntryIntent, Strategy, StrategyConfig
from rtrade.strategies.filters import (
    bollinger_touch,
    choppiness_index,
    keltner_touch,
    rsi_divergence,
)
```

- [ ] **Step 4: Store confirmation flags in `populate_indicators`**

In `S2RangeMR.populate_indicators`, immediately before `# Donchian channel for band detection.`, add:

```python
        # SP-5 opt-in confirmations (default OFF → preserves current behavior).
        df.attrs["s2_bb_enabled"] = bool(cfg.get("confirmation.bollinger_touch_enabled", False))
        df.attrs["s2_bb_period"] = cfg.get_int("confirmation.bb_period", 20)
        df.attrs["s2_bb_std"] = cfg.get_float("confirmation.bb_std", 2.0)
        df.attrs["s2_kc_enabled"] = bool(cfg.get("confirmation.keltner_touch_enabled", False))
        df.attrs["s2_kc_period"] = cfg.get_int("confirmation.keltner_period", 20)
        df.attrs["s2_kc_mult"] = cfg.get_float("confirmation.keltner_multiplier", 1.5)
        df.attrs["s2_rsidiv_enabled"] = bool(
            cfg.get("confirmation.rsi_divergence_enabled", False)
        )
        df.attrs["s2_rsidiv_lookback"] = cfg.get_int("confirmation.rsi_divergence_lookback", 20)
        df.attrs["s2_chop_enabled"] = bool(cfg.get("confirmation.choppiness_enabled", False))
        df.attrs["s2_chop_period"] = cfg.get_int("confirmation.choppiness_period", 14)
        df.attrs["s2_chop_min"] = cfg.get_float("confirmation.choppiness_min", 61.8)
```

- [ ] **Step 5: AND the gate into `entry_signal`**

Replace the loop body in `entry_signal`:

```python
        for action, direction in [
            (Action.BUY, "LONG"),
            (Action.SELL, "SHORT"),
        ]:
            if self._check_entry(df, action):
                return EntryIntent(
                    action=action,
                    reason=f"S2 Range Mean-Reversion {direction} at band edge",
                )
```

with:

```python
        for action, direction in [
            (Action.BUY, "LONG"),
            (Action.SELL, "SHORT"),
        ]:
            if self._check_entry(df, action) and self._passes_confirmations(df, action):
                return EntryIntent(
                    action=action,
                    reason=f"S2 Range Mean-Reversion {direction} at band edge",
                )
```

- [ ] **Step 6: Add the gate method**

Add this method to `S2RangeMR` (e.g. directly after `_check_entry`):

```python
    def _passes_confirmations(self, df: pd.DataFrame, action: Action) -> bool:
        """SP-5 opt-in confirmations. Returns True when nothing is enabled."""
        side: Literal["lower", "upper"] = "lower" if action == Action.BUY else "upper"

        if bool(df.attrs.get("s2_bb_enabled", False)) and not bollinger_touch(
            df,
            period=int(df.attrs.get("s2_bb_period", 20)),
            std=float(df.attrs.get("s2_bb_std", 2.0)),
            side=side,
        ):
            return False

        if bool(df.attrs.get("s2_kc_enabled", False)) and not keltner_touch(
            df,
            period=int(df.attrs.get("s2_kc_period", 20)),
            multiplier=float(df.attrs.get("s2_kc_mult", 1.5)),
            side=side,
        ):
            return False

        if bool(df.attrs.get("s2_rsidiv_enabled", False)):
            div = rsi_divergence(df, lookback=int(df.attrs.get("s2_rsidiv_lookback", 20)))
            want = "bullish" if action == Action.BUY else "bearish"
            if div != want:
                return False

        if bool(df.attrs.get("s2_chop_enabled", False)):
            ci = choppiness_index(df, period=int(df.attrs.get("s2_chop_period", 14)))
            last_ci = float(ci.iloc[-1])
            if np.isnan(last_ci) or last_ci < float(df.attrs.get("s2_chop_min", 61.8)):
                return False

        return True
```

- [ ] **Step 7: Add default-OFF `confirmation:` section to the YAML**

In `config/strategies/s2_range_mr.yaml`, after the `levels:` block (before `news:`), add:

```yaml
# SP-5 opt-in confirmations. ALL default OFF — enabling any requires a fresh
# walk-forward backtest pass (SP-6) before going live. Disabled = behavior
# identical to the original S2.
confirmation:
  bollinger_touch_enabled: false
  bb_period: 20
  bb_std: 2.0
  keltner_touch_enabled: false
  keltner_period: 20
  keltner_multiplier: 1.5
  rsi_divergence_enabled: false
  rsi_divergence_lookback: 20
  choppiness_enabled: false
  choppiness_period: 14
  choppiness_min: 61.8          # CI above this = choppy enough to be a real range
```

- [ ] **Step 8: Run tests + gate**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_s2_confirmations.py tests/unit/test_s2_range_mr.py -q`
Expected: PASS — new gate tests pass AND `test_s2_range_mr.py` (preservation) stays green unmodified.

Then the full gate:

```
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src ; .venv\Scripts\python.exe -m pytest tests -q
```
Expected: clean + all green.

- [ ] **Step 9: Commit**

```
cmd /c 'if exist nul del "\\?\%CD%\nul"'
git add src/rtrade/strategies/s2_range_mr.py config/strategies/s2_range_mr.yaml tests/unit/test_s2_confirmations.py
git commit -F COMMIT_MSG_TMP.txt
del COMMIT_MSG_TMP.txt
```
Message: `feat(sp5): S2 opt-in Bollinger/Keltner/RSI-divergence/Choppiness gate (default off)`

---

## Self-Review (completed by plan author)

**1. Spec coverage (design §10 SP-5):**
- New pure confirmation predicates in `strategies/filters.py` (§10.1) → Tasks 1–6. ✅
  - `supertrend` + `supertrend_flip` → Task 1.
  - `choppiness_index` → Task 2.
  - `adx_ok` reusing engine ADX column → Task 3.
  - `bollinger_touch` → Task 4; `keltner_touch` → Task 5.
  - `rsi_divergence` → Task 6.
- S1 refresh: opt-in SuperTrend + ADX/Choppiness + MTF EMA bias, default OFF (§10.1) → Task 7. ✅
- S2 refresh: opt-in Bollinger/Keltner touch + RSI-divergence + Choppiness gate, default OFF (§10.1) → Task 8. ✅
- Each predicate unit-tested deterministically with known SuperTrend flip, ADX threshold cross, BB/Keltner touch, RSI divergence (§10.2) → Tasks 1–6. ✅
- Existing S1/S2 tests stay green (preservation, §10.2) → enforced in Task 7 (`test_harness.py`) and Task 8 (`test_s2_range_mr.py`), plus explicit default-OFF tests. ✅
- No hard-floor changes (§10.1, Global Constraints) → confirmations only ADD gates; RR/SL/risk untouched. ✅

**2. Pinned interfaces:**
- Module `rtrade.strategies.filters` with `supertrend`, `supertrend_flip`, `choppiness_index`, `adx_ok`, `bollinger_touch`, `keltner_touch`, `rsi_divergence` — exact names as required by SP-6. ✅
- New config keys are additive under `confirmation:` and default to disabled. ✅
- `STRATEGY_REGISTRY` names unchanged: S1 stays `s1_trend_pullback`, S2 stays `s2_range_mr` — no edit to `strategies/__init__.py`. ✅

**3. Placeholder scan:** No TBD/TODO. Every code step has complete, typed code; every command has exact expected output. ✅

**4. Type consistency:** `supertrend(df, *, period, multiplier)` is called identically in Tasks 1 and 7. `adx_ok(df, *, threshold)` identical in Tasks 3 and 7. `choppiness_index(df, *, period)` identical in Tasks 2, 7, 8. `bollinger_touch`/`keltner_touch` `side: Literal["upper","lower"]` matches the `side` Literal built in Task 8. `rsi_divergence(df, *, lookback) -> Literal["bullish","bearish","none"]` matches the `want` comparison in Task 8. The `df.attrs` flag names set in `populate_indicators` (Tasks 7/8) exactly match the keys read in `_passes_confirmations`. ✅

**5. mypy --strict note:** the gate runs `mypy --strict src` only (tests are excluded per `pyproject.toml`), but all `src` code shown is fully typed. `df.attrs.get(...)` returns `Any` under the project's untyped-pandas setup; every read is wrapped in `int(...)`/`float(...)`/`bool(...)` before use so no `Any` leaks into typed call sites. `_mtf_bias_ok` wraps equality results in `bool(...)` to return a concrete `bool`.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-20-sp5-swing-refresh.md`.
This is **SP-5 of 6** and is independent of SP-2..SP-4 (can run in parallel after SP-1). Recommended execution: **subagent-driven-development** (fresh subagent per task + two-stage review), one task at a time, full gate green before advancing.
