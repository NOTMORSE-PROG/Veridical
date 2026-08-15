"""Add llm_quota_counter.instructor_id (V-052, BYOK): quota becomes per
(day, model, key owner) instead of a single shared island per (day, model).
NULL instructor_id = the shared pool key (every existing row, unchanged).

Replaces the old plain UniqueConstraint("quota_day", "model") with two
partial unique indexes, not one: Postgres treats NULL as distinct from
every other NULL in a unique constraint, so a plain
(quota_day, model, instructor_id) constraint would let unlimited
shared-key rows exist for the same (day, model) -- the exact same pattern
V-040's migration 0020 used for `share_link`.

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-15 00:00:00.000004

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_quota_counter",
        sa.Column("instructor_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_llm_quota_counter_instructor_id",
        "llm_quota_counter",
        "instructor",
        ["instructor_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("uq_llm_quota_counter_day_model", "llm_quota_counter", type_="unique")
    op.create_index(
        "uq_llm_quota_counter_shared",
        "llm_quota_counter",
        ["quota_day", "model"],
        unique=True,
        postgresql_where=sa.text("instructor_id IS NULL"),
    )
    op.create_index(
        "uq_llm_quota_counter_own",
        "llm_quota_counter",
        ["quota_day", "model", "instructor_id"],
        unique=True,
        postgresql_where=sa.text("instructor_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_llm_quota_counter_own", table_name="llm_quota_counter")
    op.drop_index("uq_llm_quota_counter_shared", table_name="llm_quota_counter")
    op.create_unique_constraint(
        "uq_llm_quota_counter_day_model", "llm_quota_counter", ["quota_day", "model"]
    )
    op.drop_constraint(
        "fk_llm_quota_counter_instructor_id", "llm_quota_counter", type_="foreignkey"
    )
    op.drop_column("llm_quota_counter", "instructor_id")
