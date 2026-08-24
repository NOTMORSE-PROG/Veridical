"""BUG-078: adds `flag.confirmed_citation_source` -- distinguishes a flag
resolved via "Confirm this source" (also marks the shared citation_cache
row) from an ordinary override. Both set overridden/override_reason the
same way, and override_reason is instructor-authored free text on BOTH
paths (ui-designer spec, 2026-08-24), so a real boolean column is needed
rather than inferring the path from override_reason's text.

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "flag",
        sa.Column(
            "confirmed_citation_source",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("flag", "confirmed_citation_source")
