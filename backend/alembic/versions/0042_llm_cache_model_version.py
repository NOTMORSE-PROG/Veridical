"""V-081: retain optional provider version with cached responses.

Revision ID: 0042
Revises: 0041
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("llm_response_cache", sa.Column("model_version", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("llm_response_cache", "model_version")
