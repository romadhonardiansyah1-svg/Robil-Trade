"""Initial schema — IMPLEMENTATION_PLAN §10 DDL, candles as TimescaleDB hypertable.

Revision ID: 0001
Revises:
Create Date: 2026-06-11
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.create_table(
        "instruments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.Text(), nullable=False, unique=True),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_symbol", sa.Text(), nullable=False),
        sa.Column("pip_size", sa.Numeric(), nullable=False),
        sa.Column("config", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )

    op.create_table(
        "candles",
        sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("timeframe", sa.Text(), nullable=False),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(), nullable=False),
        sa.Column("high", sa.Numeric(), nullable=False),
        sa.Column("low", sa.Numeric(), nullable=False),
        sa.Column("close", sa.Numeric(), nullable=False),
        sa.Column("volume", sa.Numeric(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("instrument_id", "timeframe", "ts"),
    )
    # Hypertable partitioned on ts; PK already contains ts (timescale requirement).
    op.execute("SELECT create_hypertable('candles', 'ts', if_not_exists => TRUE)")

    op.create_table(
        "economic_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("impact", sa.Text(), nullable=False),
        sa.Column("event_time", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("actual", sa.Numeric(), nullable=True),
        sa.Column("forecast", sa.Numeric(), nullable=True),
        sa.Column("previous", sa.Numeric(), nullable=True),
        sa.Column("fetched_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )

    op.create_table(
        "derivatives_snapshots",
        sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("funding_rate", sa.Numeric(), nullable=True),
        sa.Column("open_interest", sa.Numeric(), nullable=True),
        sa.PrimaryKeyConstraint("instrument_id", "ts"),
    )

    op.create_table(
        "signals",
        sa.Column("signal_id", sa.Text(), primary_key=True),
        sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("timeframe", sa.Text(), nullable=False),
        sa.Column("strategy", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("entry_limit", sa.Numeric(), nullable=True),
        sa.Column("stop_loss", sa.Numeric(), nullable=True),
        sa.Column("take_profit", sa.Numeric(), nullable=True),
        sa.Column("position_size", sa.Numeric(), nullable=True),
        sa.Column("risk_pct", sa.Numeric(), nullable=True),
        sa.Column("confluence_score", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Numeric(), nullable=True),
        sa.Column("bar_ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("valid_until", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("outcome_r", sa.Numeric(), nullable=True),
        sa.Column("payload", JSONB(), nullable=False),
        sa.UniqueConstraint(
            "instrument_id", "timeframe", "strategy", "bar_ts", name="uq_signals_dedup"
        ),
    )

    op.create_table(
        "signal_audits",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("signal_id", sa.Text(), nullable=True),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("detail", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "strategy_state",
        sa.Column("strategy", sa.Text(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("disabled_reason", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("strategy", sa.Text(), nullable=True),
        sa.Column("instrument", sa.Text(), nullable=True),
        sa.Column("params", JSONB(), nullable=True),
        sa.Column("window_start", sa.Date(), nullable=True),
        sa.Column("window_end", sa.Date(), nullable=True),
        sa.Column("is_oos", sa.Boolean(), nullable=True),
        sa.Column("metrics", JSONB(), nullable=True),
        sa.Column("gates", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("backtest_runs")
    op.drop_table("strategy_state")
    op.drop_table("signal_audits")
    op.drop_table("signals")
    op.drop_table("derivatives_snapshots")
    op.drop_table("economic_events")
    op.drop_table("candles")
    op.drop_table("instruments")
