"""V-072 (F7.4): passage-level embedding archive -- one row per ~150-word
passage, INCLUDING reference-list and block-quote passages (tagged via
`is_reference_list`/`is_block_quote`, not dropped) so the instructor's
exclusion toggle has something real to switch on. `text`/`context_text`
are the bounded-excerpt mechanism itself (owner ruling, carried from
V-058/BUG-050 Branch B) -- computed once here from this manuscript's own
extraction, never read live from a (potentially another account's) file.

Dimension hardcoded to 256, not `get_settings().embedding_dim` -- same
migration-must-be-deterministic lesson 0014 already fixed the hard way
(BUG, V-043 production finding): a mutable env-backed setting read at
migration-run-time can silently diverge from what actually got locked in.
potion-base-8M's dimension is already finalized (D-011); this table is
created fresh at that fixed size, never migrated from a different one.

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-20 00:00:00.000000

"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMBEDDING_DIM = 256


def upgrade() -> None:
    op.create_table(
        "manuscript_passage_archive",
        sa.Column("manuscript_id", sa.BigInteger(), nullable=False),
        sa.Column("passage_index", sa.Integer(), nullable=False),
        sa.Column("chapter_index", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("paragraph", sa.Integer(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("context_text", sa.Text(), nullable=False),
        sa.Column("is_reference_list", sa.Boolean(), nullable=False),
        sa.Column("is_block_quote", sa.Boolean(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(_EMBEDDING_DIM), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
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
            name=op.f("fk_manuscript_passage_archive_manuscript_id_manuscript"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_manuscript_passage_archive")),
        sa.UniqueConstraint(
            "manuscript_id",
            "passage_index",
            name="uq_manuscript_passage_archive_manuscript_passage",
        ),
    )
    op.create_index(
        op.f("ix_manuscript_passage_archive_manuscript_id"),
        "manuscript_passage_archive",
        ["manuscript_id"],
        unique=False,
    )
    op.create_index(
        "ix_manuscript_passage_archive_embedding",
        "manuscript_passage_archive",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_manuscript_passage_archive_embedding", table_name="manuscript_passage_archive"
    )
    op.drop_index(
        op.f("ix_manuscript_passage_archive_manuscript_id"),
        table_name="manuscript_passage_archive",
    )
    op.drop_table("manuscript_passage_archive")
