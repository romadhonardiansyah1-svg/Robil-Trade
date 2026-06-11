"""Enum string values are a serialization contract (DB/API/Telegram/LLM).

If one of these tests fails you are breaking stored data — write a migration
and an ADR instead of editing the expected values.
"""

from rtrade.core.constants import Action, AuditStage, Market, Regime, SignalStatus, Timeframe


def test_action_values() -> None:
    assert {a.value for a in Action} == {"BUY", "SELL", "ABSTAIN"}


def test_regime_values() -> None:
    assert {r.value for r in Regime} == {"TREND", "RANGE", "CRISIS"}


def test_timeframe_values() -> None:
    assert {t.value for t in Timeframe} == {"1h", "4h", "1d"}


def test_signal_status_values() -> None:
    assert {s.value for s in SignalStatus} == {
        "PUBLISHED",
        "REJECTED",
        "ABSTAINED",
        "FILLED",
        "TP_HIT",
        "SL_HIT",
        "EXPIRED",
    }


def test_market_values() -> None:
    assert {m.value for m in Market} == {"metals", "forex", "crypto"}


def test_audit_stage_values() -> None:
    assert {s.value for s in AuditStage} == {
        "candidate",
        "analyst",
        "critic",
        "verifier",
        "gate",
        "delivery",
    }
