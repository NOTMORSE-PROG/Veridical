"""Add instructor.onboarding_dismissed_at (V-055, Flow A first-run onboarding).

NULL = hasn't dismissed yet (true for both a genuinely new account and
every pre-existing dev/demo account -- no production accounts exist yet,
context/STATE.md 2026-08-06, so no backfill is needed).

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-06 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "instructor",
        sa.Column("onboarding_dismissed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("instructor", "onboarding_dismissed_at")
