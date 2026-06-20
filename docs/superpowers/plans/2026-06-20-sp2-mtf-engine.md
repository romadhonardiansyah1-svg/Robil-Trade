# SP-2: Multi-Timeframe Scan Engine (anchor H4 + entry M5/M15) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generalize the scan pipeline from the hard-coded "entry = H1, context = H4" shape to a config-driven **entry-timeframe set** (`{M5, M15}`) with **H4 as the trend/regime anchor**, so the scalping strategies (SP-4) can run on M5/M15 while only entries aligned with the H4 trend bias survive — all while staying signal-only and fully type/lint/test clean. When no MTF config is present, behavior is byte-for-byte the current H1 pipeline (back-compat).

**Architecture:** A new pure helper module `pipeline/mtf.py` exposes `h4_trend_bias(df_h4)` (EMA-fast/slow + slope) and `aligned(bias, action)` — both I/O-free and unit-testable with synthetic frames. `InstrumentConfig` gains optional `entry_timeframes`/`anchor_timeframe` with back-compat resolvers (empty → entry `{H1}`, anchor `H4`). `run_scan` routes on the resolved entry set: it runs the FULL pipeline when `tf` is an entry timeframe and returns `ingested_context_only` for every other tf (incl. the anchor). The anchor (H4) df is loaded, the bias computed, and `_run_strategies` drops any candidate whose `action` disagrees with the bias — but only when MTF is actually configured, so the legacy H1 path is unchanged. The warmup guarantee (P1-7) is enforced per entry tf AND anchor tf via a generalized pure helper. The scheduler grows M5/M15 cron mappings; the existing H4 entry already produces the anchor ingest job, and idempotency stays guaranteed by the `signals` unique constraint per bar.

**Tech Stack:** Python 3.12, pandas (EMA via `Series.ewm`), structlog, pydantic / pydantic-settings (config), APScheduler `CronTrigger`, pytest (deterministic, monkeypatch — no live network).

## Global Constraints

- **Signal-only** — no order/broker placement, ever.
- **Hard risk floors (config-loader enforced, never weakened):** GR-03 `rr_min ≥ 1.5`; GR-04 `sl_atr ∈ [0.5, 3.0]`; GR-05 `risk_per_trade_pct ≤ 2.0`. Not modified here and must not be disturbed.
- **News blackout (GR-07)** applies to ALL timeframes incl. M5/M15 (already enforced inside `_run_strategies`; unchanged).
- **Calendar fail-CLOSE:** `calendar.fail_open_when_stale = false` (unchanged).
- **`llm.enabled = false`**; GI-5: no `model_construct` on the production path.
- **Warmup guarantee (P1-7):** abstain (`abstain_warmup`) until a full warmup window exists, **per entry timeframe AND the anchor timeframe**.
- **Back-compat:** with no `entry_timeframes`/`anchor_timeframe` configured, the resolved entry set is `{H1}`, the anchor is `H4`, and H4-bias filtering is **not** enforced — the legacy H1 pipeline behaves exactly as before.
- **Determinism in tests:** synthetic DataFrames + `monkeypatch` (mirror `tests/unit/test_scan_post_llm_gate.py`); no live network; no DB. Integration tests skip when the live stack is unreachable.
- **Toolchain (run via venv):** `.venv\Scripts\python.exe -m <tool>`. Gate per task: `ruff check src tests` ; `ruff format src tests` ; `mypy --strict src` ; `pytest tests -q`.
- **Commits:** message via `COMMIT_MSG_TMP.txt` + `git commit -F COMMIT_MSG_TMP.txt`, then delete the temp file. Remove stray Windows `nul` artifact before commit. No push unless explicitly requested.
- Follow existing file conventions (`from __future__ import annotations`, structlog logger, pure helpers kept I/O-free and offload-safe).

---

## File Structure

- Create: `src/rtrade/pipeline/mtf.py` — pure `h4_trend_bias` + `aligned` (PINNED interfaces for SP-4).
- Modify: `src/rtrade/core/config.py` — `InstrumentConfig`: add `entry_timeframes` / `anchor_timeframe` + resolver methods + subset validator.
- Modify: `src/rtrade/pipeline/scan.py` — add `_warmup_deficit_mtf` + `_is_entry_timeframe`; generalize `run_scan` routing (entry tf full pipeline, others ingest-only) + anchor df/bias load; add bias filter to `_run_strategies`.
- Modify: `src/rtrade/scheduler/main.py` — `build_scan_schedules`: add M5/M15 cron mappings.
- Modify: `config/instruments.yaml` — XAUUSD `timeframes: ["5m","15m","4h"]`, `entry_timeframes: ["5m","15m"]`, `anchor_timeframe: "4h"`.
- Test: `tests/unit/test_mtf_bias.py`, `tests/unit/test_instrument_mtf_config.py`, `tests/unit/test_warmup_deficit_mtf.py`, `tests/unit/test_scan_mtf_routing.py`, `tests/unit/test_scheduler_mtf_schedules.py`.

---

## Task 1: `pipeline/mtf.py` — pure `h4_trend_bias` + `aligned` (PINNED for SP-4)

**Files:**
- Create: `src/rtrade/pipeline/mtf.py`
- Test: `tests/unit/test_mtf_bias.py`

**Interfaces:**
- Consumes: `pandas`, `rtrade.core.constants.Action`.
- Produces (PINNED — SP-4 depends on these exact names/types):
  - `h4_trend_bias(df_h4: pd.DataFrame) -> Literal["UP", "DOWN", "NONE"]` — EMA-fast(20)/slow(50) cross + slow-EMA slope over the last 10 bars; `NONE` when fewer than `_MIN_BARS` (60) usable closes or no clear aligned slope.
  - `aligned(bias: Literal["UP", "DOWN", "NONE"], action: Action) -> bool` — `BUY` aligns with `UP`, `SELL` with `DOWN`; `NONE` blocks everything.
- Note: kept I/O-free and column-light (only needs a `close` column) so it is deterministic with hand-built frames and safe to call from the event loop or a worker thread.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_mtf_bias.py
from __future__ import annotations

