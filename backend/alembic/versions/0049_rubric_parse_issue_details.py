"""Structured rubric parse issues, the source they were checked against, and
a save revision (BUG-566).

Revision ID: 0049
Revises: 0048
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade():
    # Nullable, no backfill: rubrics parsed earlier keep their plain-text
    # issues only, and the review screen falls back to those.
    op.add_column("rubric", sa.Column("parse_issue_details", JSONB()))
    op.add_column("rubric", sa.Column("source_snapshot", JSONB()))
    op.add_column("rubric", sa.Column("revision", sa.Integer(), server_default="0", nullable=False))


def downgrade():
    op.drop_column("rubric", "revision")
    op.drop_column("rubric", "source_snapshot")
    op.drop_column("rubric", "parse_issue_details")
