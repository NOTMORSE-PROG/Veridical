"""Add citation table (V-006, F1.5): structured reference-list entries.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-17 18:57:17.868081

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "citation",
        sa.Column("manuscript_id", sa.BigInteger(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("authors", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("venue", sa.Text(), nullable=True),
        sa.Column("doi", sa.String(length=255), nullable=True),
        sa.Column("isbn", sa.String(length=32), nullable=True),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column(
            "parse_status",
            sa.Enum("parsed", "parse_failed", name="citationparsestatus", native_enum=False),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["manuscript_id"],
            ["manuscript.id"],
            name=op.f("fk_citation_manuscript_id_manuscript"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_citation")),
    )
    op.create_index(op.f("ix_citation_manuscript_id"), "citation", ["manuscript_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_citation_manuscript_id"), table_name="citation")
    op.drop_table("citation")
