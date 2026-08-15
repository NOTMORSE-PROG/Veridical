"""V-043 (production deployment) finding, live-reproduced: migration
0014's `upgrade()` read `get_settings().embedding_dim` at migration-run
time instead of hardcoding the value it was meant to lock in permanently.
Every environment that never carried a stray `EMBEDDING_DIM` env var
override (local dev, CI's fresh-DB-per-run) happened to get the correct
answer (256) anyway, by coincidence -- but the production Render service
still had the OLD provisional `EMBEDDING_DIM=768` set (a leftover from
before 0014 existed, never cleaned up when V-036 finalized 256), so 0014
"succeeded" there and marked itself applied while silently leaving the
column at `vector(768)`, the same value it started at.

This migration is the actual repair mechanism for any environment stuck
in that state (idempotent-safe: both tables are provably empty in every
real environment today -- F7 has never processed a real manuscript, this
was caught before it could). 0014 itself was also fixed to hardcode 256
directly, so no FRESH environment can hit this again; this migration
exists for the one that already had.

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-16 00:00:00.000000

"""

from collections.abc import Sequence

import pgvector.sqlalchemy

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMBEDDING_DIM = 256


def upgrade() -> None:
    op.execute("TRUNCATE TABLE manuscript_archive, manuscript_chapter_archive")
    op.drop_index("ix_manuscript_archive_embedding", table_name="manuscript_archive")
    op.alter_column(
        "manuscript_archive",
        "embedding",
        type_=pgvector.sqlalchemy.Vector(_EMBEDDING_DIM),
        postgresql_using=f"embedding::vector({_EMBEDDING_DIM})",
    )
    op.create_index(
        "ix_manuscript_archive_embedding",
        "manuscript_archive",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.drop_index(
        "ix_manuscript_chapter_archive_embedding", table_name="manuscript_chapter_archive"
    )
    op.alter_column(
        "manuscript_chapter_archive",
        "embedding",
        type_=pgvector.sqlalchemy.Vector(_EMBEDDING_DIM),
        postgresql_using=f"embedding::vector({_EMBEDDING_DIM})",
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
    # A no-op, not a real reversal (CODING.md §2 still needs a working
    # down-up cycle, so this can't just raise) -- the state this migration
    # "started from" was a live bug (an environment-dependent, non-
    # deterministic dimension: 768 where the stray env var existed, 256
    # everywhere else), not a single well-defined prior schema to restore.
    # Reverting to 0022 leaves the columns at vector(256), same as
    # upgrade() -- correct for every environment that matters for the
    # up/down/up test cycle (dev, CI), since none of them ever had the
    # stray env var this migration exists to repair.
    op.execute("TRUNCATE TABLE manuscript_archive, manuscript_chapter_archive")
