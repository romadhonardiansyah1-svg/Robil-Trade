"""QA-GATE-02: post-LLM guardrail gate wiring (audit item C2 / plan P1-3).

The deterministic first ``run_gate`` call in ``_run_strategies`` omits
``confidence``/``original_candidate``/``sources``/``pack_source_ids``, so GR-09
(confidence floor), GR-10 (LLM number-mutation) and GR-11 (citations) are never
exercised at runtime. After the LLM pipeline returns a PUBLISH decision a SECOND
"post_llm" gate must run with those P2 args populated.

These tests spy on ``rtrade.pipeline.scan.run_gate`` and drive ``_run_strategies``
directly with monkeypatched candidate generation + LLM pipeline. No live network,
fully deterministic.
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
from rtrade.llm.context_pack import ContextPack
from rtrade.llm.pipeline import PipelineDecision, PipelineResult
import rtrade.pipeline.scan as scan_mod
from rtrade.signals.schemas import ConfluenceBreakdown, LevelSet, SignalCandidate
from rtrade.strategies import StrategyConfig

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def _make_candidate() -> SignalCandidate:
    return SignalCandidate(
        candidate_id="postllm_001",
        symbol="XAUUSD",
        timeframe=Timeframe.H1,
        strategy="fake_strat",
        action=Action.BUY,
        levels=LevelSet(
            entry_limit=2700.0,
            stop_loss=2690.0,
            take_profit=2720.0,
            atr_at_signal=5.0,
        ),
        confluence_score=70,
        confluence_breakdown=ConfluenceBreakdown(
            trend=20, momentum=15, structure=15, volume=10, macro=10
        ),
        risk_pct=1.0,
        position_size=0.5,
        valid_until=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        bar_ts=datetime(2026, 7, 1, 6, 0, tzinfo=UTC),
        created_at=datetime(2026, 7, 1, 6, 0, 30, tzinfo=UTC),
    )


def _make_pack() -> ContextPack:
    source_ids = [
        "ind:rsi:XAUUSD:1h:2026-07-01T06:00:00",
        "ind:atr:XAUUSD:1h:2026-07-01T06:00:00",
    ]
    return ContextPack(
        pack_id="pack_test",
        generated_at="2026-07-01T06:01:00",
        instrument={"symbol": "XAUUSD", "market": "forex", "session_active": True},
        candidate={},
        indicators={},
        structure={"swing_highs": [], "swing_lows": [], "sr_levels": [], "gap_zones": []},
        regime={"state": "TREND", "since": "2026-06-25", "source_id": source_ids[0]},
        calendar_next_72h=[],
        derivatives=None,
        similar_setups=None,
        recent_summary={},
        source_ids=source_ids,
    )


class _FakeStrategy:
    """Minimal strategy stub: only ``required_regime`` is read by _run_strategies."""

    required_regime = Regime.TREND


class _FakeRepo:
    """AsyncMock-style repo that records calls but needs concrete return values."""

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


def _spy_run_gate(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Wrap the real run_gate so guardrails still execute, capturing kwargs."""
    calls: list[dict[str, Any]] = []
    real = scan_mod.run_gate

    def spy(candidate: SignalCandidate, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return real(candidate, **kwargs)

    monkeypatch.setattr(scan_mod, "run_gate", spy)
    return calls


def _patch_common(monkeypatch: pytest.MonkeyPatch, candidate: SignalCandidate) -> None:
    monkeypatch.setattr(scan_mod, "STRATEGY_REGISTRY", {"fake_strat": _FakeStrategy})
    monkeypatch.setattr(scan_mod, "_load_strategy_config", lambda _n: StrategyConfig(raw={}))
    monkeypatch.setattr(scan_mod, "generate_candidate", lambda *a, **k: candidate)
    # similar-setups lookup is imported lazily inside the LLM branch.
    monkeypatch.setattr("rtrade.ml.similar.find_similar_setups", lambda *a, **k: {"n": 0})


def _build_cfg(*, llm_enabled: bool) -> AppConfig:
    cfg = AppConfig.load(config_dir=_CONFIG_DIR, env_file=None)
    cfg.settings.llm.enabled = llm_enabled
    cfg.settings.signal.edge_quality.enabled = False  # skip edge-quality branch
    return cfg


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
        live_price=candidate.levels.entry_limit,  # zero drift -> GR-06 passes
        session_repo=repo,  # type: ignore[arg-type]
        state_repo=repo,  # type: ignore[arg-type]
        audit_repo=audit,  # type: ignore[arg-type]
        now=datetime(2026, 7, 1, 6, 30, tzinfo=UTC),
        calendar_stale=False,
    ), repo


@pytest.mark.asyncio
async def test_post_llm_gate_invoked_on_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    """When llm.enabled, a 2nd run_gate runs with P2 args after a PUBLISH decision."""
    candidate = _make_candidate()
    pack = _make_pack()
    _patch_common(monkeypatch, candidate)
    monkeypatch.setattr(scan_mod, "_build_pack", lambda *a, **k: pack)
    monkeypatch.setattr(scan_mod, "_build_llm_client", lambda _cfg: object())
    monkeypatch.setattr(scan_mod, "resolve_role_model", lambda _cfg, _role: "test/model")
    monkeypatch.setattr(scan_mod, "should_escalate", lambda *a, **k: False)

    pres = PipelineResult(
        decision=PipelineDecision.PUBLISH,
        confidence=0.80,
        rationale="ok",
        key_risks=["r"],
        sources=[pack.source_ids[0]],  # valid citation -> GR-11 passes
        llm_used=True,
    )

    async def _fake_pipeline(*_a: Any, **_k: Any) -> PipelineResult:
        return pres

    monkeypatch.setattr(scan_mod, "run_llm_pipeline", _fake_pipeline)

    calls = _spy_run_gate(monkeypatch)
    result, _repo = await _run(_build_cfg(llm_enabled=True), candidate)

    # Two gate runs: deterministic (pre-LLM) + post_llm.
    assert len(calls) == 2, calls
    post = calls[1]
    assert post["confidence"] == pytest.approx(0.80)
    assert post["original_candidate"] is candidate
    assert post["sources"] == [pack.source_ids[0]]
    assert post["pack_source_ids"] == set(pack.source_ids)
    # Full 13-gate run: deterministic args re-passed.
    assert post["live_quote_required"] is True
    assert post["regime"] == Regime.TREND
    # Post-LLM gate passed -> signal PUBLISHED.
    assert result.status == "published"


@pytest.mark.asyncio
async def test_no_post_llm_gate_when_llm_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """With llm.enabled false (default/CI), run_gate is called exactly once."""
    candidate = _make_candidate()
    _patch_common(monkeypatch, candidate)

    calls = _spy_run_gate(monkeypatch)
    result, _repo = await _run(_build_cfg(llm_enabled=False), candidate)

    assert len(calls) == 1, calls
    assert "confidence" not in calls[0] or calls[0].get("confidence") is None
    assert result.status == "published"
