# SP-4: Scalping Strategies (S3 MTF-Confluence + S4 SMC/ICT) + Scalping Gate Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the two scalping signal producers — `S3MtfScalper` (MTF EMA20/VWAP pullback + momentum + structure) and `S4SmcScalper` (liquidity-sweep + BOS/CHoCH into order-block/FVG) — plus a config-driven **scalping gate profile** that loosens only the soft thresholds (confluence / edge-quality / confidence / signals-per-day) while every hard risk floor stays globally validated, so XAUUSD M5/M15 can emit signals without weakening safety, fully type/lint/test clean.

**Architecture:** Two new `Strategy` subclasses live in `strategies/s3_mtf_scalper.py` and `strategies/s4_smc_scalper.py`, mirroring `s1_trend_pullback.py`/`s2_range_mr.py` exactly (callback methods + `df.attrs` config plumbing + `STRATEGY_REGISTRY` registration). Both have `required_regime = Regime.TREND` and emit only entry-timeframe candidates; the SP-2 scan layer (`_run_strategies` with `enforce_bias=True`) drops any candidate whose action disagrees with the H4 trend bias, so the strategies never re-check the anchor. S4 consumes the pinned `rtrade.indicators.smc` detectors from SP-3; S3 reuses `detect_swing_points` and (optionally) `rtrade.strategies.filters` from SP-5. The scalping gate profile is a new `signal.profiles` map in `core/config.py` (`GateProfile` model holding the 4 soft thresholds); `_run_strategies` selects the active profile per strategy via a `gate_profile:` key in each strategy YAML and feeds the profile's values into `generate_candidate` (confluence floor), the edge-quality config (min_score), `run_gate` (max signals/day) and the post-LLM gate (confidence floor). A `_strategy_applies` predicate confines S3/S4 to XAUUSD entry timeframes via `instruments:`/`entry_timeframes:` allowlists in their YAML. Hard floors (rr_min, sl_atr, risk_per_trade_pct, news blackout, `llm.enabled`) are NEVER part of a profile and remain in `RiskSettings`/`LLMSettings`, validated as today.

**Tech Stack:** Python 3.12, pandas/numpy (EMA via `Series.ewm`, rolling VWAP), pydantic v2 (`GateProfile` model + validator), pytest (deterministic synthetic frames; scan-level wiring via the `monkeypatch` pattern from `tests/unit/test_scan_post_llm_gate.py`). No new runtime dependency.

## Global Constraints

