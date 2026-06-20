# SP-3: SMC/ICT Indicator Module (FVG, Order Block, Liquidity Sweep, BOS/CHoCH) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pure `indicators/smc.py` module that detects the four Smart-Money-Concepts / ICT structures the S4 scalper (SP-4) will trade on — Fair Value Gaps, Order Blocks, Liquidity Sweeps, and market-structure BOS/CHoCH — over an OHLC(V) DataFrame, with deterministic, type-strict, hand-tested definitions and stable interfaces that SP-4 depends on by name.

**Architecture:** `indicators/smc.py` extends the existing `indicators/structure.py` style: frozen `slots=True` dataclasses + module-level pure functions, no I/O and no network. Detectors operate on a DataFrame indexed by bar-open timestamp with lowercase `open/high/low/close` columns (same convention as `structure.py` and `engine.py`). Swing-dependent detectors (order blocks, sweeps, market structure) reuse `structure.detect_swing_points` for fractal swings so swing semantics stay single-sourced. Concepts are ported (not imported) from the MIT-licensed `joshyattridge/smart-money-concepts` and `LesterCS/Decoding-Institutional-Order-Flow-in-Python-like-ICT`; an MIT attribution note lives in the module docstring.

**Tech Stack:** Python 3.12, pandas, numpy, frozen dataclasses (`slots=True`), `hypothesis` for property tests, pytest. No new runtime dependency is added.

## Global Constraints

- Signal-only — no order/broker placement. `smc.py` is a pure indicator layer (no I/O, no network, no DB).
- Hard risk floors untouched (GR-03 `rr_min ≥ 1.5`, GR-04 `sl_atr ∈ [0.5, 3.0]`, GR-05 `risk_per_trade_pct ≤ 2.0`) — not in scope here but must not be disturbed.
- `llm.enabled` stays false; no `model_construct` on production path.
- Determinism in tests: hand-constructed DataFrame fixtures only; no live network, no random seeds in the assertion path (property test uses `hypothesis`, deadline disabled).
- DataFrame convention (match `structure.py`/`engine.py`): datetime index (bar-open), lowercase columns `open/high/low/close` (`volume` optional); integer bar positions are returned as `*_idx` fields.
- Pinned interfaces are a contract for SP-4 S4 strategy — names, field order, and types must not drift.
- Toolchain via venv. Per-task gate: `.venv\Scripts\python.exe -m ruff check src tests` ; `ruff format src tests` ; `mypy --strict src` ; `pytest tests -q`.
- Commit via `COMMIT_MSG_TMP.txt` + `git commit -F COMMIT_MSG_TMP.txt`, then delete it. Before commit run `cmd /c 'if exist nul del "\\?\%CD%\nul"'`. No push unless asked.
- Follow existing file conventions (`from __future__ import annotations`, `@dataclass(frozen=True, slots=True)`, `df["col"].astype(float).values`, `float(...)` on returned scalars).

---

## File Structure

- Create: `src/rtrade/indicators/smc.py` — pure SMC/ICT detectors (4 dataclasses + 4 functions).
- Test: `tests/unit/test_smc.py` — deterministic fixtures (exact indices/levels) + one `hypothesis` property test.

---

## Pinned Interfaces (contract for SP-4)

```python
@dataclass(frozen=True, slots=True)
class FairValueGap:
    start_idx: int
    end_idx: int
    top: float
    bottom: float
    direction: Literal["bullish", "bearish"]

@dataclass(frozen=True, slots=True)
class OrderBlock:
    idx: int
    top: float
    bottom: float
    direction: Literal["bullish", "bearish"]

@dataclass(frozen=True, slots=True)
class LiquiditySweep:
    idx: int
    level: float
    side: Literal["high", "low"]

@dataclass(frozen=True, slots=True)
class StructureEvent:
    idx: int
    kind: Literal["BOS", "CHoCH"]
    direction: Literal["bullish", "bearish"]

def fair_value_gaps(df: pd.DataFrame) -> list[FairValueGap]: ...
def order_blocks(df: pd.DataFrame, *, swing_lookback: int = 5) -> list[OrderBlock]: ...
def liquidity_sweeps(df: pd.DataFrame, *, swing_lookback: int = 5) -> list[LiquiditySweep]: ...
def market_structure(df: pd.DataFrame, *, swing_lookback: int = 5) -> list[StructureEvent]: ...
```

