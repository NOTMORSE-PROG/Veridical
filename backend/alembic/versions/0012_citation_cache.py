"""Add citation_cache table (V-028, F5.6): external citation-verification
results keyed by DOI/ISBN/normalized-title, so re-runs of the same
manuscript never re-query CrossRef/S2/OpenLibrary/GBooks.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-06 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "citation_cache",
        sa.Column("key_kind", sa.String(length=16), nullable=False),
        sa.Column("key_value", sa.String(length=512), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("instructor_confirmed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("instructor_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_citation_cache")),
        sa.UniqueConstraint("key_kind", "key_value", name="uq_citation_cache_key"),
    )


def downgrade() -> None:
    op.drop_table("citation_cache")
