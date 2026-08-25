"""V-069: adds `criterion.levels` -- a graded performance scale's own
named levels (BEGINNER 1 / ACCEPTABLE 2 / PROFICIENT 3 / EXEMPLARY 4, or
any other institution's scale), captured as structured JSONB instead of
being collapsed into `evidence` prose. Nullable, no default beyond NULL:
every existing criterion is a pass/fail criterion until a fresh
decomposition (or a hand-edit) says otherwise -- this migration changes
no existing row's meaning (AC3: a pass/fail rubric is unaffected).

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "criterion",
        sa.Column("levels", postgresql.JSONB(astext_type=None), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("criterion", "levels")
