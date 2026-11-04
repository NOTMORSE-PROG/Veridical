"""Index existing Library passage text for supplementary lexical retrieval.

Revision ID: 0050
Revises: 0049
"""

from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE INDEX ix_passage_literal_search ON manuscript_passage_archive "
        "USING gin (to_tsvector('simple'::regconfig, text))"
    )


def downgrade():
    op.drop_index("ix_passage_literal_search", table_name="manuscript_passage_archive")
