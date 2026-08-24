"""BUG-092: widens `criterion.type` from VARCHAR(10) to VARCHAR(20) so it
can hold the new `not_assessable` `CriterionType` value (14 chars) --
`native_enum=False` sizes the column to the longest member known when the
column was first created (0001's migration only knew "structural"/
"semantic", 10 chars), and adding a longer Python-side enum member does
NOT widen an already-created column. 20 leaves headroom for a future
member without another migration for a few characters' worth of name.

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "criterion",
        "type",
        existing_type=sa.String(length=10),
        type_=sa.String(length=20),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "criterion",
        "type",
        existing_type=sa.String(length=20),
        type_=sa.String(length=10),
        existing_nullable=False,
    )
