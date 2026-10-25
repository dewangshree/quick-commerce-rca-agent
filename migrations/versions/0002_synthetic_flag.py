"""flag synthetic (generated demo) rows

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-25
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("store_hourly_metrics",
                  sa.Column("is_synthetic", sa.Boolean, nullable=False, server_default=sa.false()))
    op.create_index("ix_hourly_synthetic_date", "store_hourly_metrics", ["is_synthetic", "charge_date"])


def downgrade() -> None:
    op.drop_index("ix_hourly_synthetic_date", "store_hourly_metrics")
    op.drop_column("store_hourly_metrics", "is_synthetic")
