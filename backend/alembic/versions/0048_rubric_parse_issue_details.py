"""Structured rubric parse issues (BUG-566).

Revision ID: 0048
Revises: 0047
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade():
    # Nullable, no backfill: rubrics parsed earlier keep their plain-text
    # issues only, and the review screen falls back to those.
    op.add_column("rubric", sa.Column("parse_issue_details", JSONB()))


def downgrade():
    op.drop_column("rubric", "parse_issue_details")
