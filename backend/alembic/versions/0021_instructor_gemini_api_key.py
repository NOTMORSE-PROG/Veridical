"""Add instructor.gemini_api_key_encrypted (V-052, BYOK): Fernet-encrypted
storage for an instructor's own pasted Gemini API key. NULL = uses the
shared pool key (today's only behavior, unchanged for every existing row).
Never plaintext, never returned by any API response (settings.py exposes
only a `has_own_api_key` boolean).

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-15 00:00:00.000003

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "instructor",
        sa.Column("gemini_api_key_encrypted", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("instructor", "gemini_api_key_encrypted")