**Pinned definitions (judgment calls, documented so detectors are testable):**
- **FVG (3-bar imbalance):** bullish when `low[i] > high[i-2]` (zone `bottom=high[i-2]`, `top=low[i]`); bearish when `high[i] < low[i-2]` (zone `bottom=high[i]`, `top=low[i-2]`). `start_idx=i-2`, `end_idx=i`. Strict inequalities guarantee `top > bottom`.
- **Market structure:** references are the **latest confirmed** fractal swing high/low (a swing at bar `j` is confirmed at `j + swing_lookback`). A bullish break = a bar **closes** above the active reference high; a bearish break = a bar **closes** below the active reference low. A break in the same direction as the current trend (or the **first** break, when no trend is established yet) is a **BOS** (continuation); the **first** break against the current trend is a **CHoCH**, after which the trend flips. A reference level is consumed once broken.
- **Order block:** the **last opposing candle before a structure break** — for a bullish break, the most recent down candle (`close < open`) before the break bar (`top=high`, `bottom=low`, `bullish`); for a bearish break, the most recent up candle (`close > open`) (`bearish`). Derived from every `market_structure` event (BOS and CHoCH are both structural breaks).
- **Liquidity sweep:** a bar whose **wick** pierces the latest confirmed swing level but **closes back inside** — high-side: `high[i] > swing_high` and `close[i] < swing_high`; low-side: `low[i] < swing_low` and `close[i] > swing_low`. The level is consumed once swept.

---

## Task 1: `fair_value_gaps` + the four pinned dataclasses

**Files:**
- Create: `src/rtrade/indicators/smc.py`
- Test: `tests/unit/test_smc.py`

**Interfaces:**
- Produces: `FairValueGap`, `OrderBlock`, `LiquiditySweep`, `StructureEvent` (all four pinned dataclasses, defined now so SP-4 has stable types immediately) and `fair_value_gaps(df) -> list[FairValueGap]`.
- Consumes: `pandas`. No swings needed (FVG is a pure 3-bar pattern).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_smc.py
"""Unit + property tests for SMC/ICT detectors (indicators/smc.py)."""

from __future__ import annotations

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

OHLC = tuple[float, float, float, float]


def _df(rows: list[OHLC]) -> pd.DataFrame:
    """Build an OHLC DataFrame from explicit (open, high, low, close) bars."""
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame(
        {
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
        },
        index=idx,
    )


# 5 bars: a bullish 3-bar gap at i=2 (low[2]=12.5 > high[0]=11),
# and a bearish 3-bar gap at i=4 (high[4]=9.5 < low[2]=12.5).
_FVG_ROWS: list[OHLC] = [
    (10.0, 11.0, 9.0, 10.0),
    (11.0, 12.0, 10.0, 11.0),
    (13.0, 15.0, 12.5, 14.0),
    (14.0, 14.5, 11.5, 12.0),
    (9.0, 9.5, 8.0, 8.5),
]


class TestFairValueGaps:
    def test_detects_bullish_and_bearish_gaps_with_exact_bounds(self) -> None:
        from rtrade.indicators.smc import FairValueGap, fair_value_gaps

        gaps = fair_value_gaps(_df(_FVG_ROWS))
        assert gaps == [
            FairValueGap(start_idx=0, end_idx=2, top=12.5, bottom=11.0, direction="bullish"),
            FairValueGap(start_idx=2, end_idx=4, top=12.5, bottom=9.5, direction="bearish"),
        ]

    def test_no_gap_when_bars_overlap(self) -> None:
        from rtrade.indicators.smc import fair_value_gaps

        rows: list[OHLC] = [
            (10.0, 11.0, 9.0, 10.0),
            (10.0, 11.0, 9.0, 10.0),
            (10.0, 11.0, 9.0, 10.0),
        ]
        assert fair_value_gaps(_df(rows)) == []

    def test_too_short_returns_empty(self) -> None:
        from rtrade.indicators.smc import fair_value_gaps

        assert fair_value_gaps(_df(_FVG_ROWS[:2])) == []


