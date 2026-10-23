"""V-084: persisted assessment revision foundation.

Revision ID: 0039
Revises: 0038
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Baseline for existing assessments, not a reconstruction of prior edits.
    op.add_column(
        "readiness_report",
        sa.Column("assessment_revision", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        op.f("ck_readiness_report_assessment_revision_nonnegative"),
        "readiness_report",
        "assessment_revision >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_readiness_report_assessment_revision_nonnegative"),
        "readiness_report",
        type_="check",
    )
    op.drop_column("readiness_report", "assessment_revision")
