"""V-081: explicit group assignment and original proposal provenance.

Revision ID: 0038
Revises: 0037
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No backfill: neither a name nor a member list proves confirmation.
    op.add_column("manuscript", sa.Column("group_confirmation", postgresql.JSONB(), nullable=True))
    op.add_column(
        "manuscript_group", sa.Column("proposal_name_normalized", sa.String(200), nullable=True)
    )
    op.create_index(
        "ix_manuscript_group_proposal_name_normalized",
        "manuscript_group",
        ["proposal_name_normalized"],
    )


def downgrade() -> None:
    op.drop_index("ix_manuscript_group_proposal_name_normalized", table_name="manuscript_group")
    op.drop_column("manuscript_group", "proposal_name_normalized")
    op.drop_column("manuscript", "group_confirmation")
