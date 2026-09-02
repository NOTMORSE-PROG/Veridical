"""BUG-140: content-identity hash on Manuscript.

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "manuscript",
        sa.Column("content_hash", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_manuscript_content_hash",
        "manuscript",
        ["content_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_manuscript_content_hash", table_name="manuscript")
    op.drop_column("manuscript", "content_hash")
