"""A format can set its own citation recency window (V-092).

NULL keeps every existing format on the configured defaults, so no backfill
is needed.

Revision ID: 0048
Revises: 0047
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "rubric",
        sa.Column("citation_recency", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade():
    op.drop_column("rubric", "citation_recency")
