"""Add manuscript.purged_at (V-042, F7 archive): purge removes the F7
embedding archive rows + the stored raw/upload files, but the Manuscript
row, its check-run history, and any decision record are deliberately kept
(charter: decisions are never silently unmade). `purged_at` is the only
new column needed to distinguish "processed, still archived" from
"processed, instructor purged the archive" honestly instead of a purged
row looking identical to one that was simply never checked.

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-14 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "manuscript",
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("manuscript", "purged_at")
