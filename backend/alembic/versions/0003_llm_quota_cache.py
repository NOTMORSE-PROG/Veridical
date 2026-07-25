"""Add llm_quota_counter + llm_response_cache tables (V-009, ENGINEERING §3).

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_quota_counter",
        sa.Column("quota_day", sa.String(length=10), nullable=False),
        sa.Column("call_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cache_hit_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_quota_counter")),
        sa.UniqueConstraint("quota_day", name=op.f("uq_llm_quota_counter_quota_day")),
    )

    op.create_table(
        "llm_response_cache",
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_response_cache")),
        sa.UniqueConstraint("input_hash", name=op.f("uq_llm_response_cache_input_hash")),
    )


def downgrade() -> None:
    op.drop_table("llm_response_cache")
    op.drop_table("llm_quota_counter")