@settings(max_examples=100, deadline=None)
@given(
    rows=st.lists(
        st.tuples(
            st.floats(min_value=1.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
            st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False),
            st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False),
            st.floats(min_value=-20.0, max_value=20.0, allow_nan=False, allow_infinity=False),
        ),
        min_size=3,
        max_size=40,
    )
)
def test_fvg_top_always_strictly_above_bottom(
    rows: list[tuple[float, float, float, float]],
) -> None:
    from rtrade.indicators.smc import fair_value_gaps

    built: list[OHLC] = []
    for base, up, down, coff in rows:
        high = base + up
        low = base - down
        close = min(max(base + coff, low), high)
        built.append((base, high, low, close))
    for fvg in fair_value_gaps(_df(built)):
        assert fvg.top > fvg.bottom
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_smc.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rtrade.indicators.smc'`.

- [ ] **Step 3: Create the module with dataclasses + `fair_value_gaps`**

```python
# src/rtrade/indicators/smc.py
"""Smart-Money-Concepts / ICT detectors — pure functions, no I/O (PLAN SP-3 §8).

Detects the structures the S4 SMC scalper trades on, over an OHLC(V) DataFrame
with the same conventions as indicators/structure.py (datetime index of bar-open
timestamps; lowercase open/high/low/close columns). Integer bar positions are
returned as *_idx fields.

Detectors (pinned, deterministic definitions):
- fair_value_gaps:  3-bar imbalance (bullish low[i] > high[i-2]; bearish high[i] < low[i-2]).
- market_structure: BOS (continuation break of the latest swing in trend direction)
                    and CHoCH (first counter-trend break, flips the trend).
- order_blocks:     last opposing candle before a structure break.
- liquidity_sweeps: wick beyond a prior swing high/low that closes back inside.

Concepts adapted (ported, not imported) from the MIT-licensed projects
`joshyattridge/smart-money-concepts` and
`LesterCS/Decoding-Institutional-Order-Flow-in-Python-like-ICT`.
The detectors are re-implemented here for strict typing, determinism, and test
control. Original works are MIT licensed; attribution retained per their terms.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from rtrade.indicators.structure import SwingPoint, detect_swing_points


@dataclass(frozen=True, slots=True)
class FairValueGap:
    """A 3-bar imbalance zone (top > bottom). start/end are bar positions."""

    start_idx: int
    end_idx: int
    top: float
    bottom: float
    direction: Literal["bullish", "bearish"]


@dataclass(frozen=True, slots=True)
class OrderBlock:
    """Last opposing candle before a structure break; zone = [bottom, top]."""

    idx: int
    top: float
    bottom: float
    direction: Literal["bullish", "bearish"]


@dataclass(frozen=True, slots=True)
class LiquiditySweep:
    """A wick beyond a prior swing level that closes back inside it."""

    idx: int
    level: float
    side: Literal["high", "low"]


@dataclass(frozen=True, slots=True)
class StructureEvent:
    """A break of structure (BOS) or change of character (CHoCH)."""

    idx: int
    kind: Literal["BOS", "CHoCH"]
    direction: Literal["bullish", "bearish"]


def fair_value_gaps(df: pd.DataFrame) -> list[FairValueGap]:
    """Detect 3-bar fair value gaps (imbalances).

    Bullish: low[i] > high[i-2] (price jumped up, leaving an unfilled gap).
    Bearish: high[i] < low[i-2] (price dropped, leaving an unfilled gap).
    The zone spans bars [i-2 .. i]; `top` is the higher edge, `bottom` the lower.
    """
    if len(df) < 3:
        return []

    highs = df["high"].astype(float).values
    lows = df["low"].astype(float).values

    gaps: list[FairValueGap] = []
    for i in range(2, len(df)):
        if lows[i] > highs[i - 2]:
            gaps.append(
                FairValueGap(
                    start_idx=i - 2,
                    end_idx=i,
                    top=float(lows[i]),
                    bottom=float(highs[i - 2]),
                    direction="bullish",
                )
            )
        elif highs[i] < lows[i - 2]:
            gaps.append(
                FairValueGap(
                    start_idx=i - 2,
                    end_idx=i,
                    top=float(lows[i - 2]),
                    bottom=float(highs[i]),
                    direction="bearish",
                )
            )

    return gaps
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_smc.py -q`
Expected: PASS (4 passed — 3 example tests + 1 property test).

- [ ] **Step 5: Gate + commit**

```bash
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src ; .venv\Scripts\python.exe -m pytest tests -q
```
Expected: ruff clean, mypy `Success`, pytest green.

