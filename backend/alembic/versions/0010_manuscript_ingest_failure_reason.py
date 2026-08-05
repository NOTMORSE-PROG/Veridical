"""Add manuscript.ingest_failure_reason (BUG-016, V-055).

A failed-ingestion dashboard row previously gave the instructor no reason
and no recovery path (dead end). NULL on existing rows means "failed
before this field existed" -- never backfilled with a guessed reason.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-05 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "manuscript",
        sa.Column("ingest_failure_reason", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("manuscript", "ingest_failure_reason")
