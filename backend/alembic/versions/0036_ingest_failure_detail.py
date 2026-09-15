"""BUG-066: honest, specific ingest-failure detail on Manuscript.

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-16 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "manuscript",
        sa.Column("ingest_failure_detail", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("manuscript", "ingest_failure_detail")
