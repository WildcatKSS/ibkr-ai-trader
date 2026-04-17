"""Add universe_selections table.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-14 00:00:01.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "universe_selections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scan_date", sa.Date(), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("selected_symbol", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=80), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scan_date", name="uq_universe_selections_scan_date"),
    )
    op.create_index("ix_universe_selections_scan_date", "universe_selections", ["scan_date"])
    op.create_index("ix_universe_selections_status", "universe_selections", ["status"])


def downgrade() -> None:
    op.drop_index("ix_universe_selections_status", table_name="universe_selections")
    op.drop_index("ix_universe_selections_scan_date", table_name="universe_selections")
    op.drop_table("universe_selections")
