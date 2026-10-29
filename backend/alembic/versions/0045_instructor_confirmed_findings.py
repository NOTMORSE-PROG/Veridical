"""Instructor-confirmed integrity findings (BUG-518).

Revision ID: 0045
Revises: 0044
"""

import sqlalchemy as sa

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "flag",
        sa.Column("finding_confirmed", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column("flag", sa.Column("finding_confirm_note", sa.Text()))
    op.add_column("flag", sa.Column("finding_confirmed_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("flag", "finding_confirmed_at")
    op.drop_column("flag", "finding_confirm_note")
    op.drop_column("flag", "finding_confirmed")