```
# write COMMIT_MSG_TMP.txt then:
git add src/rtrade/indicators/smc.py tests/unit/test_smc.py
git commit -F COMMIT_MSG_TMP.txt
```
Message: `feat(sp3): smc.py pinned dataclasses + fair_value_gaps (3-bar imbalance)`

---

## Task 2: `market_structure` (BOS / CHoCH)

> **Ordering note (judgment call):** the suggested granularity is FVG → order blocks → sweeps → market_structure, but `order_blocks` is defined as "last opposing candle before a **structure break**" and therefore depends on `market_structure`. To keep the order-block detector reusing a single break definition (no duplicated logic), `market_structure` is implemented **before** `order_blocks`. Liquidity sweeps (swing-only) come last.

**Files:**
- Modify: `src/rtrade/indicators/smc.py`
- Test: `tests/unit/test_smc.py`

**Interfaces:**
- Produces: `market_structure(df, *, swing_lookback: int = 5) -> list[StructureEvent]`.
- Consumes: `structure.detect_swing_points` (fractal swings; called with `left=right=swing_lookback`), `StructureEvent`.
- Definition: references = latest **confirmed** swing high/low (swing at bar `j` confirmed at `j + swing_lookback`). Bullish break = a bar closes above the active reference high; bearish break = closes below the active reference low. Same-direction break (or the first break, trend undetermined) → `BOS`; first counter-trend break → `CHoCH` (then trend flips). A reference is consumed when broken.

- [ ] **Step 1: Write the failing test (append to `tests/unit/test_smc.py`)**

```python
# 12 bars: swing high @3 (110) broken at close[6]=111 -> bullish BOS @6;
# swing low @8 (107) broken at close[11]=105 -> bearish CHoCH @11.
_MS_ROWS: list[OHLC] = [
    (100.0, 102.0, 99.0, 101.0),
    (101.0, 103.0, 100.0, 102.0),
    (102.0, 104.0, 101.0, 103.0),
    (103.0, 110.0, 102.0, 104.0),
    (104.0, 106.0, 103.0, 105.0),
    (108.0, 108.5, 104.0, 106.0),
    (106.0, 112.0, 109.0, 111.0),
    (111.0, 113.0, 109.0, 110.0),
    (110.0, 111.0, 107.0, 108.0),
    (108.0, 110.0, 108.0, 109.0),
    (109.0, 111.0, 108.0, 110.0),
    (108.0, 109.0, 104.0, 105.0),
]


class TestMarketStructure:
    def test_bos_then_choch_with_exact_indices(self) -> None:
        from rtrade.indicators.smc import StructureEvent, market_structure

        events = market_structure(_df(_MS_ROWS), swing_lookback=2)
        assert events == [
            StructureEvent(idx=6, kind="BOS", direction="bullish"),
            StructureEvent(idx=11, kind="CHoCH", direction="bearish"),
        ]

    def test_no_events_without_breaks(self) -> None:
        from rtrade.indicators.smc import market_structure

        flat: list[OHLC] = [(100.0, 101.0, 99.0, 100.0)] * 8
        assert market_structure(_df(flat), swing_lookback=2) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_smc.py::TestMarketStructure -q`
Expected: FAIL — `ImportError: cannot import name 'market_structure' from 'rtrade.indicators.smc'`.

- [ ] **Step 3: Add a confirmation helper + `market_structure` (append to `smc.py`)**

