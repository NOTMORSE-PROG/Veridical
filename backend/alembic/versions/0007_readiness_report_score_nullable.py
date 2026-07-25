"""Make readiness_report.composite_score nullable (V-019, F8.1): an
all-escalated run (or a rubric whose decidable weight sum is zero) has no
real composite number to report — status becomes 'needs_review' with a
NULL score, never a fabricated 0/100 (charter rule 9).

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "readiness_report", "composite_score", existing_type=sa.Numeric(5, 2), nullable=True
    )


def downgrade() -> None:
    op.alter_column(
        "readiness_report", "composite_score", existing_type=sa.Numeric(5, 2), nullable=False
    )