import pandas as pd

from rtrade.core.constants import Action
from rtrade.pipeline.mtf import aligned, h4_trend_bias


def _frame(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(closes), freq="4h", tz="UTC")
    return pd.DataFrame({"close": closes}, index=idx)


def test_rising_series_is_up() -> None:
    df = _frame([100.0 + i for i in range(80)])
    assert h4_trend_bias(df) == "UP"


def test_falling_series_is_down() -> None:
    df = _frame([300.0 - i for i in range(80)])
    assert h4_trend_bias(df) == "DOWN"


def test_flat_series_is_none() -> None:
    df = _frame([200.0] * 80)
    assert h4_trend_bias(df) == "NONE"


def test_insufficient_bars_is_none() -> None:
    df = _frame([100.0 + i for i in range(40)])  # < _MIN_BARS
    assert h4_trend_bias(df) == "NONE"


def test_empty_or_missing_close_is_none() -> None:
    assert h4_trend_bias(pd.DataFrame()) == "NONE"
    assert h4_trend_bias(pd.DataFrame({"open": [1.0, 2.0]})) == "NONE"


def test_aligned_truth_table() -> None:
    assert aligned("UP", Action.BUY) is True
    assert aligned("UP", Action.SELL) is False
    assert aligned("DOWN", Action.SELL) is True
    assert aligned("DOWN", Action.BUY) is False
    assert aligned("NONE", Action.BUY) is False
    assert aligned("NONE", Action.SELL) is False
    assert aligned("UP", Action.ABSTAIN) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_mtf_bias.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rtrade.pipeline.mtf'`.

- [ ] **Step 3: Implement the module**

```python
# src/rtrade/pipeline/mtf.py
"""Multi-timeframe (MTF) helpers for the scan engine (SP-2).

Pure, I/O-free functions shared by the scan pipeline and the scalping
strategies (SP-4). ``h4_trend_bias`` reduces an anchor (H4) OHLC frame to a
coarse trend label and ``aligned`` answers whether a candidate action agrees
with that bias. Both are deterministic over a plain ``close`` column so they
can be unit-tested with hand-built frames and are safe to call from a worker
thread (no shared state).
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

from rtrade.core.constants import Action

Bias = Literal["UP", "DOWN", "NONE"]

# Minimum closed anchor bars before a bias is meaningful (else NONE → blocks).
_MIN_BARS = 60
# EMA spans for the fast/slow trend pair and the slow-EMA slope lookback.
_EMA_FAST = 20
_EMA_SLOW = 50
_SLOPE_LOOKBACK = 10


def h4_trend_bias(df_h4: pd.DataFrame) -> Bias:
    """Coarse anchor-timeframe trend bias: UP / DOWN / NONE.

    UP   : fast EMA above slow EMA AND the slow EMA is rising over the last
           ``_SLOPE_LOOKBACK`` bars.
    DOWN : fast EMA below slow EMA AND the slow EMA is falling.
    NONE : insufficient bars, missing ``close``, or no aligned slope (flat /
           conflicting) — NONE deliberately blocks all entries downstream.
    """
    if df_h4 is None or "close" not in df_h4.columns:  # type: ignore[redundant-expr]
        return "NONE"
    closes = df_h4["close"].astype(float).dropna()
    if len(closes) < _MIN_BARS:
        return "NONE"

    ema_fast = closes.ewm(span=_EMA_FAST, adjust=False).mean()
    ema_slow = closes.ewm(span=_EMA_SLOW, adjust=False).mean()

    fast_last = float(ema_fast.iloc[-1])
    slow_last = float(ema_slow.iloc[-1])
    slow_prev = float(ema_slow.iloc[-1 - _SLOPE_LOOKBACK])

    rising = slow_last > slow_prev
    falling = slow_last < slow_prev

    if fast_last > slow_last and rising:
        return "UP"
    if fast_last < slow_last and falling:
        return "DOWN"
    return "NONE"


def aligned(bias: Bias, action: Action) -> bool:
    """True when ``action`` agrees with ``bias``; NONE blocks everything."""
    if bias == "UP":
        return action == Action.BUY
    if bias == "DOWN":
        return action == Action.SELL
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_mtf_bias.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Gate + commit**

```bash
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src ; .venv\Scripts\python.exe -m pytest tests/unit/test_mtf_bias.py -q
```
Expected: ruff clean, mypy `Success`, pytest 6 passed.

```
# write COMMIT_MSG_TMP.txt then:
git add src/rtrade/pipeline/mtf.py tests/unit/test_mtf_bias.py
git commit -F COMMIT_MSG_TMP.txt
```
Message: `feat(sp2): pure MTF helpers h4_trend_bias + aligned (pinned for SP-4)`

---

## Task 2: `InstrumentConfig` — `entry_timeframes` / `anchor_timeframe` (PINNED) + resolvers

**Files:**
- Modify: `src/rtrade/core/config.py` (the `InstrumentConfig` class)
- Test: `tests/unit/test_instrument_mtf_config.py`

**Interfaces:**
- Consumes: existing `InstrumentConfig(_StrictModel)` with `timeframes: list[Timeframe]`, `context_timeframe: Timeframe`.
- Produces (PINNED — SP-4 reads these):
  - New optional fields `entry_timeframes: list[Timeframe] = Field(default_factory=list)` and `anchor_timeframe: Timeframe | None = None`.
  - `resolved_entry_timeframes(self) -> list[Timeframe]` — configured set, else `[Timeframe.H1]` (back-compat).
  - `resolved_anchor_timeframe(self) -> Timeframe` — configured anchor, else `Timeframe.H4` (back-compat).
  - `model_validator` ensuring (when set) every entry tf and the anchor tf are members of `timeframes`, entry tfs are unique, and the anchor is not also an entry tf.
- Note: defaults keep existing `config/instruments.yaml` entries valid with no edits (empty list / `None`). `_StrictModel` forbids extra keys, so the new fields are the only accepted way to enable MTF.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_instrument_mtf_config.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from rtrade.core.config import InstrumentConfig
from rtrade.core.constants import Market, Timeframe


def _inst(**over: object) -> InstrumentConfig:
    base: dict[str, object] = {
        "symbol": "XAUUSD",
        "market": Market.METALS,
        "provider": "oanda",
        "provider_symbol": "XAU_USD",
        "timeframes": [Timeframe.M5, Timeframe.M15, Timeframe.H4],
        "context_timeframe": Timeframe.D1,
        "pip_size": 0.01,
        "quote_currency": "USD",
    }
    base.update(over)
    return InstrumentConfig(**base)  # type: ignore[arg-type]


def test_defaults_are_back_compat() -> None:
    inst = _inst(timeframes=[Timeframe.H1, Timeframe.H4])
    assert inst.entry_timeframes == []
    assert inst.anchor_timeframe is None
    assert inst.resolved_entry_timeframes() == [Timeframe.H1]
    assert inst.resolved_anchor_timeframe() == Timeframe.H4


def test_configured_mtf_resolves_to_configured_values() -> None:
    inst = _inst(
        entry_timeframes=[Timeframe.M5, Timeframe.M15],
        anchor_timeframe=Timeframe.H4,
    )
    assert inst.resolved_entry_timeframes() == [Timeframe.M5, Timeframe.M15]
    assert inst.resolved_anchor_timeframe() == Timeframe.H4


def test_entry_tf_must_be_in_timeframes() -> None:
    with pytest.raises(ValidationError):
        _inst(entry_timeframes=[Timeframe.H1], anchor_timeframe=Timeframe.H4)


def test_anchor_tf_must_be_in_timeframes() -> None:
    with pytest.raises(ValidationError):
        _inst(entry_timeframes=[Timeframe.M5], anchor_timeframe=Timeframe.D1)


def test_anchor_cannot_also_be_entry() -> None:
    with pytest.raises(ValidationError):
        _inst(
            entry_timeframes=[Timeframe.M5, Timeframe.H4],
            anchor_timeframe=Timeframe.H4,
        )


def test_duplicate_entry_tfs_rejected() -> None:
    with pytest.raises(ValidationError):
        _inst(entry_timeframes=[Timeframe.M5, Timeframe.M5], anchor_timeframe=Timeframe.H4)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_instrument_mtf_config.py -q`
