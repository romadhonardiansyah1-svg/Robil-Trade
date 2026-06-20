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
        levels=LevelSet(
            entry_limit=2700.0, stop_loss=2690.0, take_profit=2720.0, atr_at_signal=5.0
        ),
        confluence_score=70,
        confluence_breakdown=ConfluenceBreakdown(
            trend=20, momentum=15, structure=15, volume=10, macro=10
        ),
        risk_pct=1.0,
        position_size=0.5,
        valid_until=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        bar_ts=datetime(2026, 7, 1, 6, 25, tzinfo=UTC),
        created_at=datetime(2026, 7, 1, 6, 25, 30, tzinfo=UTC),
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

    async def add(
        self, *, stage: str, ok: bool, signal_id: str | None = None, detail: Any = None
    ) -> None:
        self.entries.append({"stage": stage, "ok": ok, "detail": detail})


def _build_cfg() -> AppConfig:
    cfg = AppConfig.load(config_dir=_CONFIG_DIR, env_file=None)
    cfg.settings.llm.enabled = False
    cfg.settings.signal.edge_quality.enabled = False
    return cfg


async def _run(
    cfg: AppConfig, candidate: SignalCandidate, *, bias: str, enforce_bias: bool
) -> tuple[Any, _FakeAudit]:
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
        e["ok"] is False
        and isinstance(e["detail"], dict)
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
