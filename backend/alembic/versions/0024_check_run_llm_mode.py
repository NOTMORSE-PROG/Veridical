"""BUG-049: persist which LLM backend (fake fixtures vs real Gemini)
produced a check_run's results. Without this, a fake-LLM-mode run's
report/PDF/public adviser link rendered fixture data (a canned vision
table, a fabricated forensics flag with a real page anchor) exactly like
a genuine finding, with no way to tell after the fact which runs were
real.

Existing rows backfill to 'unknown' -- honest, not a guess: this project
has no way to know after the fact which of its pre-migration runs were
fake or real (fake-LLM mode has always been the dev/CI default). New
rows always get 'fake'/'real' explicitly at creation
(`app.pipeline.service.create_check_run`); nothing new ever writes
'unknown'.

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "check_run",
        sa.Column("llm_mode", sa.String(), nullable=False, server_default="unknown"),
    )


def downgrade() -> None:
    op.drop_column("check_run", "llm_mode")
