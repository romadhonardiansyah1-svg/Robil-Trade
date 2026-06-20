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
        levels=LevelSet(
            entry_limit=2700.0, stop_loss=2690.0, take_profit=2720.0, atr_at_signal=5.0
        ),
        confluence_score=55,
        confluence_breakdown=ConfluenceBreakdown(
            trend=15, momentum=12, structure=12, volume=8, macro=8
        ),
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

    async def add(
        self, *, stage: str, ok: bool, signal_id: str | None = None, detail: Any = None
    ) -> None:
        self.entries.append({"stage": stage, "ok": ok, "detail": detail})


def _build_cfg() -> AppConfig:
    cfg = AppConfig.load(config_dir=_CONFIG_DIR, env_file=None)
    cfg.settings.llm.enabled = False
    cfg.settings.signal.edge_quality.enabled = False
    return cfg


def _patch(
    monkeypatch: pytest.MonkeyPatch, candidate: SignalCandidate, *, profile_name: str
) -> list[dict[str, Any]]:
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
        now=datetime(2026, 7, 1, 6, 8, tzinfo=UTC),
        calendar_stale=False,
        entry_tf=Timeframe.M5,
        bias="UP",
        enforce_bias=True,
    )


@pytest.mark.asyncio
async def test_scalping_profile_publishes_with_five_signals_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
async def test_default_profile_uses_global_confluence_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _make_candidate()
    captured = _patch(monkeypatch, candidate, profile_name="default")
    cfg = _build_cfg()  # default max/day = 3 <= 5 today -> GR-12 fails -> not published
    result = await _run(cfg, candidate)
    assert result.status != "published"
    assert captured and captured[0]["confluence_min_score"] == 60