Expected: FAIL — `extra` field forbidden / `AttributeError: ... has no attribute 'resolved_entry_timeframes'`.

- [ ] **Step 3: Add fields, resolvers, and validator to `InstrumentConfig`**

In `src/rtrade/core/config.py`, inside `class InstrumentConfig(_StrictModel)`, add the fields after `derivatives: bool = False`:

```python
    # SP-2 multi-timeframe routing (optional; empty → legacy H1 entry / H4 anchor).
    entry_timeframes: list[Timeframe] = Field(default_factory=list)
    anchor_timeframe: Timeframe | None = None
```

Add the resolver methods and the MTF validator (next to `_unique_timeframes`):

```python
    def resolved_entry_timeframes(self) -> list[Timeframe]:
        """Entry timeframes to run the full pipeline on; legacy default = [H1]."""
        return list(self.entry_timeframes) if self.entry_timeframes else [Timeframe.H1]

    def resolved_anchor_timeframe(self) -> Timeframe:
        """Trend/regime anchor timeframe; legacy default = H4."""
        return self.anchor_timeframe if self.anchor_timeframe is not None else Timeframe.H4

    @model_validator(mode="after")
    def _check_mtf(self) -> "InstrumentConfig":
        if self.entry_timeframes:
            if len(set(self.entry_timeframes)) != len(self.entry_timeframes):
                raise ValueError("duplicate entry_timeframes")
            missing = [tf for tf in self.entry_timeframes if tf not in self.timeframes]
            if missing:
                raise ValueError(f"entry_timeframes not in timeframes: {missing}")
        if self.anchor_timeframe is not None:
            if self.anchor_timeframe not in self.timeframes:
                raise ValueError(f"anchor_timeframe not in timeframes: {self.anchor_timeframe}")
            if self.anchor_timeframe in self.entry_timeframes:
                raise ValueError("anchor_timeframe must not also be an entry timeframe")
        return self
```