```python
def _swings_by_confirmation(df: pd.DataFrame, swing_lookback: int) -> dict[int, list[SwingPoint]]:
    """Group fractal swings by the bar at which they become confirmed.

    A swing at bar `j` is only known after `swing_lookback` further bars, i.e. it
    becomes actionable at bar `j + swing_lookback` (no look-ahead).
    """
    confirmations: dict[int, list[SwingPoint]] = {}
    for sp in detect_swing_points(df, left=swing_lookback, right=swing_lookback):
        confirmations.setdefault(sp.index + swing_lookback, []).append(sp)
    return confirmations


def market_structure(df: pd.DataFrame, *, swing_lookback: int = 5) -> list[StructureEvent]:
    """Detect BOS (continuation) and CHoCH (first counter-trend) structure breaks.

    Tracks the latest confirmed swing high/low. A close above the active swing
    high is a bullish break; a close below the active swing low is bearish. A
    break aligned with the current trend (or the first break, when no trend is
    set yet) is a BOS; the first break against the trend is a CHoCH and flips it.
    """
    closes = df["close"].astype(float).values
    confirmations = _swings_by_confirmation(df, swing_lookback)

    events: list[StructureEvent] = []
    trend: Literal["bullish", "bearish"] | None = None
    ref_high: float | None = None
    ref_low: float | None = None

    for i in range(len(df)):
        for sp in confirmations.get(i, []):
            if sp.is_high:
                ref_high = sp.price
            else:
                ref_low = sp.price

        close = float(closes[i])
        if ref_high is not None and close > ref_high:
            if trend in (None, "bullish"):
                events.append(StructureEvent(idx=i, kind="BOS", direction="bullish"))
            else:
                events.append(StructureEvent(idx=i, kind="CHoCH", direction="bullish"))
            trend = "bullish"
            ref_high = None
        elif ref_low is not None and close < ref_low:
            if trend in (None, "bearish"):
                events.append(StructureEvent(idx=i, kind="BOS", direction="bearish"))
            else:
                events.append(StructureEvent(idx=i, kind="CHoCH", direction="bearish"))
            trend = "bearish"
            ref_low = None

    return events
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_smc.py::TestMarketStructure -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Gate + commit**

Run the full gate (`ruff check`, `ruff format`, `mypy --strict src`, `pytest tests -q`). Expected: clean, all green.

```
git add src/rtrade/indicators/smc.py tests/unit/test_smc.py
git commit -F COMMIT_MSG_TMP.txt
```
Message: `feat(sp3): market_structure BOS/CHoCH over confirmed fractal swings`

---

## Task 3: `order_blocks` (last opposing candle before a break)

**Files:**
- Modify: `src/rtrade/indicators/smc.py`
- Test: `tests/unit/test_smc.py`

**Interfaces:**
- Produces: `order_blocks(df, *, swing_lookback: int = 5) -> list[OrderBlock]`.
- Consumes: `market_structure` (reuses its break events) + `OrderBlock`.
- Definition: for each `StructureEvent` (BOS or CHoCH), scan backwards from the bar before the break for the last **opposing** candle — bullish break → last down candle (`close < open`); bearish break → last up candle (`close > open`). The block zone is that candle's `[low, high]` (`bottom=low`, `top=high`). If no opposing candle exists before the break, no block is emitted for that event.

- [ ] **Step 1: Write the failing test (append to `tests/unit/test_smc.py`)**

Reuses `_MS_ROWS` from Task 2: bullish break @6 → last down candle is @5 (`close 106 < open 108`); bearish break @11 → last up candle is @10 (`close 110 > open 109`).

```python
class TestOrderBlocks:
    def test_last_opposing_candle_before_each_break(self) -> None:
        from rtrade.indicators.smc import OrderBlock, order_blocks

        blocks = order_blocks(_df(_MS_ROWS), swing_lookback=2)
        assert blocks == [
            OrderBlock(idx=5, top=108.5, bottom=104.0, direction="bullish"),
            OrderBlock(idx=10, top=111.0, bottom=108.0, direction="bearish"),
        ]

    def test_no_blocks_without_structure(self) -> None:
        from rtrade.indicators.smc import order_blocks

        flat: list[OHLC] = [(100.0, 101.0, 99.0, 100.0)] * 8
        assert order_blocks(_df(flat), swing_lookback=2) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_smc.py::TestOrderBlocks -q`
Expected: FAIL — `ImportError: cannot import name 'order_blocks' from 'rtrade.indicators.smc'`.

- [ ] **Step 3: Add `order_blocks` (append to `smc.py`)**

```python
def order_blocks(df: pd.DataFrame, *, swing_lookback: int = 5) -> list[OrderBlock]:
    """Detect order blocks: the last opposing candle before each structure break.

    Bullish break -> most recent down candle (close < open) before the break bar.
    Bearish break -> most recent up candle (close > open) before the break bar.
    The block's price zone is that candle's [low, high].
    """
    opens = df["open"].astype(float).values
    highs = df["high"].astype(float).values
    lows = df["low"].astype(float).values
    closes = df["close"].astype(float).values

    blocks: list[OrderBlock] = []
    for event in market_structure(df, swing_lookback=swing_lookback):
        j = event.idx - 1
        if event.direction == "bullish":
            while j >= 0 and not closes[j] < opens[j]:
                j -= 1
            if j >= 0:
                blocks.append(
                    OrderBlock(
                        idx=j,
                        top=float(highs[j]),
                        bottom=float(lows[j]),
                        direction="bullish",
                    )
                )
        else:
            while j >= 0 and not closes[j] > opens[j]:
                j -= 1
            if j >= 0:
                blocks.append(
                    OrderBlock(
                        idx=j,
                        top=float(highs[j]),
                        bottom=float(lows[j]),
                        direction="bearish",
                    )
                )

    return blocks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_smc.py::TestOrderBlocks -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Gate + commit**

