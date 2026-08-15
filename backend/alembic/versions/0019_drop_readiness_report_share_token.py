"""Drop readiness_report.share_token (V-040, F8.7): reserved since
migration 0001's original schema, never wired to any code (verified:
grepped every backend/frontend file, zero references outside the model
and its own creating migration). Superseded by the new `share_link`
table (migration 0018) instead of being implemented as originally
reserved, for two real gaps against this ticket's own ACs: a bare UUIDv4
carries ~122 bits of entropy, not the AC's required >=128-bit floor
(6 bits are fixed for version/variant), and a single nullable column
can't hold an optional expiry or any history of past links (an
instructor's own audit trail of "was this ever shared, and when").

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-15 00:00:00.000001

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_readiness_report_share_token", "readiness_report", type_="unique")
    op.drop_column("readiness_report", "share_token")


def downgrade() -> None:
    op.add_column("readiness_report", sa.Column("share_token", sa.Uuid(), nullable=True))
    op.create_unique_constraint(
        "uq_readiness_report_share_token", "readiness_report", ["share_token"]
    )
