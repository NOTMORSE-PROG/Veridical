"""Add rubric.parse_status + parse_issues (V-011, F2.2 validation gate).

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rubric",
        sa.Column(
            "parse_status",
            sa.Enum("parsed", "needs_review", name="rubricparsestatus", native_enum=False),
            server_default="parsed",
            nullable=False,
        ),
    )
    op.add_column(
        "rubric",
        sa.Column("parse_issues", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rubric", "parse_issues")
    op.drop_column("rubric", "parse_status")
