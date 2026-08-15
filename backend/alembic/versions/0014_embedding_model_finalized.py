"""Finalize the embedding model (V-036, F7.1): potion-base-8M, 256 dims —
`manuscript_archive.embedding` was provisionally `vector(768)` since V-003
(comment on the column: "Provisional until the embedding model is chosen").
The table has always been empty (F7 never shipped until now), so this
migration truncates it defensively before the ALTER rather than assume,
adds `model_id` (ticket edge case: a future model change makes existing
vectors incomparable — every row must record what produced it, and
V-037's queries must never mix models), and adds the new
`manuscript_chapter_archive` table for per-chapter vectors (ticket:
"whole-doc + per-chapter vectors").

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-08 00:00:00.000000

"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Hardcoded, not `get_settings().embedding_dim` (V-043 deployment finding,
# live-reproduced): this migration's whole job is to LOCK IN the finalized
# dimension permanently -- reading a mutable env-backed setting at
# migration-run-time instead defeats that, and it's exactly what broke
# production. Render's env still carried the OLD provisional
# `EMBEDDING_DIM=768` (a leftover from before this migration existed,
# never cleaned up) when this migration ran there, so it "succeeded" and
# marked itself applied while silently leaving the column at `vector(768)`
# -- the same value it started at. Every other environment (local dev, CI,
# which never set that env var) happened to get the right answer by
# coincidence, not because the migration was actually deterministic. Fixed
# here at the root; migration 0023 repairs any environment already stuck
# on the wrong dimension from before this fix.
_EMBEDDING_DIM = 256


def upgrade() -> None:
    # Defensive, not a real data migration: F7 has never shipped, so this
    # table is empty in every real environment. Truncating first makes the
    # dimension-changing ALTER safe regardless.
    op.execute("TRUNCATE TABLE manuscript_archive")
    op.drop_index("ix_manuscript_archive_embedding", table_name="manuscript_archive")
    op.alter_column(
        "manuscript_archive",
        "embedding",
        type_=pgvector.sqlalchemy.Vector(_EMBEDDING_DIM),
        postgresql_using=f"embedding::vector({_EMBEDDING_DIM})",
    )
    op.add_column("manuscript_archive", sa.Column("model_id", sa.String(200), nullable=False))
    op.create_index(
        "ix_manuscript_archive_embedding",
        "manuscript_archive",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "manuscript_chapter_archive",
        sa.Column("manuscript_id", sa.BigInteger(), nullable=False),
        sa.Column("chapter_index", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("paragraph", sa.Integer(), nullable=True),
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
            name=op.f("fk_manuscript_chapter_archive_manuscript_id_manuscript"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_manuscript_chapter_archive")),
        sa.UniqueConstraint(
            "manuscript_id",
            "chapter_index",
            name="uq_manuscript_chapter_archive_manuscript_chapter",
        ),
    )
    op.create_index(
        op.f("ix_manuscript_chapter_archive_manuscript_id"),
        "manuscript_chapter_archive",
        ["manuscript_id"],
        unique=False,
    )
    op.create_index(
        "ix_manuscript_chapter_archive_embedding",
        "manuscript_chapter_archive",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_manuscript_chapter_archive_embedding", table_name="manuscript_chapter_archive"
    )
    op.drop_index(
        op.f("ix_manuscript_chapter_archive_manuscript_id"),
        table_name="manuscript_chapter_archive",
    )
    op.drop_table("manuscript_chapter_archive")

    op.execute("TRUNCATE TABLE manuscript_archive")
    op.drop_index("ix_manuscript_archive_embedding", table_name="manuscript_archive")
    op.drop_column("manuscript_archive", "model_id")
    op.alter_column(
        "manuscript_archive",
        "embedding",
        type_=pgvector.sqlalchemy.Vector(768),
        postgresql_using="embedding::vector(768)",
    )
    op.create_index(
        "ix_manuscript_archive_embedding",
        "manuscript_archive",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
