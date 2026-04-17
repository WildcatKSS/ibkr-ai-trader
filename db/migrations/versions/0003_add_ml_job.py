"""Add ml_jobs table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-14 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ml_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.String(length=40), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("requested_by", sa.String(length=80), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ml_jobs_status", "ml_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_ml_jobs_status", table_name="ml_jobs")
    op.drop_table("ml_jobs")
