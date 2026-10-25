"""widen investigations.status (needs_clarification is 19 chars)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-25
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("investigations", "status", type_=sa.String(32), existing_type=sa.String(16),
                    existing_nullable=False)


def downgrade() -> None:
    op.alter_column("investigations", "status", type_=sa.String(16), existing_type=sa.String(32),
                    existing_nullable=False)
