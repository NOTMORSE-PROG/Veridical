"""Add manuscript.original_filename (BUG-022): group_label defaults to a
constant, near-zero-information string ("Ungrouped") for any manuscript
not explicitly grouped -- the common case pre-V7 student portal -- so two
manuscripts routinely render identically in every picker/list. The
original uploaded filename was already received on every upload
(app/ingest/router.py) but discarded after deriving a storage suffix;
this persists it instead. NULL for pre-existing rows (no backfill source
-- the original bytes were already gone).

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "manuscript",
        sa.Column("original_filename", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("manuscript", "original_filename")
