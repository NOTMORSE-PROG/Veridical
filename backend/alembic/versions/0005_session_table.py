"""Add session table (V-014, F9.1: login sessions).

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session",
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("instructor_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["instructor_id"],
            ["instructor.id"],
            name=op.f("fk_session_instructor_id_instructor"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("token", name=op.f("pk_session")),
    )
    op.create_index(op.f("ix_session_instructor_id"), "session", ["instructor_id"], unique=False)
    op.create_index(op.f("ix_session_expires_at"), "session", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_session_expires_at"), table_name="session")
    op.drop_index(op.f("ix_session_instructor_id"), table_name="session")
    op.drop_table("session")
