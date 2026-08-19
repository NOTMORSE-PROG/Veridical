"""V-063: a `group_member` table -- names observed on a manuscript's own
title page (`By:` block), the durable record V-063's matching rule needs
("short name matches AND at least one member overlaps") to tell two
different teams that happen to share a short name apart from one real
team resubmitting under a reworded subtitle.

No backfill: every existing `Group` row was created before this ticket
(V-062's typed-label path, which has no member concept), so it correctly
starts with zero members -- not a guess, an honest "never observed any."

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "group_member",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_group_member"),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["manuscript_group.id"],
            name="fk_group_member_group_id_manuscript_group",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_group_member_group_id", "group_member", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_group_member_group_id", table_name="group_member")
    op.drop_table("group_member")
