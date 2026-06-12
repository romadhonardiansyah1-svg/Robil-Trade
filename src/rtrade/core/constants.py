"""Core enums. String values are a serialization contract (DB rows, Telegram,
API payloads, LLM structured output) — changing them is a breaking change and
requires a data migration plus an ADR.
"""

from enum import StrEnum


class Action(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    ABSTAIN = "ABSTAIN"


class Regime(StrEnum):
    TREND = "TREND"
    RANGE = "RANGE"
    CRISIS = "CRISIS"


class Timeframe(StrEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


class SignalStatus(StrEnum):
    PUBLISHED = "PUBLISHED"  # passed all gates, delivered to user
    REJECTED = "REJECTED"  # failed a guardrail / VETO
    ABSTAINED = "ABSTAINED"  # AI/confidence not convinced
    FILLED = "FILLED"  # paper: limit touched
    TP_HIT = "TP_HIT"
    SL_HIT = "SL_HIT"
    EXPIRED = "EXPIRED"  # limit untouched until valid_until


class Market(StrEnum):
    METALS = "metals"
    FOREX = "forex"
    CRYPTO = "crypto"


class AuditStage(StrEnum):
    CANDIDATE = "candidate"
    ANALYST = "analyst"
    CRITIC = "critic"
    VERIFIER = "verifier"
    GATE = "gate"
    DELIVERY = "delivery"
    REGIME_SHADOW = "regime_shadow"