(`model_validator` is already imported at the top of `config.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_instrument_mtf_config.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Gate + commit**

```bash
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src ; .venv\Scripts\python.exe -m pytest tests/unit/test_instrument_mtf_config.py -q
```
Expected: ruff clean, mypy `Success`, pytest 6 passed. Then run the existing config suite (`.venv\Scripts\python.exe -m pytest tests/unit/test_config*.py -q`) to confirm legacy instruments still load.

```
git add src/rtrade/core/config.py tests/unit/test_instrument_mtf_config.py
git commit -F COMMIT_MSG_TMP.txt
```
Message: `feat(sp2): InstrumentConfig entry_timeframes/anchor_timeframe + resolvers (pinned)`

---

## Task 3: Generalized warmup helper `_warmup_deficit_mtf`

**Files:**
- Modify: `src/rtrade/pipeline/scan.py` (add new helper; leave `_warmup_deficit` untouched)
- Test: `tests/unit/test_warmup_deficit_mtf.py`

**Interfaces:**
- Consumes: `Timeframe`.
- Produces: `_warmup_deficit_mtf(*, bars_entry: int, entry_tf: Timeframe, bars_anchor: int, anchor_tf: Timeframe, warmup_bars: int) -> dict[str, int | str] | None` — returns the abstain detail when the entry tf OR the anchor tf is under-warmed (entry checked first), else `None`. The existing `_warmup_deficit` (used only by its own legacy test) is **not** removed, so the P1-7 cold-start property test stays green.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_warmup_deficit_mtf.py
from __future__ import annotations

from rtrade.core.constants import Timeframe
from rtrade.pipeline.scan import _warmup_deficit_mtf


def test_entry_under_warmup_reports_entry_first() -> None:
    out = _warmup_deficit_mtf(
        bars_entry=100, entry_tf=Timeframe.M5,
        bars_anchor=100, anchor_tf=Timeframe.H4,
        warmup_bars=500,
    )
    assert out == {"timeframe": "5m", "bars": 100, "required": 500}


def test_anchor_under_warmup_when_entry_ok() -> None:
    out = _warmup_deficit_mtf(
        bars_entry=600, entry_tf=Timeframe.M15,
        bars_anchor=120, anchor_tf=Timeframe.H4,
        warmup_bars=500,
    )
    assert out == {"timeframe": "4h", "bars": 120, "required": 500}


def test_fully_warmed_returns_none() -> None:
    out = _warmup_deficit_mtf(
        bars_entry=600, entry_tf=Timeframe.M5,
        bars_anchor=600, anchor_tf=Timeframe.H4,
        warmup_bars=500,
    )
    assert out is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_warmup_deficit_mtf.py -q`
Expected: FAIL — `ImportError: cannot import name '_warmup_deficit_mtf'`.

- [ ] **Step 3: Add the helper**

In `src/rtrade/pipeline/scan.py`, directly after the existing `_warmup_deficit` function, add:

```python
def _warmup_deficit_mtf(
    *,
    bars_entry: int,
    entry_tf: Timeframe,
    bars_anchor: int,
    anchor_tf: Timeframe,
    warmup_bars: int,
) -> dict[str, int | str] | None:
    """P1-7 generalized to MTF: abstain until BOTH the entry tf and the anchor tf
    hold a full warmup window. The entry tf is checked first so its deficit is the
    one surfaced. Returns the abstain detail, or ``None`` once both are warmed.
    """
    if bars_entry < warmup_bars:
        return {"timeframe": entry_tf.value, "bars": bars_entry, "required": warmup_bars}
    if bars_anchor < warmup_bars:
        return {"timeframe": anchor_tf.value, "bars": bars_anchor, "required": warmup_bars}
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_warmup_deficit_mtf.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Gate + commit**

```bash
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src ; .venv\Scripts\python.exe -m pytest tests/unit/test_warmup_deficit_mtf.py -q
```
Expected: ruff clean, mypy `Success`, pytest 3 passed.

```
git add src/rtrade/pipeline/scan.py tests/unit/test_warmup_deficit_mtf.py
git commit -F COMMIT_MSG_TMP.txt
```
Message: `feat(sp2): generalized per-timeframe warmup helper _warmup_deficit_mtf`

---

## Task 4: `run_scan` MTF routing + `_run_strategies` H4-bias filter

**Files:**
- Modify: `src/rtrade/pipeline/scan.py` (imports; `run_scan` routing/anchor/bias block at `:232-272`; `_run_strategies` signature + bias filter + entry-tf wiring)
- Test: `tests/unit/test_scan_mtf_routing.py`

**Interfaces:**
- Consumes: `InstrumentConfig.resolved_entry_timeframes/resolved_anchor_timeframe`, `_warmup_deficit_mtf`, `mtf.h4_trend_bias`, `mtf.aligned`, `Action`.
- Produces:
  - `_is_entry_timeframe(instrument: InstrumentConfig, tf: Timeframe) -> bool` — pure routing predicate.
  - `run_scan` runs the FULL pipeline iff `tf` is a resolved entry tf, ingests-only otherwise (incl. the anchor tf); loads the anchor df, computes the bias, and passes `entry_tf` + `bias` + `enforce_bias` into `_run_strategies`.
  - `_run_strategies(..., *, entry_tf: Timeframe = Timeframe.H1, bias: Literal["UP","DOWN","NONE"] = "NONE", enforce_bias: bool = False, ...)` — drops any candidate whose `action` disagrees with `bias` (audited `ok=False`) **only when `enforce_bias`**; uses `entry_tf` as the candidate timeframe.
- Back-compat: defaults (`enforce_bias=False`, `entry_tf=H1`) keep the legacy H1 path and existing `tests/unit/test_scan_post_llm_gate.py` unchanged.
- Judgment call (documented in Self-Review): `RegimeClassifier` hysteresis is keyed by `symbol` only; with two entry tfs on one symbol the regime state is shared across M5/M15. Left as-is for this plan (minimal change); flagged as a follow-up.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_scan_mtf_routing.py
"""SP-2: entry/anchor routing predicate + H4-bias rejection in _run_strategies.

Mirrors tests/unit/test_scan_post_llm_gate.py: drives _run_strategies directly
with monkeypatched candidate generation. Deterministic, no DB, no network.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from rtrade.core.config import AppConfig, InstrumentConfig
from rtrade.core.constants import Action, Market, Regime, Timeframe
import rtrade.pipeline.scan as scan_mod
from rtrade.signals.schemas import ConfluenceBreakdown, LevelSet, SignalCandidate
from rtrade.strategies import StrategyConfig

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def _inst(**over: object) -> InstrumentConfig:
    base: dict[str, object] = {
        "symbol": "XAUUSD",
        "market": Market.METALS,
        "provider": "oanda",
        "provider_symbol": "XAU_USD",
        "timeframes": [Timeframe.M5, Timeframe.M15, Timeframe.H4],
        "context_timeframe": Timeframe.D1,
        "pip_size": 0.01,
        "quote_currency": "USD",
    }
    base.update(over)
    return InstrumentConfig(**base)  # type: ignore[arg-type]


def test_is_entry_timeframe_default_is_h1_only() -> None:
    inst = _inst(timeframes=[Timeframe.H1, Timeframe.H4])
    assert scan_mod._is_entry_timeframe(inst, Timeframe.H1) is True
    assert scan_mod._is_entry_timeframe(inst, Timeframe.H4) is False


def test_is_entry_timeframe_mtf_configured() -> None:
    inst = _inst(entry_timeframes=[Timeframe.M5, Timeframe.M15], anchor_timeframe=Timeframe.H4)
    assert scan_mod._is_entry_timeframe(inst, Timeframe.M5) is True
    assert scan_mod._is_entry_timeframe(inst, Timeframe.M15) is True
    assert scan_mod._is_entry_timeframe(inst, Timeframe.H4) is False


def _make_candidate(action: Action) -> SignalCandidate:
    return SignalCandidate(
        candidate_id="mtf_001",
        symbol="XAUUSD",
        timeframe=Timeframe.M5,
        strategy="fake_strat",
        action=action,
        levels=LevelSet(entry_limit=2700.0, stop_loss=2690.0, take_profit=2720.0, atr_at_signal=5.0),
        confluence_score=70,
        confluence_breakdown=ConfluenceBreakdown(trend=20, momentum=15, structure=15, volume=10, macro=10),
        risk_pct=1.0,
        position_size=0.5,
        valid_until=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        bar_ts=datetime(2026, 7, 1, 6, 0, tzinfo=UTC),
        created_at=datetime(2026, 7, 1, 6, 0, 30, tzinfo=UTC),
    )


class _FakeStrategy:
    required_regime = Regime.TREND


class _FakeRepo:
    def __init__(self) -> None:
        self.added: list[Any] = []

    async def is_enabled(self, _name: str) -> bool:
        return True

    async def recent_outcomes(self, *_a: Any, **_k: Any) -> list[float]:
        return []

    async def get_by_dedup(self, **_k: Any) -> None:
        return None

    async def count_since(self, **_k: Any) -> int:
        return 0

    async def resolved_with_features(self, *_a: Any, **_k: Any) -> list[Any]:
        return []

    async def add(self, model: Any) -> None:
        self.added.append(model)

    async def set_state(self, *_a: Any, **_k: Any) -> None:
        return None


class _FakeAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def add(self, *, stage: str, ok: bool, signal_id: str | None = None, detail: Any = None) -> None:
        self.entries.append({"stage": stage, "ok": ok, "detail": detail})


def _build_cfg() -> AppConfig:
    cfg = AppConfig.load(config_dir=_CONFIG_DIR, env_file=None)
    cfg.settings.llm.enabled = False
    cfg.settings.signal.edge_quality.enabled = False
    return cfg


async def _run(cfg: AppConfig, candidate: SignalCandidate, *, bias: str, enforce_bias: bool) -> tuple[Any, _FakeAudit]:
    instrument = cfg.instrument("XAUUSD")
    repo = _FakeRepo()
    audit = _FakeAudit()
    result = await scan_mod._run_strategies(
        cfg,
        instrument,
        instrument_id=1,
        df_1h=pd.DataFrame(),
        df_4h=None,
        sr_levels=[],
        gap_zones=[],
        event_dicts=[],
        in_news_blackout=False,
        regime=SimpleNamespace(regime=Regime.TREND),
        live_price=candidate.levels.entry_limit,
        session_repo=repo,  # type: ignore[arg-type]
        state_repo=repo,  # type: ignore[arg-type]
        audit_repo=audit,  # type: ignore[arg-type]
        now=datetime(2026, 7, 1, 6, 30, tzinfo=UTC),
        calendar_stale=False,
        entry_tf=Timeframe.M5,
        bias=bias,  # type: ignore[arg-type]
        enforce_bias=enforce_bias,
    )
    return result, audit


def _patch_common(monkeypatch: pytest.MonkeyPatch, candidate: SignalCandidate) -> None:
    monkeypatch.setattr(scan_mod, "STRATEGY_REGISTRY", {"fake_strat": _FakeStrategy})
    monkeypatch.setattr(scan_mod, "_load_strategy_config", lambda _n: StrategyConfig(raw={}))
    monkeypatch.setattr(scan_mod, "generate_candidate", lambda *a, **k: candidate)


@pytest.mark.asyncio
async def test_bias_aligned_candidate_publishes(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = _make_candidate(Action.BUY)
    _patch_common(monkeypatch, candidate)
    result, _audit = await _run(_build_cfg(), candidate, bias="UP", enforce_bias=True)
    assert result.status == "published"


@pytest.mark.asyncio
async def test_bias_misaligned_candidate_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = _make_candidate(Action.BUY)
    _patch_common(monkeypatch, candidate)
    result, audit = await _run(_build_cfg(), candidate, bias="DOWN", enforce_bias=True)
    assert result.status != "published"
    assert result.signal_id is None
    assert any(
        e["ok"] is False and isinstance(e["detail"], dict)
        and e["detail"].get("rejected") == "h4_bias_misaligned"
        for e in audit.entries
    )


@pytest.mark.asyncio
async def test_bias_not_enforced_when_back_compat(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = _make_candidate(Action.BUY)
    _patch_common(monkeypatch, candidate)
    # enforce_bias False (legacy H1) → misaligned bias is ignored, candidate publishes.
    result, _audit = await _run(_build_cfg(), candidate, bias="DOWN", enforce_bias=False)
    assert result.status == "published"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_scan_mtf_routing.py -q`
Expected: FAIL — `AttributeError: module 'rtrade.pipeline.scan' has no attribute '_is_entry_timeframe'` and `_run_strategies() got an unexpected keyword argument 'entry_tf'`.

- [ ] **Step 3: Update imports in `scan.py`**

In the `from typing import Any` line (`:11`), change to:

```python
from typing import Any, Literal
```

In the constants import block (`:17-23`), add `Action`:

```python
from rtrade.core.constants import (
    FUNDING_EXTREME_ABS,
    Action,
    AuditStage,
    Market,
    SignalStatus,
    Timeframe,
)
```

Add an import next to the other pipeline imports (after the `from rtrade.papertrack...` group, before `from rtrade.persistence.db ...`):

```python
from rtrade.pipeline.mtf import aligned, h4_trend_bias
```

- [ ] **Step 4: Add the routing predicate**

In `src/rtrade/pipeline/scan.py`, directly after `_warmup_deficit_mtf` (added in Task 3), add:

```python
def _is_entry_timeframe(instrument: InstrumentConfig, tf: Timeframe) -> bool:
    """True when ``tf`` is one of the instrument's resolved entry timeframes.

    Legacy default (no entry_timeframes configured) → only H1 is an entry tf,
    preserving the original H1-entry / H4-context pipeline.
    """
    return tf in instrument.resolved_entry_timeframes()
```

- [ ] **Step 5: Replace the routing/anchor/warmup block in `run_scan`**

Replace the block currently at `src/rtrade/pipeline/scan.py:232-272` (from `await _ingest_incremental(provider, instrument, inst_row.id, tf, candle_repo, now)` through the `regime = _REGIME_CLASSIFIER.classify(symbol, df_1h)` line) with:

```python
            await _ingest_incremental(provider, instrument, inst_row.id, tf, candle_repo, now)

            entry_tfs = instrument.resolved_entry_timeframes()
            anchor_tf = instrument.resolved_anchor_timeframe()
            mtf_mode = bool(instrument.entry_timeframes)

            # Ingest-only for any non-entry timeframe (incl. the anchor tf).
            if not _is_entry_timeframe(instrument, tf):
                await session.commit()
                return ScanResult(
                    symbol=symbol,
                    timeframe=tf.value,
                    status="ingested_context_only",
                    detail={"timeframe": tf.value},
                )

            # Refresh the anchor tf so the trend bias is current.
            if anchor_tf != tf and anchor_tf in instrument.timeframes:
                latest_anchor = await candle_repo.latest(inst_row.id, anchor_tf)
                due_anchor = latest_anchor is None or (
                    ensure_utc(latest_anchor.ts) + 2 * timeframe_duration(anchor_tf) <= now
                )
                if due_anchor:
                    await _ingest_incremental(
                        provider, instrument, inst_row.id, anchor_tf, candle_repo, now
                    )

            warmup_bars = cfg.settings.signal.warmup_bars
            load_n = max(500, warmup_bars)
            df_1h = _candles_to_df(await candle_repo.latest_n(inst_row.id, tf, load_n))
            df_4h = _candles_to_df(await candle_repo.latest_n(inst_row.id, anchor_tf, load_n))

            deficit = _warmup_deficit_mtf(
                bars_entry=len(df_1h),
                entry_tf=tf,
                bars_anchor=len(df_4h),
                anchor_tf=anchor_tf,
                warmup_bars=warmup_bars,
            )
            if deficit is not None:
                await session.commit()
                return ScanResult(
                    symbol=symbol,
                    timeframe=tf.value,
                    status="abstain_warmup",
                    detail=deficit,
                )

            # H4 trend bias from the raw anchor closes (pre-indicator; only needs close).
            bias = h4_trend_bias(df_4h)

            df_1h = await asyncio.to_thread(compute_indicators, df_1h)
            df_4h_ind = (
                await asyncio.to_thread(compute_indicators, df_4h) if not df_4h.empty else None
            )
            # A1: regime classify is intentionally NOT offloaded. _REGIME_CLASSIFIER is a
            # process-scoped shared singleton whose classify() mutates per-symbol hysteresis
            # state (self._prev); running it in the threadpool could race on that shared dict
            # if scan jobs overlap. Keeping it on the single event loop serializes the
            # mutation safely. Indicators/structure ARE offloaded (pure, no shared state).
            regime = _REGIME_CLASSIFIER.classify(symbol, df_1h)
```

Notes for the implementer:
- The internal variable names `df_1h` / `df_4h` / `df_4h_ind` are retained on purpose so the downstream `run_scan` body (swings on `df_1h.tail(200)`, `atr = float(df_1h.iloc[-1]...)`, the `_run_strategies` call, the HMM shadow on `df_1h`) needs no further edits — `df_1h` now holds the **entry-tf** frame and `df_4h` the **anchor-tf** frame.
- `detail` for `abstain_warmup` now carries `{"timeframe", "bars", "required"}` (generic across tfs) rather than the legacy `bars_1h`/`bars_4h` keys. If any existing run-scan test asserts the old keys, update it to the new schema.

- [ ] **Step 6: Thread `entry_tf` + `bias` + `enforce_bias` into the `_run_strategies` call**

In `run_scan`, extend the existing `result = await _run_strategies(...)` call (`:361-381`) — add three keyword args alongside the current ones (e.g. after `spread=spread,`):

```python
                entry_tf=tf,
                bias=bias,
                enforce_bias=mtf_mode,
```

- [ ] **Step 7: Add the params + bias filter to `_run_strategies`**

In the `_run_strategies` signature (`:807-828`), add to the keyword-only block (after `spread: float | None = None,`):

```python
    entry_tf: Timeframe = Timeframe.H1,
    bias: Literal["UP", "DOWN", "NONE"] = "NONE",
    enforce_bias: bool = False,
```

Replace the hard-coded `timeframe=Timeframe.H1,` in the `generate_candidate(...)` call (`:888`) with:

```python
            timeframe=entry_tf,
```

Immediately after the `if candidate is None:\n    continue` block that follows `generate_candidate`, insert the bias filter:

```python
        if enforce_bias and not aligned(bias, candidate.action):
            await audit_repo.add(
                stage=AuditStage.CANDIDATE.value,
                ok=False,
                signal_id=candidate.candidate_id,
                detail={
                    "rejected": "h4_bias_misaligned",
                    "bias": bias,
                    "action": candidate.action.value,
                    "entry_tf": entry_tf.value,
                },
            )
            logger.info(
                "candidate rejected: H4 bias misaligned",
                strategy=strategy_name,
                bias=bias,
                action=candidate.action.value,
            )
            continue
```

- [ ] **Step 8: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_scan_mtf_routing.py tests/unit/test_scan_post_llm_gate.py -q`
Expected: PASS (5 in routing + the 2 legacy post-LLM tests still green).

- [ ] **Step 9: Gate + commit**

```bash
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src ; .venv\Scripts\python.exe -m pytest tests -q
```
Expected: ruff clean, mypy `Success`, full suite green. If a legacy run-scan test asserts the old `abstain_warmup` detail keys, update it to `{"timeframe","bars","required"}` (the only intended behavior change).

```
git add src/rtrade/pipeline/scan.py tests/unit/test_scan_mtf_routing.py
git commit -F COMMIT_MSG_TMP.txt
```
Message: `feat(sp2): run_scan MTF routing (entry tf full / anchor ingest) + H4-bias filter`

---

## Task 5: Scheduler — M5/M15 cron mappings in `build_scan_schedules`

**Files:**
- Modify: `src/rtrade/scheduler/main.py` (`build_scan_schedules`)
- Test: `tests/unit/test_scheduler_mtf_schedules.py`

**Interfaces:**
- Consumes: `InstrumentConfig.timeframes`, `Timeframe`.
- Produces: `build_scan_schedules` now maps `Timeframe.M5 → {"minute": "*/5", "second": <stagger>}` and `Timeframe.M15 → {"minute": "*/15", "second": <stagger>}` (candle-close + ~30s buffer via the existing `second` stagger). The existing H4 branch already emits the anchor ingest job; the per-tf scan job count invariant (one entry per instrument×tf) is preserved.
- Note: M5/M15 instruments are OANDA in this spec (not TwelveData), so they use the original second-stagger path; the TwelveData minute-spread is untouched.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_scheduler_mtf_schedules.py
from __future__ import annotations

from rtrade.core.config import InstrumentConfig
from rtrade.core.constants import Market, Timeframe
from rtrade.scheduler.main import build_scan_schedules


def _xau_mtf() -> InstrumentConfig:
    return InstrumentConfig(
        symbol="XAUUSD",
        market=Market.METALS,
        provider="oanda",
        provider_symbol="XAU_USD",
        timeframes=[Timeframe.M5, Timeframe.M15, Timeframe.H4],
        context_timeframe=Timeframe.D1,
        pip_size=0.01,
        quote_currency="USD",
        entry_timeframes=[Timeframe.M5, Timeframe.M15],
        anchor_timeframe=Timeframe.H4,
    )


def test_one_entry_per_timeframe() -> None:
    schedules = build_scan_schedules([_xau_mtf()])
    assert len(schedules) == 3  # M5, M15, H4


def test_m5_runs_every_five_minutes() -> None:
    schedules = build_scan_schedules([_xau_mtf()])
    m5 = next(cron for _s, tf, cron in schedules if tf == "5m")
    assert m5["minute"] == "*/5"
    assert "second" in m5


def test_m15_runs_every_fifteen_minutes() -> None:
    schedules = build_scan_schedules([_xau_mtf()])
    m15 = next(cron for _s, tf, cron in schedules if tf == "15m")
    assert m15["minute"] == "*/15"


def test_h4_anchor_keeps_six_hour_grid() -> None:
    schedules = build_scan_schedules([_xau_mtf()])
    h4 = next(cron for _s, tf, cron in schedules if tf == "4h")
    assert h4["hour"] == "0,4,8,12,16,20"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_scheduler_mtf_schedules.py -q`
Expected: FAIL — M5/M15 currently fall through to the D1 `else` branch, so `m5["minute"] == "1"` (not `"*/5"`).

- [ ] **Step 3: Add M5/M15 branches**

In `src/rtrade/scheduler/main.py`, inside `build_scan_schedules`, extend the per-tf `if/elif` chain. Insert the two new branches **before** the final `else:  # D1` branch:

```python
            elif tf == Timeframe.M5:
                cron = {"minute": "*/5", "second": second}
            elif tf == Timeframe.M15:
                cron = {"minute": "*/15", "second": second}
            else:  # D1
                cron = {"minute": "1", "second": second, "hour": "0"}
```

(Only the two `elif` lines are new; the existing `H1`, `H4`, and `else  # D1` branches are unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_scheduler_mtf_schedules.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Gate + commit**

```bash
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src ; .venv\Scripts\python.exe -m pytest tests/unit/test_scheduler_mtf_schedules.py tests/unit/test_scheduler_build.py -q
```
Expected: all green (existing scheduler tests still pass — H1/H4/TwelveData behavior unchanged).

```
git add src/rtrade/scheduler/main.py tests/unit/test_scheduler_mtf_schedules.py
git commit -F COMMIT_MSG_TMP.txt
```
Message: `feat(sp2): scheduler M5/M15 cron mappings (candle-close + buffer)`

---

## Task 6: Wire `config/instruments.yaml` (XAUUSD M5/M15 + anchor) + full gate

**Files:**
- Modify: `config/instruments.yaml` (XAUUSD entry)
- Test: reuses Tasks 1-5; this task's deliverable is a green full gate with XAUUSD configured for MTF.

**Interfaces:**
- Consumes: everything above (resolvers + validator + routing + scheduler).
- Produces: XAUUSD runs M5/M15 entries anchored on H4 in the live config; load-time validation guarantees entry/anchor tfs are members of `timeframes`.

- [ ] **Step 1: Point XAUUSD at M5/M15 + H4 anchor**

In `config/instruments.yaml`, update the XAUUSD block so `timeframes` includes the entry + anchor tfs and the new MTF fields are set (SP-1 already set `provider: oanda` / `provider_symbol: "XAU_USD"`):

```yaml
  - symbol: XAUUSD
    market: metals
    provider: oanda
    provider_symbol: "XAU_USD"
    timeframes: ["5m", "15m", "4h"]
    context_timeframe: "1d"
    entry_timeframes: ["5m", "15m"]
    anchor_timeframe: "4h"
    pip_size: 0.01
    quote_currency: USD
    related_currencies: [USD]
    session_filter: true
```

- [ ] **Step 2: Verify the config loads and resolves**

Run:
```bash
.venv\Scripts\python.exe -c "from rtrade.core.config import AppConfig; i=AppConfig.load().instrument('XAUUSD'); print(i.resolved_entry_timeframes(), i.resolved_anchor_timeframe())"
```
Expected output: `[<Timeframe.M5: '5m'>, <Timeframe.M15: '15m'>] Timeframe.H4`

- [ ] **Step 3: Full gate**

```bash
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src ; .venv\Scripts\python.exe -m pytest tests -q
```
Expected: ruff clean, mypy `Success`, full suite green. Before committing, clear the stray Windows `nul` artifact:
```bash
cmd /c 'if exist nul del "\\?\%CD%\nul"'
```

- [ ] **Step 4: Commit**

```
git add config/instruments.yaml
git commit -F COMMIT_MSG_TMP.txt
```
Message: `feat(sp2): enable XAUUSD M5/M15 entry timeframes anchored on H4`

---

## Self-Review (completed by plan author)

**1. Spec coverage (SP-2 section §7 of design):**
- `pipeline/mtf.py` pure `h4_trend_bias` + `aligned` (§7.2, §7.3 PINNED) → Task 1. ✅
- `InstrumentConfig.entry_timeframes` / `anchor_timeframe` (§7.2 PINNED) + back-compat resolvers → Task 2. ✅
- Warmup per entry tf AND anchor tf (§7.2, P1-7) → Task 3 (`_warmup_deficit_mtf`) + applied in Task 4. ✅
- `run_scan` routing: full pipeline for entry tf, ingest-only for anchor/others; load anchor df + bias; only aligned candidates proceed (§7.2) → Task 4. ✅
- Scheduler M5 every 5 min, M15 every 15 min, anchor H4 ingest job, idempotency via `signals` unique constraint (§7.2) → Task 5 (H4 ingest job already emitted by the existing H4 branch). ✅
- `instruments.yaml` XAUUSD M5/M15/H4 (§7.2 / §6.3) → Task 6. ✅
- Tests: bias on synthetic frames, entry routing via monkeypatch (mirrors `test_scan_post_llm_gate.py`), warmup per tf (§7.4) → Tasks 1,3,4,5. ✅

**2. Placeholder scan:** No TBD/TODO. Every code step is complete, typed, and paired with exact commands + expected output.

**3. Type consistency (PINNED interfaces identical everywhere they appear):**
- `h4_trend_bias(df_h4: pd.DataFrame) -> Literal["UP","DOWN","NONE"]` — defined Task 1, consumed in Task 4 (`bias = h4_trend_bias(df_4h)`).
- `aligned(bias: Literal["UP","DOWN","NONE"], action: Action) -> bool` — defined Task 1, consumed in Task 4 (`aligned(bias, candidate.action)`).
- `InstrumentConfig.entry_timeframes: list[Timeframe]` (default `[]`) and `anchor_timeframe: Timeframe | None` (default `None`) — defined Task 2, consumed in Tasks 4 (`resolved_*`, `bool(instrument.entry_timeframes)`), 5, 6.
- `_warmup_deficit_mtf(*, bars_entry, entry_tf, bars_anchor, anchor_tf, warmup_bars)` consistent Tasks 3 → 4.
- `_run_strategies(..., *, entry_tf=Timeframe.H1, bias="NONE", enforce_bias=False)` defaults preserve the legacy call site in `tests/unit/test_scan_post_llm_gate.py`.

**4. Final pinned signatures + chosen defaults:**
- `h4_trend_bias(df_h4: pd.DataFrame) -> Literal["UP", "DOWN", "NONE"]`
- `aligned(bias: Literal["UP", "DOWN", "NONE"], action: Action) -> bool`
- `InstrumentConfig.entry_timeframes: list[Timeframe] = Field(default_factory=list)`
- `InstrumentConfig.anchor_timeframe: Timeframe | None = None`
- Resolver defaults: empty `entry_timeframes` → `[Timeframe.H1]`; `anchor_timeframe is None` → `Timeframe.H4`.

**5. Judgment calls (flagged for the executor/reviewer):**
- **`h4_trend_bias` is self-contained EMA-slope** (fast EMA 20 / slow EMA 50 + 10-bar slope, `_MIN_BARS = 60`) rather than calling `RegimeClassifier`. The spec said "EMA-slope + regime based," but `RegimeClassifier.classify` requires `adx`/`atr_percentile` columns (not present on hand-built synthetic frames) and mutates shared hysteresis state — both at odds with a pure, deterministically-testable helper. Regime gating still happens at the scan level via the entry-tf `RegimeClassifier` + `Strategy.required_regime`; the anchor bias is intentionally a lightweight directional filter. Thresholds are module constants, easy to tune in SP-4 if backtests prefer different spans.
- **Bias enforcement is gated on `mtf_mode = bool(instrument.entry_timeframes)`** so the legacy H1 path is byte-for-byte unchanged (it never filtered by H4 bias before). Enabling bias filtering on H1 would change existing behavior/tests, which the back-compat constraint forbids.
- **`abstain_warmup` detail schema changed** from `{"bars_1h","bars_4h","required"}` to the generic `{"timeframe","bars","required"}`. `_warmup_deficit` is kept (its own unit/property test stays green); only `run_scan` switches to `_warmup_deficit_mtf`. Any run-scan-level test asserting the old keys must be updated (called out in Task 4 Step 9).
- **RegimeClassifier hysteresis is keyed by `symbol` only.** With two entry tfs on one symbol (M5 + M15) the regime `_prev` state is shared across them. Left unchanged here to keep the diff minimal and avoid altering `get_previous(symbol)` semantics; recommended follow-up (key by `symbol:tf`) noted for SP-4/SP-6.
- **Anchor ingest job** is not a *new* scheduler entry — the existing H4 branch in `build_scan_schedules` already emits an H4 scan job, and `run_scan("XAUUSD","4h")` now returns `ingested_context_only` (the anchor ingest). No separate job type was added, matching "idempotency already guaranteed by the `signals` unique constraint."

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-20-sp2-mtf-engine.md`.
This is **SP-2 of 6** (depends on SP-1's data layer). Recommended execution: **subagent-driven-development** (fresh subagent per task + two-stage review), one task at a time, full gate green before advancing. SP-3 (SMC indicators) and SP-4 (scalping strategies) consume the PINNED `h4_trend_bias` / `aligned` / `InstrumentConfig.entry_timeframes` interfaces frozen here.
