"""add calendar_source_health table

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-19

Per-source calendar health metadata (FR-CAL-04). Replaces buggy
MAX(fetched_at) freshness derivation.
"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "calendar_source_health",
        sa.Column("source", sa.Text(), primary_key=True),
        sa.Column("last_success", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_attempt", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("calendar_source_health")