Run the full gate (`ruff check`, `ruff format`, `mypy --strict src`, `pytest tests -q`). Expected: clean, all green.

```
git add src/rtrade/indicators/smc.py tests/unit/test_smc.py
git commit -F COMMIT_MSG_TMP.txt
```
Message: `feat(sp3): order_blocks (last opposing candle before BOS/CHoCH)`

---

## Task 4: `liquidity_sweeps` (wick beyond swing, close back inside)

**Files:**
- Modify: `src/rtrade/indicators/smc.py`
- Test: `tests/unit/test_smc.py`

**Interfaces:**
- Produces: `liquidity_sweeps(df, *, swing_lookback: int = 5) -> list[LiquiditySweep]`.
- Consumes: `_swings_by_confirmation` (from Task 2) + `LiquiditySweep`.
- Definition: track the latest confirmed swing high/low. High-side sweep at bar `i`: `high[i] > swing_high` **and** `close[i] < swing_high` (`side="high"`, `level=swing_high`). Low-side sweep: `low[i] < swing_low` **and** `close[i] > swing_low` (`side="low"`, `level=swing_low`). The level is consumed once swept.

- [ ] **Step 1: Write the failing test (append to `tests/unit/test_smc.py`)**

```python
class TestLiquiditySweeps:
    def test_high_side_sweep_exact(self) -> None:
        from rtrade.indicators.smc import LiquiditySweep, liquidity_sweeps

        # swing high @2 = 15; bar @5 wicks to 16 but closes 12.5 (back inside).
        rows: list[OHLC] = [
            (10.0, 11.0, 9.0, 10.0),
            (10.0, 12.0, 9.0, 11.0),
            (11.0, 15.0, 10.0, 12.0),
            (11.0, 13.0, 10.0, 12.0),
            (12.0, 13.0, 11.0, 12.0),
            (12.0, 16.0, 11.0, 12.5),
        ]
        sweeps = liquidity_sweeps(_df(rows), swing_lookback=2)
        assert sweeps == [LiquiditySweep(idx=5, level=15.0, side="high")]

    def test_low_side_sweep_exact(self) -> None:
        from rtrade.indicators.smc import LiquiditySweep, liquidity_sweeps

        # swing low @2 = 15; bar @5 wicks to 14 but closes 17.5 (back inside).
        rows: list[OHLC] = [
            (20.0, 21.0, 19.0, 20.0),
            (20.0, 21.0, 18.0, 19.0),
            (19.0, 20.0, 15.0, 18.0),
            (18.0, 19.0, 16.0, 17.0),
            (17.0, 18.0, 16.0, 17.0),
            (17.0, 18.0, 14.0, 17.5),
        ]
        sweeps = liquidity_sweeps(_df(rows), swing_lookback=2)
        assert sweeps == [LiquiditySweep(idx=5, level=15.0, side="low")]

    def test_no_sweep_when_close_breaks_through(self) -> None:
        from rtrade.indicators.smc import liquidity_sweeps

        # swing high @2 = 15; bar @5 closes ABOVE 15 -> a break, not a sweep.
        rows: list[OHLC] = [
            (10.0, 11.0, 9.0, 10.0),
            (10.0, 12.0, 9.0, 11.0),
            (11.0, 15.0, 10.0, 12.0),
            (11.0, 13.0, 10.0, 12.0),
            (12.0, 13.0, 11.0, 12.0),
            (12.0, 16.0, 11.0, 15.5),
        ]
        assert liquidity_sweeps(_df(rows), swing_lookback=2) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_smc.py::TestLiquiditySweeps -q`
Expected: FAIL — `ImportError: cannot import name 'liquidity_sweeps' from 'rtrade.indicators.smc'`.

- [ ] **Step 3: Add `liquidity_sweeps` (append to `smc.py`)**