(Copied verbatim from the design spec §5 — every task's requirements implicitly include this section.)

- **Signal-only** — no order/broker placement, ever.
- **Hard risk floors (config-loader enforced, never weakened):** GR-03 `rr_min ≥ 1.5`; GR-04 `sl_atr ∈ [0.5, 3.0]`; GR-05 `risk_per_trade_pct ≤ 2.0`. SP-4 adds NO hard-floor change; profiles carry only soft thresholds.
- **News blackout (GR-07)** applies to ALL timeframes incl. M5/M15 — enforced inside `run_gate`/`_run_strategies`, NOT part of any profile.
- **Calendar fail-CLOSE:** `calendar.fail_open_when_stale = false`.
- **`llm.enabled = false`**; GI-5: no `model_construct` on the production path.
- **Warmup guarantee (P1-7):** abstain (`abstain_warmup`) until a full warmup window exists, per entry timeframe AND the anchor timeframe (enforced upstream in SP-2's `run_scan`).
- **Determinism in tests:** `freezegun`/`respx`/synthetic frames + `monkeypatch`; no live network; integration tests skip when the live stack is unreachable. SP-4 strategy logic is pure → tested on hand-built frames only.
- **Toolchain (run via venv):** `.venv\Scripts\python.exe -m <tool>`. Gate per task: `ruff check src tests` ; `ruff format src tests` ; `mypy --strict src` ; `pytest tests -q`.
- **Commits:** message via `COMMIT_MSG_TMP.txt` + `git commit -F COMMIT_MSG_TMP.txt`, then delete the temp file. Before commit run `cmd /c 'if exist nul del "\\?\%CD%\nul"'`. No push unless explicitly requested.
- **Min trades floor:** `backtest.min_trades_for_validation ≥ 100` (never lower) — SP-4 strategies are validated in SP-6.
- Follow existing file conventions (`from __future__ import annotations`, structlog where logging, `df.attrs` for strategy params, frozen `LevelSet`, lowercase OHLC columns, `float(...)` on returned scalars).

---

## Upstream pinned interfaces consumed (do NOT redefine — import by exact name)

- **SP-2 `rtrade.pipeline.mtf`:** `h4_trend_bias(df_h4) -> Literal["UP","DOWN","NONE"]`, `aligned(bias, action) -> bool`. The scan layer already enforces H4-bias alignment: `run_scan` computes the bias and calls `_run_strategies(..., entry_tf=tf, bias=bias, enforce_bias=mtf_mode)`; with `enforce_bias=True` any candidate whose `action` disagrees with `bias` is audited `ok=False` and dropped. **S3/S4 therefore emit entry-timeframe candidates in EITHER direction and let the scan layer drop the misaligned one — they do not load or inspect H4 themselves.**
- **SP-2 `InstrumentConfig`:** `resolved_entry_timeframes() -> list[Timeframe]`, `resolved_anchor_timeframe() -> Timeframe`, fields `entry_timeframes` / `anchor_timeframe`. XAUUSD is configured with `entry_timeframes: ["5m","15m"]`, `anchor_timeframe: "4h"`.
- **SP-2 `_run_strategies` signature additions (already present):** keyword-only `entry_tf: Timeframe = Timeframe.H1`, `bias: Literal["UP","DOWN","NONE"] = "NONE"`, `enforce_bias: bool = False`; the `generate_candidate(...)` call passes `timeframe=entry_tf`. SP-4 adds profile selection + applicability filtering inside the same function.
- **SP-3 `rtrade.indicators.smc`:** dataclasses `FairValueGap{start_idx,end_idx,top,bottom,direction}`, `OrderBlock{idx,top,bottom,direction}`, `LiquiditySweep{idx,level,side}`, `StructureEvent{idx,kind,direction}`; functions `fair_value_gaps(df)`, `order_blocks(df, *, swing_lookback=5)`, `liquidity_sweeps(df, *, swing_lookback=5)`, `market_structure(df, *, swing_lookback=5)`.
- **SP-5 `rtrade.strategies.filters`:** `supertrend`, `supertrend_flip`, `choppiness_index`, `adx_ok`, `bollinger_touch`, `keltner_touch`, `rsi_divergence` — available for reuse in S3 (used as documented opt-in confluence; SP-4's deterministic gate does not hard-depend on them).
- **Existing real code mirrored:** `strategies/base.py` (`Strategy` ABC: `name`, `required_regime`, `populate_indicators`, `entry_signal`, `custom_entry_price`, `confirm_signal`; `StrategyConfig.get/get_int/get_float`), `signals/engine.py::generate_candidate` (consumes `confluence_min_score`, `edge_quality_enabled`, `edge_quality_config`, `timeframe`), `signals/schemas.py` (`LevelSet`, `SignalCandidate`, `ConfluenceBreakdown` — frozen; validators enforce GR-02/03/04), `pipeline/scan.py::_run_strategies` (reads `cfg.settings.signal.confluence_min_score`, `confidence_min`, `edge_quality.min_score`, `max_signals_per_day_per_instrument`).

---

## File Structure

- Modify: `src/rtrade/core/config.py` — add `GateProfile(_StrictModel)`; add `SignalSettings.profiles: dict[str, GateProfile]` + `_ensure_default_profile` validator + `profile(name)` accessor.
- Modify: `config/settings.yaml` — add `signal.profiles` (`default` = current values, `scalping` = permissive).
- Modify: `src/rtrade/pipeline/scan.py` — add `_active_profile`, `_strategy_applies`; extend `_edge_quality_config` with a `min_score` override; wire profile + applicability into `_run_strategies`.
- Create: `src/rtrade/strategies/s3_mtf_scalper.py` — `S3MtfScalper(Strategy)`.
- Create: `src/rtrade/strategies/s4_smc_scalper.py` — `S4SmcScalper(Strategy)`.
- Modify: `src/rtrade/strategies/__init__.py` — register both in `STRATEGY_REGISTRY` + `__all__`.
- Create: `config/strategies/s3_mtf_scalper.yaml`, `config/strategies/s4_smc_scalper.yaml`.
- Test: `tests/unit/test_signal_profiles.py`, `tests/unit/test_scan_gate_profile.py`, `tests/unit/test_s3_mtf_scalper.py`, `tests/unit/test_s4_smc_scalper.py`, `tests/unit/test_scan_strategy_applies.py`.

---

## Task 1: `GateProfile` model + `SignalSettings.profiles` (config schema)

**Files:**
- Modify: `src/rtrade/core/config.py` (add `GateProfile`; extend `SignalSettings`)
- Modify: `config/settings.yaml` (add `signal.profiles`)
- Test: `tests/unit/test_signal_profiles.py`

**Interfaces:**
- Consumes: existing `_StrictModel`, `SignalSettings`, `EdgeQualitySettings`, pydantic `Field`/`model_validator`.
- Produces (PINNED — Task 2 reads these):
  - `GateProfile(_StrictModel)` with `confluence_min_score: int [0..100]`, `edge_quality_min_score: int [0..100]`, `confidence_min: float [0..1]`, `max_signals_per_day_per_instrument: int >=1`.
  - `SignalSettings.profiles: dict[str, GateProfile]` (default empty).
  - `SignalSettings.profile(name: str) -> GateProfile` — returns the named profile, else the `default` profile.
  - `_ensure_default_profile` `model_validator(mode="after")` — synthesizes `profiles["default"]` from the existing global `confluence_min_score`/`edge_quality.min_score`/`confidence_min`/`max_signals_per_day_per_instrument` when absent, so configs WITHOUT a `profiles:` block stay byte-compatible.
- Note: hard floors are NOT fields of `GateProfile`. Only the 4 soft thresholds are profileable.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_signal_profiles.py
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from rtrade.core.config import AppConfig, GateProfile, SignalSettings

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def _signal(**over: object) -> SignalSettings:
    base: dict[str, object] = {
        "confluence_min_score": 60,
        "confidence_min": 0.55,
        "max_signals_per_day_per_instrument": 3,
        "price_drift_max_pct": 0.5,
        "candle_staleness_factor": 2,
        "edge_quality": {
            "enabled": True,
            "min_score": 65,
            "max_spread_atr": 0.12,
            "min_atr_percentile": 8,
            "max_atr_percentile": 96,
            "max_opposing_wick_ratio": 0.62,
            "max_total_wick_body_ratio": 6,
            "min_body_atr": 0.03,
            "min_volume_ratio": 0.55,
            "volume_window": 20,
            "max_range_expansion_atr": 2.8,
            "max_entry_distance_atr": 1.25,
        },
    }
    base.update(over)
    return SignalSettings.model_validate(base)


def test_default_profile_synthesized_from_globals_when_absent() -> None:
    s = _signal()
    prof = s.profile("default")
    assert prof.confluence_min_score == 60
    assert prof.edge_quality_min_score == 65
    assert prof.confidence_min == pytest.approx(0.55)
    assert prof.max_signals_per_day_per_instrument == 3


def test_unknown_profile_falls_back_to_default() -> None:
    s = _signal()
    assert s.profile("does_not_exist") == s.profile("default")


def test_explicit_profiles_are_preserved() -> None:
    s = _signal(
        profiles={
            "scalping": {
                "confluence_min_score": 50,
                "edge_quality_min_score": 55,
                "confidence_min": 0.50,
                "max_signals_per_day_per_instrument": 10,
            }
        }
    )
    scal = s.profile("scalping")
    assert scal.confluence_min_score == 50
    assert scal.max_signals_per_day_per_instrument == 10
    # default still synthesized from globals.
    assert s.profile("default").confluence_min_score == 60


def test_gate_profile_rejects_out_of_range() -> None:
    with pytest.raises(ValidationError):
        GateProfile(
            confluence_min_score=120,  # > 100
            edge_quality_min_score=55,
            confidence_min=0.5,
            max_signals_per_day_per_instrument=10,
        )


def test_shipped_config_has_default_and_scalping_profiles() -> None:
    cfg = AppConfig.load(config_dir=_CONFIG_DIR, env_file=None)
    sig = cfg.settings.signal
    assert sig.profile("default").confluence_min_score == sig.confluence_min_score
    scal = sig.profile("scalping")
    assert scal.confluence_min_score < sig.profile("default").confluence_min_score
    assert scal.max_signals_per_day_per_instrument >= sig.max_signals_per_day_per_instrument
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_signal_profiles.py -q`
Expected: FAIL — `ImportError: cannot import name 'GateProfile'` (and `profile`/`profiles` missing).

- [ ] **Step 3: Add `GateProfile` + extend `SignalSettings`**

In `src/rtrade/core/config.py`, add `GateProfile` directly ABOVE `class SignalSettings(_StrictModel)`:

```python
class GateProfile(_StrictModel):
    """Soft (non-floor) signal thresholds, swappable per strategy/timeframe.

    Hard risk floors (rr_min, sl_atr, risk_per_trade_pct, news blackout,
    llm.enabled) are deliberately NOT here — they stay globally validated in
    RiskSettings/LLMSettings. A profile only loosens/tightens the gates that are
    safe to vary between swing and scalping.
    """

    confluence_min_score: int = Field(ge=0, le=100)
    edge_quality_min_score: int = Field(ge=0, le=100)
    confidence_min: float = Field(ge=0.0, le=1.0)
    max_signals_per_day_per_instrument: int = Field(ge=1)
```

Then extend `class SignalSettings(_StrictModel)` — add the `profiles` field after `edge_quality: EdgeQualitySettings` and append the validator + accessor:

```python
    edge_quality: EdgeQualitySettings
    # SP-4: named soft-threshold profiles. `default` is auto-synthesized from the
    # global values above when omitted, so configs without a profiles block are
    # byte-compatible. Hard floors are never profileable.
    profiles: dict[str, GateProfile] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ensure_default_profile(self) -> "SignalSettings":
        if "default" not in self.profiles:
            self.profiles = {
                **self.profiles,
                "default": GateProfile(
                    confluence_min_score=self.confluence_min_score,
                    edge_quality_min_score=self.edge_quality.min_score,
                    confidence_min=self.confidence_min,
                    max_signals_per_day_per_instrument=self.max_signals_per_day_per_instrument,
                ),
            }
        return self

    def profile(self, name: str) -> GateProfile:
        """Return the named gate profile, falling back to `default`."""
        return self.profiles.get(name, self.profiles["default"])
```

(`Field` and `model_validator` are already imported in `config.py`.)

- [ ] **Step 4: Add the `signal.profiles` block to `config/settings.yaml`**

In `config/settings.yaml`, inside the `signal:` mapping, append AFTER the `edge_quality:` block (same indentation as `edge_quality:`):

```yaml
  profiles:                       # SP-4: soft-threshold profiles (NO hard floors here)
    default:                      # = current global values; used by swing S1/S2
      confluence_min_score: 60
      edge_quality_min_score: 65
      confidence_min: 0.55
      max_signals_per_day_per_instrument: 3
    scalping:                     # more permissive entry bar for M5/M15 scalpers
      confluence_min_score: 50
      edge_quality_min_score: 55
      confidence_min: 0.50
      max_signals_per_day_per_instrument: 10
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_signal_profiles.py -q`
Expected: PASS (5 passed). Then confirm legacy config still loads: `.venv\Scripts\python.exe -m pytest tests/unit/test_config*.py -q`.

- [ ] **Step 6: Gate + commit**

```
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src ; .venv\Scripts\python.exe -m pytest tests -q
```
Expected: ruff clean, mypy `Success`, full suite green.

```
cmd /c 'if exist nul del "\\?\%CD%\nul"'
git add src/rtrade/core/config.py config/settings.yaml tests/unit/test_signal_profiles.py
git commit -F COMMIT_MSG_TMP.txt
del COMMIT_MSG_TMP.txt
```
Message: `feat(sp4): GateProfile model + signal.profiles (default/scalping) config`

---

## Task 2: Wire profile selection into `_run_strategies` (TDD flip test)

**Files:**
- Modify: `src/rtrade/pipeline/scan.py` (add `_active_profile`; extend `_edge_quality_config`; wire profile values into `generate_candidate`, `run_gate`, post-LLM gate)
- Test: `tests/unit/test_scan_gate_profile.py`

**Interfaces:**
- Consumes: `GateProfile`, `SignalSettings.profile`, `StrategyConfig.get`, existing `generate_candidate`/`run_gate`/`_edge_quality_config`, and SP-2's `entry_tf`/`bias`/`enforce_bias` params already on `_run_strategies`.
- Produces:
  - `_active_profile(cfg: AppConfig, strategy_cfg: StrategyConfig) -> GateProfile` — reads the strategy YAML `gate_profile` key (default `"default"`) and returns `cfg.settings.signal.profile(name)`.
  - `_edge_quality_config(cfg: AppConfig, *, min_score: int | None = None) -> EdgeQualityConfig` — same as today but overrides `min_score` when given (back-compat: `None` keeps `cfg.settings.signal.edge_quality.min_score`).
  - `_run_strategies` selects the active profile per strategy and threads its 4 values into the deterministic floor (`confluence_min_score`), edge-quality (`min_score`), the gate (`max_signals_per_day`), and the post-LLM gate (`confidence_min`).
- Behavior contract (the property under test): with NO code change, swapping the active profile's `max_signals_per_day_per_instrument` flips a candidate published↔not-published (mirrors P1-6 "thresholds from config"). The wiring also passes the profile's `confluence_min_score` into `generate_candidate` (asserted via a spy).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_scan_gate_profile.py
"""SP-4: scalping gate profile selection in _run_strategies.

Mirrors tests/unit/test_scan_post_llm_gate.py — drives _run_strategies directly
with monkeypatched candidate generation + a fake repo. Deterministic, no DB, no
network. Proves a candidate flips published<->not-published purely by changing a
profile threshold (no code change), and that the profile's confluence floor is
threaded into generate_candidate.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from rtrade.core.config import AppConfig
from rtrade.core.constants import Action, Regime, Timeframe
import rtrade.pipeline.scan as scan_mod
from rtrade.signals.schemas import ConfluenceBreakdown, LevelSet, SignalCandidate
from rtrade.strategies import StrategyConfig

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def _make_candidate() -> SignalCandidate:
    return SignalCandidate(
        candidate_id="gp_001",
        symbol="XAUUSD",
        timeframe=Timeframe.M5,
        strategy="fake_scalper",
        action=Action.BUY,
        levels=LevelSet(entry_limit=2700.0, stop_loss=2690.0, take_profit=2720.0, atr_at_signal=5.0),
        confluence_score=55,
        confluence_breakdown=ConfluenceBreakdown(trend=15, momentum=12, structure=12, volume=8, macro=8),
        risk_pct=1.0,
        position_size=0.5,
        valid_until=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        bar_ts=datetime(2026, 7, 1, 6, 0, tzinfo=UTC),
        created_at=datetime(2026, 7, 1, 6, 0, 30, tzinfo=UTC),
    )


class _FakeStrategy:
    required_regime = Regime.TREND


class _FakeRepo:
    """count_since returns 5 — already 5 signals today (for the GR-12 flip)."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    async def is_enabled(self, _name: str) -> bool:
        return True

    async def recent_outcomes(self, *_a: Any, **_k: Any) -> list[float]:
        return []

    async def get_by_dedup(self, **_k: Any) -> None:
        return None

    async def count_since(self, **_k: Any) -> int:
        return 5

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


def _patch(monkeypatch: pytest.MonkeyPatch, candidate: SignalCandidate, *, profile_name: str) -> list[dict[str, Any]]:
    """Patch registry + a spy generate_candidate; strategy selects `profile_name`."""
    captured: list[dict[str, Any]] = []

    def _spy_generate(*_a: Any, **kwargs: Any) -> SignalCandidate:
        captured.append(kwargs)
        return candidate

    monkeypatch.setattr(scan_mod, "STRATEGY_REGISTRY", {"fake_scalper": _FakeStrategy})
    monkeypatch.setattr(
        scan_mod,
        "_load_strategy_config",
        lambda _n: StrategyConfig(raw={"gate_profile": profile_name}),
    )
    monkeypatch.setattr(scan_mod, "generate_candidate", _spy_generate)
    return captured


async def _run(cfg: AppConfig, candidate: SignalCandidate) -> Any:
    instrument = cfg.instrument("XAUUSD")
    repo = _FakeRepo()
    audit = _FakeAudit()
    return await scan_mod._run_strategies(
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
        bias="UP",
        enforce_bias=True,
    )


@pytest.mark.asyncio
async def test_scalping_profile_publishes_with_five_signals_today(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = _make_candidate()
    captured = _patch(monkeypatch, candidate, profile_name="scalping")
    cfg = _build_cfg()  # scalping max/day = 10 > 5 already today -> GR-12 passes
    result = await _run(cfg, candidate)
    assert result.status == "published"
    # Profile's confluence floor (50) is threaded into generate_candidate, not the global 60.
    assert captured and captured[0]["confluence_min_score"] == 50


@pytest.mark.asyncio
async def test_lowering_scalping_profile_threshold_flips_to_not_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _make_candidate()
    _patch(monkeypatch, candidate, profile_name="scalping")
    cfg = _build_cfg()
    # NO code change — only the config threshold drops below today's count (5).
    cfg.settings.signal.profiles["scalping"].max_signals_per_day_per_instrument = 3
    result = await _run(cfg, candidate)
    assert result.status != "published"


@pytest.mark.asyncio
async def test_default_profile_uses_global_confluence_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = _make_candidate()
    captured = _patch(monkeypatch, candidate, profile_name="default")
    cfg = _build_cfg()  # default max/day = 3 <= 5 today -> GR-12 fails -> not published
    result = await _run(cfg, candidate)
    assert result.status != "published"
    assert captured and captured[0]["confluence_min_score"] == 60
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_scan_gate_profile.py -q`
Expected: FAIL — `confluence_min_score` passed to `generate_candidate` is still the global `60` (not profile-driven), and `max_signals_per_day` is still the global `3`, so the scalping publish assertion fails.

- [ ] **Step 3: Add `_active_profile` + extend `_edge_quality_config`**

In `src/rtrade/pipeline/scan.py`, replace the existing `_edge_quality_config` (at `:1295`) with a version that accepts an override, and add `_active_profile` directly below it:

```python
def _edge_quality_config(cfg: AppConfig, *, min_score: int | None = None) -> EdgeQualityConfig:
    eq = cfg.settings.signal.edge_quality
    return EdgeQualityConfig(
        min_score=eq.min_score if min_score is None else min_score,
        max_spread_atr=eq.max_spread_atr,
        min_atr_percentile=eq.min_atr_percentile,
        max_atr_percentile=eq.max_atr_percentile,
        max_opposing_wick_ratio=eq.max_opposing_wick_ratio,
        max_total_wick_body_ratio=eq.max_total_wick_body_ratio,
        min_body_atr=eq.min_body_atr,
        min_volume_ratio=eq.min_volume_ratio,
        volume_window=eq.volume_window,
        max_range_expansion_atr=eq.max_range_expansion_atr,
        max_entry_distance_atr=eq.max_entry_distance_atr,
    )


def _active_profile(cfg: AppConfig, strategy_cfg: StrategyConfig) -> GateProfile:
    """Select the gate profile for a strategy via its `gate_profile` YAML key.

    Absent / unknown key → the `default` profile (= global values), so swing
    strategies (S1/S2) keep their current thresholds unchanged.
    """
    name = str(strategy_cfg.get("gate_profile", "default"))
    return cfg.settings.signal.profile(name)
```

Add `GateProfile` to the existing config import in `scan.py`. Find the `from rtrade.core.config import (...)` block and add `GateProfile` (keep alphabetical with the existing members, e.g. after `AppConfig`):

```python
from rtrade.core.config import (
    AppConfig,
    GateProfile,
    InstrumentConfig,
    # ... existing members unchanged ...
)
```

(If `core.config` is imported as individual lines rather than a group, add `from rtrade.core.config import GateProfile` next to the existing `AppConfig` import.)

- [ ] **Step 4: Thread the profile into `_run_strategies`**

In `src/rtrade/pipeline/scan.py::_run_strategies`, immediately AFTER the `edge_cfg = _edge_quality_config(cfg)` line (at `:842`), select the profile and rebuild `edge_cfg` with the profile's edge floor:

```python
        profile = _active_profile(cfg, strategy_cfg)
        edge_cfg = _edge_quality_config(cfg, min_score=profile.edge_quality_min_score)
```

(Delete the now-duplicate plain `edge_cfg = _edge_quality_config(cfg)` line so `edge_cfg` is assigned once.)

In the `generate_candidate(...)` call, change the confluence floor argument (currently `confluence_min_score=cfg.settings.signal.confluence_min_score,`) to:

```python
            confluence_min_score=profile.confluence_min_score,
```

In the FIRST (deterministic) `run_gate(...)` call, change `max_signals_per_day=cfg.settings.signal.max_signals_per_day_per_instrument,` to:

```python
            max_signals_per_day=profile.max_signals_per_day_per_instrument,
```

In the post-LLM branch, change the SECOND `run_gate(...)` call's `max_signals_per_day=cfg.settings.signal.max_signals_per_day_per_instrument,` to the same `profile.max_signals_per_day_per_instrument`, and every `confidence_min=cfg.settings.signal.confidence_min,` inside `_run_strategies` (the `run_llm_pipeline` call(s) and the post-LLM `run_gate`) to:

```python
                confidence_min=profile.confidence_min,
```

(These post-LLM edits are dormant while `llm.enabled = false` but keep the profile authoritative once LLM is enabled.)

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_scan_gate_profile.py tests/unit/test_scan_post_llm_gate.py -q`
Expected: PASS (3 new + 2 legacy post-LLM tests still green — the legacy tests use the `default` profile, whose values equal the globals, so behavior is unchanged).

- [ ] **Step 6: Gate + commit**

```
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src ; .venv\Scripts\python.exe -m pytest tests -q
```
Expected: ruff clean, mypy `Success`, full suite green.

```
cmd /c 'if exist nul del "\\?\%CD%\nul"'
git add src/rtrade/pipeline/scan.py tests/unit/test_scan_gate_profile.py
git commit -F COMMIT_MSG_TMP.txt
del COMMIT_MSG_TMP.txt
```
Message: `feat(sp4): per-strategy gate-profile selection wired into _run_strategies`

---

## Task 3: `S3MtfScalper` strategy + registry

**Files:**
- Create: `src/rtrade/strategies/s3_mtf_scalper.py`
- Modify: `src/rtrade/strategies/__init__.py`
- Test: `tests/unit/test_s3_mtf_scalper.py`

**Interfaces:**
- Consumes: `Strategy`/`EntryIntent`/`StrategyConfig` (base), `Action`/`Regime` (constants), `LevelSet` (schemas), pandas/numpy.
- Produces:
  - `S3MtfScalper(Strategy)` with `name = "s3_mtf_scalper"`, `required_regime = Regime.TREND`.
  - `populate_indicators` adds `s3_ema_fast` (EMA20), `s3_ema_mid` (EMA50), `s3_vwap` (rolling VWAP) and stows params in `df.attrs`.
  - `entry_signal` returns `EntryIntent(BUY|SELL)` for a value-pullback + momentum-turn + structure-held setup, else `None` (the scan layer drops H4-misaligned ones).
  - `custom_entry_price` → `LevelSet`: entry = EMA20 (limit retest), SL = entry ∓ k×ATR (k clamped to [0.5, 3.0]), TP at RR = `rr_target` (≥ 1.5).
- Registered in `STRATEGY_REGISTRY` as `"s3_mtf_scalper"`.
- Judgment call (documented in Self-Review): S3's structure confirm is "pullback held above EMA50" (price dipped to the EMA20 value line but stayed above the slower value line) — a deterministic higher-low proxy that needs no fragile swing-confirmation on the trigger bar. VWAP is computed and used as a value-side confluence (`close` must be on the trend side of VWAP). The richer volume/edge rejection is delegated to the pipeline's `edge_quality` stage (already wired in `generate_candidate`); S3's own volume filter is opt-in (`volume.min_ratio`, default `0.0` = off).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_s3_mtf_scalper.py
from __future__ import annotations

import pandas as pd
import pytest

from rtrade.core.constants import Action, Regime
from rtrade.signals.schemas import LevelSet
from rtrade.strategies.base import EntryIntent, StrategyConfig
from rtrade.strategies.s3_mtf_scalper import S3MtfScalper


def _rising_frame(n: int = 60) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    close = pd.Series([100.0 + 0.7 * i for i in range(n)])
    return pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]).to_numpy(),
            "high": (close + 0.6).to_numpy(),
            "low": (close - 0.6).to_numpy(),
            "close": close.to_numpy(),
            "volume": [1000.0] * n,
            "rsi": [55.0] * n,
            "atr": [1.2] * n,
        },
        index=idx,
    )


def _flat_frame(n: int = 60) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    close = pd.Series([100.0] * n)
    return pd.DataFrame(
        {
            "open": close.to_numpy(),
            "high": (close + 0.5).to_numpy(),
            "low": (close - 0.5).to_numpy(),
            "close": close.to_numpy(),
            "volume": [1000.0] * n,
            "rsi": [50.0] * n,
            "atr": [1.0] * n,
        },
        index=idx,
    )


def _long_setup() -> tuple[S3MtfScalper, pd.DataFrame]:
    strat = S3MtfScalper()
    df = strat.populate_indicators(_rising_frame(), StrategyConfig(raw={}))
    ema20 = float(df["s3_ema_fast"].iloc[-1])
    ema50 = float(df["s3_ema_mid"].iloc[-1])
    assert ema20 > ema50  # uptrend sanity before crafting the trigger bar
    mid = (ema20 + ema50) / 2.0  # between EMA50 and EMA20 -> touch value, hold structure
    df.iloc[-1, df.columns.get_loc("low")] = mid
    df.iloc[-1, df.columns.get_loc("close")] = ema20 + 3.0  # reclaim above EMA20 (+ above VWAP)
    df.iloc[-1, df.columns.get_loc("high")] = ema20 + 3.5
    df.iloc[-1, df.columns.get_loc("rsi")] = 52.0
    df.iloc[-2, df.columns.get_loc("rsi")] = 42.0  # dip then turning up
    return strat, df


def test_metadata() -> None:
    strat = S3MtfScalper()
    assert strat.name == "s3_mtf_scalper"
    assert strat.required_regime == Regime.TREND


def test_long_pullback_setup_emits_buy() -> None:
    strat, df = _long_setup()
    intent = strat.entry_signal(df)
    assert isinstance(intent, EntryIntent)
    assert intent.action == Action.BUY


def test_flat_market_emits_nothing() -> None:
    strat = S3MtfScalper()
    df = strat.populate_indicators(_flat_frame(), StrategyConfig(raw={}))
    assert strat.entry_signal(df) is None


def test_custom_entry_price_levels_long() -> None:
    strat, df = _long_setup()
    intent = strat.entry_signal(df)
    assert intent is not None
    levels = strat.custom_entry_price(df, intent)
    assert isinstance(levels, LevelSet)
    assert levels.stop_loss < levels.entry_limit < levels.take_profit
    rr = (levels.take_profit - levels.entry_limit) / (levels.entry_limit - levels.stop_loss)
    assert rr == pytest.approx(1.8, abs=1e-6)
    atr_mult = (levels.entry_limit - levels.stop_loss) / levels.atr_at_signal
    assert 0.5 <= atr_mult <= 3.0


def test_registered_in_registry() -> None:
    from rtrade.strategies import STRATEGY_REGISTRY

    assert STRATEGY_REGISTRY["s3_mtf_scalper"] is S3MtfScalper
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_s3_mtf_scalper.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rtrade.strategies.s3_mtf_scalper'`.

- [ ] **Step 3: Create `s3_mtf_scalper.py`**

```python
# src/rtrade/strategies/s3_mtf_scalper.py
"""S3 — MTF-Confluence Scalper (PLAN SP-4 §9.1).

Entry timeframe: M5/M15. Anchor: H4 (the scan layer enforces H4-bias alignment
via enforce_bias, so this strategy only emits entry-tf candidates and never
inspects H4 itself). Active only when regime = TREND.

Setup (LONG; mirror for SHORT):
1. Trend alignment on the entry tf: EMA20 > EMA50 and EMA20 rising.
2. Pullback to value: the trigger bar's low dips into the EMA20 value line
   (low <= EMA20) but holds structure (low > EMA50) — a deterministic
   higher-low proxy — and closes back above EMA20 (reclaim).
3. Value side of VWAP: close > VWAP (longs) / close < VWAP (shorts).
4. Momentum turn: prior-bar RSI in the dip (<= rsi_long_max) and last RSI
   turning up (rsi[-1] > rsi[-2]).
5. (Opt-in) volume filter: last volume >= volume_window mean * min_ratio.

Levels (LONG):
- entry_limit = EMA20 (limit waits for the retest into value).
- stop_loss   = entry - k*ATR, k = sl_atr_mult clamped to [0.5, 3.0] (GR-04).
- take_profit = entry + rr_target * (entry - stop_loss), rr_target >= rr_min.

The pipeline's edge_quality stage (in generate_candidate) provides the primary
adverse-selection / spread / volume rejection; S3's own volume filter is off by
default (min_ratio = 0.0).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from rtrade.core.constants import Action, Regime
from rtrade.signals.schemas import LevelSet
from rtrade.strategies.base import EntryIntent, Strategy, StrategyConfig


class S3MtfScalper(Strategy):
    """MTF EMA20/VWAP pullback scalper — S3."""

    @property
    def name(self) -> str:
        return "s3_mtf_scalper"

    @property
    def required_regime(self) -> Regime:
        return Regime.TREND

    def populate_indicators(self, df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
        """Add EMA20/EMA50/rolling-VWAP columns; stow params in df.attrs."""
        ema_fast = cfg.get_int("trend.ema_fast", 20)
        ema_mid = cfg.get_int("trend.ema_mid", 50)
        vwap_window = cfg.get_int("trend.vwap_window", 20)
        df.attrs["s3_min_bars"] = cfg.get_int("min_bars", 50)
        df.attrs["s3_ema_rising_lookback"] = cfg.get_int("trend.ema_rising_lookback", 5)
        df.attrs["s3_rsi_long_max"] = cfg.get_float("momentum.rsi_long_max", 45.0)
        df.attrs["s3_rsi_short_min"] = cfg.get_float("momentum.rsi_short_min", 55.0)
        df.attrs["s3_min_volume_ratio"] = cfg.get_float("volume.min_ratio", 0.0)
        df.attrs["s3_volume_window"] = cfg.get_int("volume.window", 20)
        df.attrs["s3_sl_atr_mult"] = cfg.get_float("levels.sl_atr_mult", 1.2)
        df.attrs["s3_rr_target"] = cfg.get_float("levels.rr_target", 1.8)

        df["s3_ema_fast"] = df["close"].astype(float).ewm(span=ema_fast, adjust=False).mean()
        df["s3_ema_mid"] = df["close"].astype(float).ewm(span=ema_mid, adjust=False).mean()

        typical = (df["high"].astype(float) + df["low"].astype(float) + df["close"].astype(float)) / 3.0
        if "volume" in df.columns:
            vol = df["volume"].astype(float).replace(0.0, np.nan)
            df["s3_vwap"] = (typical * vol).rolling(vwap_window).sum() / vol.rolling(
                vwap_window
            ).sum()
        else:
            df["s3_vwap"] = typical.rolling(vwap_window).mean()
        return df

    def entry_signal(self, df: pd.DataFrame) -> EntryIntent | None:
        """Evaluate the last closed bar for a value-pullback scalp."""
        min_bars = int(df.attrs.get("s3_min_bars", 50))
        if len(df) < min_bars or "s3_ema_fast" not in df.columns:
            return None
        for action, label in [(Action.BUY, "LONG"), (Action.SELL, "SHORT")]:
            if self._check_setup(df, action):
                return EntryIntent(action=action, reason=f"S3 MTF pullback {label} at value")
        return None

    def _check_setup(self, df: pd.DataFrame, action: Action) -> bool:
        rising_lb = int(df.attrs.get("s3_ema_rising_lookback", 5))
        if len(df) <= rising_lb:
            return False

        ema_fast_series = df["s3_ema_fast"].astype(float)
        ema_mid_series = df["s3_ema_mid"].astype(float)
        ema_fast = float(ema_fast_series.iloc[-1])
        ema_mid = float(ema_mid_series.iloc[-1])
        ema_fast_prev = float(ema_fast_series.iloc[-1 - rising_lb])
        vwap = float(df["s3_vwap"].iloc[-1])
        if any(np.isnan(v) for v in (ema_fast, ema_mid, ema_fast_prev, vwap)):
            return False

        last = df.iloc[-1]
        prev = df.iloc[-2]
        low = float(last["low"])
        high = float(last["high"])
        close = float(last["close"])
        rsi_last = float(last.get("rsi", 50.0))
        rsi_prev = float(prev.get("rsi", 50.0))
        rsi_long_max = float(df.attrs.get("s3_rsi_long_max", 45.0))
        rsi_short_min = float(df.attrs.get("s3_rsi_short_min", 55.0))

        if action == Action.BUY:
            trend_ok = ema_fast > ema_mid and ema_fast > ema_fast_prev
            pullback_ok = low <= ema_fast and low > ema_mid and close > ema_fast
            value_ok = close > vwap
            momentum_ok = rsi_prev <= rsi_long_max and rsi_last > rsi_prev
        else:  # SELL
            trend_ok = ema_fast < ema_mid and ema_fast < ema_fast_prev
            pullback_ok = high >= ema_fast and high < ema_mid and close < ema_fast
            value_ok = close < vwap
            momentum_ok = rsi_prev >= rsi_short_min and rsi_last < rsi_prev

        if not (trend_ok and pullback_ok and value_ok and momentum_ok):
            return False
        return self._volume_ok(df)

    def _volume_ok(self, df: pd.DataFrame) -> bool:
        """Opt-in volume filter (default off when min_ratio == 0.0)."""
        min_ratio = float(df.attrs.get("s3_min_volume_ratio", 0.0))
        if min_ratio <= 0.0 or "volume" not in df.columns:
            return True
        window = int(df.attrs.get("s3_volume_window", 20))
        vols = df["volume"].astype(float)
        if len(vols) < window:
            return True
        mean_vol = float(vols.iloc[-window:].mean())
        return float(vols.iloc[-1]) >= mean_vol * min_ratio

    def custom_entry_price(self, df: pd.DataFrame, intent: EntryIntent) -> LevelSet:
        """entry = EMA20; SL = entry ∓ k*ATR (k clamped [0.5,3.0]); TP at rr_target."""
        last = df.iloc[-1]
        entry = float(last["s3_ema_fast"])
        atr = float(last["atr"])
        if atr <= 0:
            raise ValueError("ATR must be positive for level computation")
        k = max(0.5, min(3.0, float(df.attrs.get("s3_sl_atr_mult", 1.2))))
        rr_target = float(df.attrs.get("s3_rr_target", 1.8))

        if intent.action == Action.BUY:
            stop_loss = entry - k * atr
            take_profit = entry + rr_target * (entry - stop_loss)
        else:  # SELL
            stop_loss = entry + k * atr
            take_profit = entry - rr_target * (stop_loss - entry)

        return LevelSet(
            entry_limit=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr_at_signal=atr,
        )

    def confirm_signal(self, df: pd.DataFrame, levels: LevelSet) -> bool:
        """Discard if RR fell below rr_target after rounding."""
        sl_dist = abs(levels.entry_limit - levels.stop_loss)
        if sl_dist <= 0:
            return False
        rr = abs(levels.take_profit - levels.entry_limit) / sl_dist
        return rr >= float(df.attrs.get("s3_rr_target", 1.8))
```

- [ ] **Step 4: Register S3 in `STRATEGY_REGISTRY`**

In `src/rtrade/strategies/__init__.py`, add the import + registry/`__all__` entries:

```python
# Strategy engine — Freqtrade-pattern callbacks (ADR-02).

from rtrade.strategies.base import EntryIntent, Strategy, StrategyConfig
from rtrade.strategies.s1_trend_pullback import S1TrendPullback
from rtrade.strategies.s2_range_mr import S2RangeMR
from rtrade.strategies.s3_mtf_scalper import S3MtfScalper

__all__ = [
    "EntryIntent",
    "S1TrendPullback",
    "S2RangeMR",
    "S3MtfScalper",
    "Strategy",
    "StrategyConfig",
]

# Registry of all available strategies.
STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "s1_trend_pullback": S1TrendPullback,
    "s2_range_mr": S2RangeMR,
    "s3_mtf_scalper": S3MtfScalper,
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_s3_mtf_scalper.py -q`
Expected: PASS (5 passed).

- [ ] **Step 6: Gate + commit**

```
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src ; .venv\Scripts\python.exe -m pytest tests -q
```
Expected: ruff clean, mypy `Success`, full suite green.

```
cmd /c 'if exist nul del "\\?\%CD%\nul"'
git add src/rtrade/strategies/s3_mtf_scalper.py src/rtrade/strategies/__init__.py tests/unit/test_s3_mtf_scalper.py
git commit -F COMMIT_MSG_TMP.txt
del COMMIT_MSG_TMP.txt
```
Message: `feat(sp4): S3MtfScalper (EMA20/VWAP pullback + momentum + structure) + registry`

---

## Task 4: `S4SmcScalper` strategy + registry

**Files:**
- Create: `src/rtrade/strategies/s4_smc_scalper.py`
- Modify: `src/rtrade/strategies/__init__.py`
- Test: `tests/unit/test_s4_smc_scalper.py`

**Interfaces:**
- Consumes: `Strategy`/`EntryIntent`/`StrategyConfig` (base), `Action`/`Regime` (constants), `LevelSet` (schemas), and the SP-3 pinned `rtrade.indicators.smc` functions `fair_value_gaps`, `order_blocks`, `liquidity_sweeps`, `market_structure` + their dataclasses.
- Produces:
  - `S4SmcScalper(Strategy)` with `name = "s4_smc_scalper"`, `required_regime = Regime.TREND`.
  - `populate_indicators` stows SMC params in `df.attrs` (no extra columns — detectors run on raw OHLC).
  - `entry_signal`: BUY when a low-side `LiquiditySweep` precedes (within `sweep_window`) a bullish `StructureEvent` (BOS/CHoCH) and a bullish `OrderBlock` (or bullish `FairValueGap`) exists at/before the break; SELL is the bearish mirror. Stashes the chosen zone + sweep level in `df.attrs` for `custom_entry_price`.
  - `custom_entry_price` → `LevelSet`: entry limit at the order-block / FVG edge (`top` for longs, `bottom` for shorts), SL beyond the swept level (± `sl_buffer_atr`×ATR, distance clamped to [0.5, 3.0]×ATR), TP at RR = `rr_target` (≥ 1.5) toward the next liquidity.
- Registered in `STRATEGY_REGISTRY` as `"s4_smc_scalper"`.
- Judgment call (documented in Self-Review): TP is set at `rr_target` rather than literally at the next liquidity pool's price, guaranteeing RR ≥ floor deterministically (the next-pool target is what `rr_target` represents). The order block (always produced for a structure break that has a preceding opposing candle) is the primary entry zone; the most-recent bullish FVG is the fallback.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_s4_smc_scalper.py
from __future__ import annotations

import pandas as pd
import pytest

from rtrade.core.constants import Action, Regime
from rtrade.indicators.smc import liquidity_sweeps, market_structure, order_blocks
from rtrade.signals.schemas import LevelSet
from rtrade.strategies.base import EntryIntent, StrategyConfig
from rtrade.strategies.s4_smc_scalper import S4SmcScalper

OHLC = tuple[float, float, float, float]

# 15 bars (swing_lookback=2): low-side sweep of 100 @9, bullish BOS @13,
# bullish order block @12 (down candle before the break). Hand-traced against
# the SP-3 detector definitions.
_BULL_ROWS: list[OHLC] = [
    (100.0, 104.0, 99.0, 103.0),
    (103.0, 106.0, 102.0, 105.0),
    (105.0, 108.0, 104.0, 107.0),
    (107.0, 110.0, 106.0, 108.0),  # swing high 110 @3
    (108.0, 109.0, 105.0, 106.0),
    (106.0, 108.0, 103.0, 104.0),
    (104.0, 106.0, 100.0, 101.0),  # swing low 100 @6
    (101.0, 105.0, 102.0, 103.0),
    (103.0, 106.0, 104.0, 105.0),
    (105.0, 107.0, 98.0, 102.0),   # sweep: low 98 < 100, close 102 > 100 @9
    (102.0, 106.0, 101.0, 104.0),
    (104.0, 107.0, 103.0, 106.0),
    (106.0, 108.0, 105.0, 105.0),  # down candle -> order block @12
    (105.0, 112.0, 104.0, 111.0),  # close 111 > 110 -> bullish BOS @13
    (111.0, 113.0, 110.0, 112.0),
]


def _df(rows: list[OHLC], *, atr: float = 3.0) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame(
        {
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [1000.0] * len(rows),
            "atr": [atr] * len(rows),
        },
        index=idx,
    )


def _cfg() -> StrategyConfig:
    return StrategyConfig(raw={"smc": {"swing_lookback": 2}, "min_bars": 10})


def test_metadata() -> None:
    strat = S4SmcScalper()
    assert strat.name == "s4_smc_scalper"
    assert strat.required_regime == Regime.TREND


def test_detector_fixture_sanity() -> None:
    # Guard the hand-traced fixture against detector drift.
    df = _df(_BULL_ROWS)
    sweeps = liquidity_sweeps(df, swing_lookback=2)
    events = market_structure(df, swing_lookback=2)
    blocks = order_blocks(df, swing_lookback=2)
    assert any(s.side == "low" and s.idx == 9 for s in sweeps)
    assert any(e.direction == "bullish" and e.idx == 13 for e in events)
    assert any(b.direction == "bullish" and b.idx == 12 for b in blocks)


def test_bull_setup_emits_buy() -> None:
    strat = S4SmcScalper()
    df = strat.populate_indicators(_df(_BULL_ROWS), _cfg())
    intent = strat.entry_signal(df)
    assert isinstance(intent, EntryIntent)
    assert intent.action == Action.BUY


def test_flat_market_emits_nothing() -> None:
    strat = S4SmcScalper()
    flat: list[OHLC] = [(100.0, 101.0, 99.0, 100.0)] * 15
    df = strat.populate_indicators(_df(flat), _cfg())
    assert strat.entry_signal(df) is None


def test_custom_entry_price_levels_long() -> None:
    strat = S4SmcScalper()
    df = strat.populate_indicators(_df(_BULL_ROWS), _cfg())
    intent = strat.entry_signal(df)
    assert intent is not None
    levels = strat.custom_entry_price(df, intent)
    assert isinstance(levels, LevelSet)
    # entry at OB top (108), SL beyond swept level (100) - 0.25*ATR(3) = 99.25.
    assert levels.entry_limit == pytest.approx(108.0)
    assert levels.stop_loss == pytest.approx(99.25)
    assert levels.stop_loss < levels.entry_limit < levels.take_profit
    rr = (levels.take_profit - levels.entry_limit) / (levels.entry_limit - levels.stop_loss)
    assert rr == pytest.approx(1.8, abs=1e-6)
    atr_mult = (levels.entry_limit - levels.stop_loss) / levels.atr_at_signal
    assert 0.5 <= atr_mult <= 3.0


def test_registered_in_registry() -> None:
    from rtrade.strategies import STRATEGY_REGISTRY

    assert STRATEGY_REGISTRY["s4_smc_scalper"] is S4SmcScalper
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_s4_smc_scalper.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rtrade.strategies.s4_smc_scalper'`.

- [ ] **Step 3: Create `s4_smc_scalper.py`**

```python
# src/rtrade/strategies/s4_smc_scalper.py
"""S4 — SMC/ICT Scalper (PLAN SP-4 §9.2).

Entry timeframe: M5/M15. Anchor: H4 (the scan layer enforces H4-bias alignment
via enforce_bias). Active only when regime = TREND.

Setup (LONG; mirror for SHORT):
1. A low-side liquidity sweep (stop-run below a prior swing low, close back
   inside) occurs within `sweep_window` bars before...
2. ...a bullish structure break (BOS or CHoCH) in the bias direction.
3. A bullish order block (last opposing candle before the break) or, as a
   fallback, a bullish fair value gap, exists at/before the break — that zone
   is where the limit waits for the retest.

Levels (LONG):
- entry_limit = order-block / FVG top (retest into the institutional zone).
- stop_loss   = swept level - sl_buffer_atr*ATR (beyond the liquidity grab);
                distance clamped to [0.5, 3.0]*ATR (GR-04).
- take_profit = entry + rr_target * risk (toward the next liquidity pool),
                rr_target >= rr_min.

Uses only the SP-3 pinned detectors in rtrade.indicators.smc.
"""

from __future__ import annotations

import pandas as pd

from rtrade.core.constants import Action, Regime
from rtrade.indicators.smc import (
    fair_value_gaps,
    liquidity_sweeps,
    market_structure,
    order_blocks,
)
from rtrade.signals.schemas import LevelSet
from rtrade.strategies.base import EntryIntent, Strategy, StrategyConfig


class S4SmcScalper(Strategy):
    """Liquidity-sweep + BOS/CHoCH into order-block/FVG scalper — S4."""

    @property
    def name(self) -> str:
        return "s4_smc_scalper"

    @property
    def required_regime(self) -> Regime:
        return Regime.TREND

    def populate_indicators(self, df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
        """Stow SMC params in df.attrs (detectors run on raw OHLC at entry time)."""
        df.attrs["s4_swing_lookback"] = cfg.get_int("smc.swing_lookback", 3)
        df.attrs["s4_sweep_window"] = cfg.get_int("smc.sweep_window", 8)
        df.attrs["s4_min_bars"] = cfg.get_int("min_bars", 30)
        df.attrs["s4_sl_buffer_atr"] = cfg.get_float("levels.sl_buffer_atr", 0.25)
        df.attrs["s4_rr_target"] = cfg.get_float("levels.rr_target", 1.8)
        return df

    def entry_signal(self, df: pd.DataFrame) -> EntryIntent | None:
        """Detect a sweep -> BOS/CHoCH -> order-block/FVG setup on the last bar."""
        if len(df) < int(df.attrs.get("s4_min_bars", 30)):
            return None
        sl_lb = int(df.attrs.get("s4_swing_lookback", 3))
        sweep_window = int(df.attrs.get("s4_sweep_window", 8))

        events = market_structure(df, swing_lookback=sl_lb)
        if not events:
            return None
        sweeps = liquidity_sweeps(df, swing_lookback=sl_lb)
        obs = order_blocks(df, swing_lookback=sl_lb)
        fvgs = fair_value_gaps(df)

        for action, direction, sweep_side, label in [
            (Action.BUY, "bullish", "low", "LONG"),
            (Action.SELL, "bearish", "high", "SHORT"),
        ]:
            dir_events = [e for e in events if e.direction == direction]
            if not dir_events:
                continue
            event = max(dir_events, key=lambda e: e.idx)

            qual_sweeps = [
                s
                for s in sweeps
                if s.side == sweep_side and event.idx - sweep_window <= s.idx <= event.idx
            ]
            if not qual_sweeps:
                continue
            sweep = max(qual_sweeps, key=lambda s: s.idx)

            zone = self._entry_zone(obs, fvgs, direction, event.idx)
            if zone is None:
                continue
            top, bottom = zone

            df.attrs["s4_entry_top"] = top
            df.attrs["s4_entry_bottom"] = bottom
            df.attrs["s4_sweep_level"] = sweep.level
            return EntryIntent(
                action=action,
                reason=f"S4 SMC {label}: sweep@{sweep.idx} -> {event.kind}@{event.idx}",
            )
        return None

    @staticmethod
    def _entry_zone(
        obs: list,  # list[OrderBlock]
        fvgs: list,  # list[FairValueGap]
        direction: str,
        event_idx: int,
    ) -> tuple[float, float] | None:
        """Most-recent directional order block at/before the break, else a FVG."""
        dir_obs = [o for o in obs if o.direction == direction and o.idx <= event_idx]
        if dir_obs:
            ob = max(dir_obs, key=lambda o: o.idx)
            return (float(ob.top), float(ob.bottom))
        dir_fvgs = [f for f in fvgs if f.direction == direction and f.end_idx <= event_idx]
        if dir_fvgs:
            fvg = max(dir_fvgs, key=lambda f: f.end_idx)
            return (float(fvg.top), float(fvg.bottom))
        return None

    def custom_entry_price(self, df: pd.DataFrame, intent: EntryIntent) -> LevelSet:
        """entry at the OB/FVG edge; SL beyond the sweep; TP at rr_target."""
        last = df.iloc[-1]
        atr = float(last["atr"])
        if atr <= 0:
            raise ValueError("ATR must be positive for level computation")
        entry_top = float(df.attrs["s4_entry_top"])
        entry_bottom = float(df.attrs["s4_entry_bottom"])
        sweep_level = float(df.attrs["s4_sweep_level"])
        buffer_atr = float(df.attrs.get("s4_sl_buffer_atr", 0.25))
        rr_target = float(df.attrs.get("s4_rr_target", 1.8))

        if intent.action == Action.BUY:
            entry = entry_top
            raw_sl = sweep_level - buffer_atr * atr
            sl_dist = max(0.5 * atr, min(3.0 * atr, entry - raw_sl))
            stop_loss = entry - sl_dist
            take_profit = entry + rr_target * sl_dist
        else:  # SELL
            entry = entry_bottom
            raw_sl = sweep_level + buffer_atr * atr
            sl_dist = max(0.5 * atr, min(3.0 * atr, raw_sl - entry))
            stop_loss = entry + sl_dist
            take_profit = entry - rr_target * sl_dist

        return LevelSet(
            entry_limit=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr_at_signal=atr,
        )

    def confirm_signal(self, df: pd.DataFrame, levels: LevelSet) -> bool:
        """Discard if RR fell below rr_target after rounding."""
        sl_dist = abs(levels.entry_limit - levels.stop_loss)
        if sl_dist <= 0:
            return False
        rr = abs(levels.take_profit - levels.entry_limit) / sl_dist
        return rr >= float(df.attrs.get("s4_rr_target", 1.8))
```

Typing note (mypy `--strict`): annotate `_entry_zone` with the real SP-3 types instead of bare `list` — import them and use them:

```python
from rtrade.indicators.smc import (
    FairValueGap,
    OrderBlock,
    fair_value_gaps,
    liquidity_sweeps,
    market_structure,
    order_blocks,
)
```

and change the signature to `obs: list[OrderBlock], fvgs: list[FairValueGap]`. Replace the placeholder `list` annotations above with these (the `# list[...]` comments mark exactly where).

- [ ] **Step 4: Register S4 in `STRATEGY_REGISTRY`**

In `src/rtrade/strategies/__init__.py`, add the S4 import + registry/`__all__` entries (keeping the S3 entries from Task 3):

```python
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

STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "s1_trend_pullback": S1TrendPullback,
    "s2_range_mr": S2RangeMR,
    "s3_mtf_scalper": S3MtfScalper,
    "s4_smc_scalper": S4SmcScalper,
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_s4_smc_scalper.py -q`
Expected: PASS (6 passed). The `test_detector_fixture_sanity` guard confirms the hand-traced frame still matches the SP-3 detectors (catches drift early).

- [ ] **Step 6: Gate + commit**

```
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src ; .venv\Scripts\python.exe -m pytest tests -q
```
Expected: ruff clean, mypy `Success`, full suite green.

```
cmd /c 'if exist nul del "\\?\%CD%\nul"'
git add src/rtrade/strategies/s4_smc_scalper.py src/rtrade/strategies/__init__.py tests/unit/test_s4_smc_scalper.py
git commit -F COMMIT_MSG_TMP.txt
del COMMIT_MSG_TMP.txt
```
Message: `feat(sp4): S4SmcScalper (sweep + BOS/CHoCH into OB/FVG) + registry`

---

## Task 5: Confine S3/S4 to XAUUSD entry timeframes (`_strategy_applies`) + strategy configs

**Files:**
- Modify: `src/rtrade/pipeline/scan.py` (add `_strategy_applies`; wire into `_run_strategies`)
- Create: `config/strategies/s3_mtf_scalper.yaml`
- Create: `config/strategies/s4_smc_scalper.yaml`
- Test: `tests/unit/test_scan_strategy_applies.py`

**Interfaces:**
- Consumes: `StrategyConfig.get`, `InstrumentConfig`, `Timeframe`, the `entry_tf` param already on `_run_strategies` (SP-2).
- Produces: `_strategy_applies(strategy_cfg: StrategyConfig, instrument: InstrumentConfig, entry_tf: Timeframe) -> bool` — `True` unless the strategy YAML declares an `instruments:` allowlist excluding the symbol, or an `entry_timeframes:` allowlist excluding `entry_tf.value`. Absent allowlists → always applies (S1/S2 keep running everywhere → back-compat).
- Wiring: `_run_strategies` skips a strategy when `_strategy_applies` is `False`. Because S3/S4 ship with `instruments: ["XAUUSD"]` and `entry_timeframes: ["5m","15m"]`, they only run on XAUUSD M5/M15; XAUUSD's `entry_timeframes` come from SP-2's `instruments.yaml`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_scan_strategy_applies.py
from __future__ import annotations

from rtrade.core.config import InstrumentConfig
from rtrade.core.constants import Market, Timeframe
import rtrade.pipeline.scan as scan_mod
from rtrade.strategies import StrategyConfig


def _inst(symbol: str = "XAUUSD") -> InstrumentConfig:
    return InstrumentConfig(
        symbol=symbol,
        market=Market.METALS if symbol == "XAUUSD" else Market.FOREX,
        provider="oanda",
        provider_symbol="XAU_USD",
        timeframes=[Timeframe.M5, Timeframe.M15, Timeframe.H4],
        context_timeframe=Timeframe.D1,
        pip_size=0.01,
        quote_currency="USD",
    )


_SCALP = StrategyConfig(raw={"instruments": ["XAUUSD"], "entry_timeframes": ["5m", "15m"]})


def test_scalper_applies_on_xauusd_entry_tf() -> None:
    assert scan_mod._strategy_applies(_SCALP, _inst("XAUUSD"), Timeframe.M5) is True
    assert scan_mod._strategy_applies(_SCALP, _inst("XAUUSD"), Timeframe.M15) is True


def test_scalper_skipped_on_other_symbol() -> None:
    assert scan_mod._strategy_applies(_SCALP, _inst("EURUSD"), Timeframe.M5) is False


def test_scalper_skipped_on_non_entry_tf() -> None:
    assert scan_mod._strategy_applies(_SCALP, _inst("XAUUSD"), Timeframe.H1) is False


def test_no_allowlist_always_applies() -> None:
    swing = StrategyConfig(raw={})
    assert scan_mod._strategy_applies(swing, _inst("EURUSD"), Timeframe.H1) is True
    assert scan_mod._strategy_applies(swing, _inst("XAUUSD"), Timeframe.M5) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_scan_strategy_applies.py -q`
Expected: FAIL — `AttributeError: module 'rtrade.pipeline.scan' has no attribute '_strategy_applies'`.

- [ ] **Step 3: Add `_strategy_applies` + wire it into `_run_strategies`**

In `src/rtrade/pipeline/scan.py`, add the predicate next to `_active_profile` (from Task 2):

```python
def _strategy_applies(
    strategy_cfg: StrategyConfig,
    instrument: InstrumentConfig,
    entry_tf: Timeframe,
) -> bool:
    """True unless the strategy YAML restricts it away from this symbol/timeframe.

    `instruments: [...]` and `entry_timeframes: [...]` are optional allowlists.
    Absent (or empty) → the strategy applies everywhere (S1/S2 back-compat).
    """
    symbols = strategy_cfg.get("instruments")
    if isinstance(symbols, list) and symbols and instrument.symbol not in symbols:
        return False
    tfs = strategy_cfg.get("entry_timeframes")
    if isinstance(tfs, list) and tfs and entry_tf.value not in tfs:
        return False
    return True
```

In `_run_strategies`, directly AFTER `strategy_cfg = _load_strategy_config(strategy_name)` (at `:839`), add the applicability skip (before `profile = _active_profile(...)` from Task 2):

```python
        if not _strategy_applies(strategy_cfg, instrument, entry_tf):
            logger.info(
                "strategy not applicable for instrument/timeframe, skipping",
                strategy=strategy_name,
                symbol=instrument.symbol,
                entry_tf=entry_tf.value,
            )
            continue
```

- [ ] **Step 4: Create `config/strategies/s3_mtf_scalper.yaml`**

```yaml
# ============================================================================
# Parameter Strategi S3 — MTF-Confluence Scalper (PLAN SP-4 §9.1).
# Hanya jalan di XAUUSD M5/M15 untuk saat ini. Tuning HANYA via walk-forward
# harness (SP-6) — dilarang tuning manual ke test set.
# ============================================================================

strategy: s3_mtf_scalper
gate_profile: scalping            # SP-4: longgarkan threshold lunak (bukan floor)
instruments: ["XAUUSD"]           # allowlist — S3 hanya untuk XAUUSD
entry_timeframes: ["5m", "15m"]   # allowlist — hanya entry tf scalping

min_bars: 50

trend:
  ema_fast: 20
  ema_mid: 50
  vwap_window: 20
  ema_rising_lookback: 5

momentum:
  rsi_long_max: 45                # dip sehat untuk LONG (RSI exiting low)
  rsi_short_min: 55               # mirror untuk SHORT

volume:
  min_ratio: 0.0                  # filter volume S3 OFF (edge_quality yang utama)
  window: 20

levels:
  sl_atr_mult: 1.2                # k×ATR, di-clamp ke [0.5, 3.0] (GR-04)
  rr_target: 1.8                  # >= rr_min 1.5 (GR-03)
  valid_bars: 12                  # limit tak tersentuh dalam 12 bar → expired

confluence_weights:               # total 100 — §8.6
  trend: 25
  momentum: 20
  structure: 20
  volume: 15
  macro: 20
```

- [ ] **Step 5: Create `config/strategies/s4_smc_scalper.yaml`**

```yaml
# ============================================================================
# Parameter Strategi S4 — SMC/ICT Scalper (PLAN SP-4 §9.2).
# Hanya jalan di XAUUSD M5/M15 untuk saat ini. Tuning HANYA via walk-forward
# harness (SP-6) — dilarang tuning manual ke test set.
# ============================================================================

strategy: s4_smc_scalper
gate_profile: scalping            # SP-4: longgarkan threshold lunak (bukan floor)
instruments: ["XAUUSD"]           # allowlist — S4 hanya untuk XAUUSD
entry_timeframes: ["5m", "15m"]   # allowlist — hanya entry tf scalping

min_bars: 30

smc:
  swing_lookback: 3               # fractal half-window untuk swing/BOS/sweep
  sweep_window: 8                 # max bar sweep boleh mendahului structure break

levels:
  sl_buffer_atr: 0.25             # SL di luar level sweep (× ATR)
  rr_target: 1.8                  # >= rr_min 1.5 (GR-03)
  valid_bars: 12

confluence_weights:               # total 100 — §8.6
  trend: 25
  momentum: 20
  structure: 20
  volume: 15
  macro: 20
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_scan_strategy_applies.py -q`
Expected: PASS (4 passed).

- [ ] **Step 7: Full gate + commit**

```
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src ; .venv\Scripts\python.exe -m pytest tests -q
```
Expected: ruff clean, mypy `Success`, full suite green (S3/S4 now only run on XAUUSD M5/M15; S1/S2 unaffected).

```
cmd /c 'if exist nul del "\\?\%CD%\nul"'
git add src/rtrade/pipeline/scan.py config/strategies/s3_mtf_scalper.yaml config/strategies/s4_smc_scalper.yaml tests/unit/test_scan_strategy_applies.py
git commit -F COMMIT_MSG_TMP.txt
del COMMIT_MSG_TMP.txt
```
Message: `feat(sp4): confine S3/S4 to XAUUSD M5/M15 via _strategy_applies + strategy configs`

---

## Self-Review (completed by plan author)

**1. Spec coverage (SP-4 section §9 of design):**
- S3 MTF-Confluence scalper: pullback to EMA20/VWAP + momentum (RSI exiting extreme) + structure confirm + volume/edge filter; SL=k×ATR (k∈[0.5,3.0]); TP at RR≥rr_target; `required_regime=TREND`; registered (§9.1) → Task 3. ✅
- S4 SMC/ICT scalper: liquidity_sweep + BOS/CHoCH → entry limit at OB/FVG, SL beyond sweep, TP toward next liquidity (RR≥rr_target); `required_regime=TREND`; registered; uses `indicators.smc` (§9.2) → Task 4. ✅
- Scalping gate profile: `signal.profiles` map with `default` (current values) + `scalping` (more permissive confluence_min_score/edge_quality.min_score/confidence_min/max_signals_per_day_per_instrument); hard floors (rr_min, sl_atr, risk, news, llm.enabled) NOT in profile, stay globally validated; per-strategy selection wired into `_run_strategies` (§9.3) → Tasks 1 + 2. ✅
- TDD test: changing a profile threshold flips a candidate published↔not-published with NO code change, mirroring P1-6 testability (§9.3) → Task 2 `test_lowering_scalping_profile_threshold_flips_to_not_published`. ✅
- S3/S4 wired into `config/strategies/*.yaml`, only run on XAUUSD entry timeframes for now (§9.4) → Task 5 (configs + `_strategy_applies`). ✅
- S3/S4 entry-rule tests: valid setup → candidate; flat/no setup → none; RR<1.5 / SL outside ATR band guarded by `LevelSet`/`SignalCandidate` validators + `confirm_signal` (§9.4) → Tasks 3, 4. ✅
- Bias-misaligned candidates dropped by the scan layer (not re-checked in-strategy) — consumes SP-2's `enforce_bias` (§9.1/9.2 + SP-2 pinned interface). Documented in Upstream interfaces + Task headers. ✅

**2. Placeholder scan:** No TBD/TODO. Every code step shows complete, typed code and exact commands with expected output. The only `# list[...]` markers in Task 4 are immediately followed by the explicit typed-signature replacement instruction (mypy `--strict` clean).

**3. Type / interface consistency:**
- `GateProfile{confluence_min_score, edge_quality_min_score, confidence_min, max_signals_per_day_per_instrument}` defined in Task 1, consumed identically in Task 2 (`profile.confluence_min_score`, `.edge_quality_min_score`, `.confidence_min`, `.max_signals_per_day_per_instrument`).
- `SignalSettings.profile(name)` / `.profiles` consistent across Tasks 1, 2, and tests.
- `_active_profile(cfg, strategy_cfg)`, `_edge_quality_config(cfg, *, min_score=None)`, `_strategy_applies(strategy_cfg, instrument, entry_tf)` signatures consistent between scan.py edits (Tasks 2, 5) and their tests.
- Strategy class names / registry keys consistent everywhere: `S3MtfScalper`→`"s3_mtf_scalper"`, `S4SmcScalper`→`"s4_smc_scalper"` (Tasks 3, 4 + `__init__.py` + configs + `_strategy_applies` allowlists).
- `df.attrs` keys namespaced per strategy (`s3_*`, `s4_*`) and read with the same names they are written with.
- SP-3 detector names/dataclass fields (`liquidity_sweeps`/`market_structure`/`order_blocks`/`fair_value_gaps`; `LiquiditySweep.side/level/idx`, `StructureEvent.direction/idx`, `OrderBlock.top/bottom/idx/direction`, `FairValueGap.top/bottom/end_idx/direction`) consumed exactly as pinned.
- `generate_candidate` arg `confluence_min_score` and `edge_quality_config`/`edge_quality_enabled` consumed as defined in `signals/engine.py`; `LevelSet`/`SignalCandidate` invariants (GR-02/03/04) enforced by the schemas, asserted in Task 3/4 level tests.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-20-sp4-scalping-strategies.md`.
This is **SP-4 of 6** (depends on SP-1 data layer, SP-2 MTF engine, SP-3 SMC indicators; SP-5 filters are optional reuse). Validation of each strategy×timeframe is **SP-6** — S3/S4 ship signal-only and are gated to XAUUSD M5/M15 until they pass the backtest gate.

Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration. **REQUIRED SUB-SKILL:** superpowers:subagent-driven-development.

**2. Inline Execution** — execute tasks in this session with checkpoints. **REQUIRED SUB-SKILL:** superpowers:executing-plans.

Recommended: subagent-driven-development, one task at a time, full gate green before advancing.
