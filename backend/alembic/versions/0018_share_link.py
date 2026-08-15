"""Add share_link table (V-040, F8.7): a read-only, tokenized link to
one report -- the lightest honest implementation of "shared down to
adviser" (proposal §1.4). No adviser accounts; the token itself is the
credential. Rows are immutable once created (regenerate = revoke +
insert, never mutate an existing token in place).

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "share_link",
        sa.Column("token", sa.String(length=64), primary_key=True),
        sa.Column(
            "check_run_id",
            sa.BigInteger(),
            sa.ForeignKey("check_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_share_link_check_run_id", "share_link", ["check_run_id"])


def downgrade() -> None:
    op.drop_index("ix_share_link_check_run_id", table_name="share_link")
    op.drop_table("share_link")