```python
def liquidity_sweeps(df: pd.DataFrame, *, swing_lookback: int = 5) -> list[LiquiditySweep]:
    """Detect liquidity sweeps: a wick beyond a prior swing that closes back inside.

    High-side: high[i] > swing_high and close[i] < swing_high (stop-run above, then
    rejection). Low-side: low[i] < swing_low and close[i] > swing_low. The swept
    level is consumed so it does not re-trigger.
    """
    highs = df["high"].astype(float).values
    lows = df["low"].astype(float).values
    closes = df["close"].astype(float).values
    confirmations = _swings_by_confirmation(df, swing_lookback)

    sweeps: list[LiquiditySweep] = []
    ref_high: float | None = None
    ref_low: float | None = None

    for i in range(len(df)):
        for sp in confirmations.get(i, []):
            if sp.is_high:
                ref_high = sp.price
            else:
                ref_low = sp.price

        high = float(highs[i])
        low = float(lows[i])
        close = float(closes[i])
        if ref_high is not None and high > ref_high and close < ref_high:
            sweeps.append(LiquiditySweep(idx=i, level=ref_high, side="high"))
            ref_high = None
        elif ref_low is not None and low < ref_low and close > ref_low:
            sweeps.append(LiquiditySweep(idx=i, level=ref_low, side="low"))
            ref_low = None

    return sweeps
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_smc.py::TestLiquiditySweeps -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Full gate + commit**

Run the full gate (`ruff check`, `ruff format`, `mypy --strict src`, `pytest tests -q`). Expected: clean, all green (`tests/unit/test_smc.py` now has 11 tests).

```
git add src/rtrade/indicators/smc.py tests/unit/test_smc.py
git commit -F COMMIT_MSG_TMP.txt
```
Message: `feat(sp3): liquidity_sweeps (wick beyond swing, close back inside)`

---

## Self-Review (completed by plan author)

**1. Spec coverage (SP-3 section §8 of design):**
- `fair_value_gaps` (3-bar imbalance, §8.1) → Task 1. ✅
- `market_structure` BOS/CHoCH (§8.1) → Task 2. ✅
- `order_blocks` = last opposing candle before a break (§8.1) → Task 3. ✅
- `liquidity_sweeps` = wick beyond swing then reversal close (§8.1) → Task 4. ✅
- Pure module extending `structure.py`, no I/O, MIT attribution in docstring (§8.1, §16) → module docstring in Task 1. ✅
- Deterministic hand-constructed fixtures asserting exact indices/levels (§8.2) → every task. ✅
- Property test for an invariant — FVG `top > bottom` always (§8.2) → Task 1. ✅
- Note: order detector tasks were sequenced FVG → market_structure → order_blocks → liquidity_sweeps (instead of the literal FVG → OB → sweeps → market_structure) because `order_blocks` depends on `market_structure`; documented as a judgment call in Task 2.

**2. Placeholder scan:** No TBD/TODO; every code step has complete, typed code (mypy `--strict` clean: explicit `Literal` branch assignment for `StructureEvent.kind`/`direction`, `float(...)` on returned scalars, fully annotated signatures) and exact commands with expected output.

**3. Type/interface consistency:** The four pinned dataclasses (`FairValueGap{start_idx,end_idx,top,bottom,direction}`, `OrderBlock{idx,top,bottom,direction}`, `LiquiditySweep{idx,level,side}`, `StructureEvent{idx,kind,direction}`) are defined once in Task 1 and referenced unchanged in Tasks 2–4. `swing_lookback: int = 5` is identical across `order_blocks`/`liquidity_sweeps`/`market_structure`. `_swings_by_confirmation(df, swing_lookback)` (added in Task 2) is reused by Task 4. `market_structure` is consumed by `order_blocks` in Task 3.

**4. Determinism:** All example fixtures are hand-built integer/half-integer OHLC bars; swing confirmation uses `swing_lookback=2` in fixtures so frames stay compact while exercising real fractal confirmation. The single property test disables the `hypothesis` deadline and clamps `close` into `[low, high]` so every generated frame is a valid OHLC frame.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-20-sp3-smc-indicators.md`.
This is **SP-3 of 6**. It is an independent, gated module (no SP-1/SP-2 dependency) and can run in parallel with SP-2. Recommended execution: **subagent-driven-development** (fresh subagent per task + two-stage review), one task at a time, full gate green before advancing. Downstream **SP-4 S4** imports the four pinned dataclasses and detector functions by name — do not rename or reorder their fields.
