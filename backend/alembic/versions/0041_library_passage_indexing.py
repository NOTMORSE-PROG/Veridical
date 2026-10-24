"""V-081: retain measured Library passage-indexing scope without backfilling history.

Revision ID: 0041
Revises: 0040
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "library_entry", sa.Column("passage_indexing_snapshot", postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("library_entry", "passage_indexing_snapshot")
