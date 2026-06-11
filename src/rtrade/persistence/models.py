"""SQLAlchemy ORM models — MUST stay 1:1 with IMPLEMENTATION_PLAN §10 DDL.

Schema changes go through Alembic migrations only; never edit a shipped
migration. `candles` is a TimescaleDB hypertable (created in migration 0001 —
hypertable conversion cannot be expressed in ORM metadata).

Money/price columns are NUMERIC → Python Decimal (exactness in storage);
conversion to float happens at the pandas boundary in the indicator engine.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    market: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_symbol: Mapped[str] = mapped_column(Text, nullable=False)
    pip_size: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class Candle(Base):
    """OHLCV bar. `ts` is the OPEN time of the bar, UTC (see core.timeutil)."""

    __tablename__ = "candles"

    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id"), primary_key=True, autoincrement=False
    )
    timeframe: Mapped[str] = mapped_column(Text, primary_key=True)
    ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    open: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("0"))


class EconomicEvent(Base):
    __tablename__ = "economic_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # hash(provider,event,time)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    impact: Mapped[str] = mapped_column(Text, nullable=False)  # low|medium|high
    event_time: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    actual: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    forecast: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    previous: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class DerivativesSnapshot(Base):
    __tablename__ = "derivatives_snapshots"

    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id"), primary_key=True, autoincrement=False
    )
    ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    funding_rate: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    open_interest: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        # Scheduler idempotency: re-scanning the same closed bar must not
        # produce a duplicate signal (PLAN §8.12).
        UniqueConstraint(
            "instrument_id", "timeframe", "strategy", "bar_ts", name="uq_signals_dedup"
        ),
    )

    signal_id: Mapped[str] = mapped_column(Text, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    timeframe: Mapped[str] = mapped_column(Text, nullable=False)
    strategy: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    entry_limit: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    position_size: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    risk_pct: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    confluence_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    bar_ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    outcome_r: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)  # R-multiple
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # full TradingSignal


class SignalAudit(Base):
    """Why every signal was published/rejected — kept >= 12 months (PLAN §14.3)."""

    __tablename__ = "signal_audits"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    signal_id: Mapped[str | None] = mapped_column(Text, nullable=True)  # null: pre-candidate
    stage: Mapped[str] = mapped_column(Text, nullable=False)  # AuditStage values
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class StrategyState(Base):
    __tablename__ = "strategy_state"

    strategy: Mapped[str] = mapped_column(Text, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    disabled_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    strategy: Mapped[str | None] = mapped_column(Text, nullable=True)
    instrument: Mapped[str | None] = mapped_column(Text, nullable=True)
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    window_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    window_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_oos: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    gates: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
