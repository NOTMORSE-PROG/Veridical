"""V-066: adds `manuscript_group.title` -- the full capstone title V-063's
title-page extraction already computes (`app/ingest/titlepage.py`) and shows
in the confirm dialog, but never persisted anywhere. NULL = "not set, never
guessed" -- same convention as `program_id`/`ingest_failure_reason` -- true
for every pre-existing row (no backfill source: the raw extraction that
would let us re-derive it isn't re-read here) and for any group created
before an instructor confirms a proposal that carried a title.

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("manuscript_group", sa.Column("title", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("manuscript_group", "title")
