"""Add flag.detail (V-033): F5/F6 (V-027-033) put many flags under one
check_result, each needing its own honest-wording reason — check_result's
single top-level `detail` key (V-020's original design, one flag per
check_result) can't hold that. `app.flags.service._to_flag_out` prefers
this new per-flag field and falls back to check_result.detail for the
older one-flag-per-check_result shape.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "flag",
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("flag", "detail")
