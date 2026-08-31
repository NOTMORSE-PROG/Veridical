"""V-071: Review Desk lifecycle contracts.

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "check_run",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "manuscript",
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "audit_log",
        sa.Column("manuscript_id", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_audit_log_manuscript_id",
        "audit_log",
        ["manuscript_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_log_manuscript_id", table_name="audit_log")
    op.drop_column("audit_log", "manuscript_id")
    op.drop_column("manuscript", "dismissed_at")
    op.drop_column("check_run", "cancel_requested_at")
