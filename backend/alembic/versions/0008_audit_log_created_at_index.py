"""Index audit_log.created_at (V-024, F8.10): screen 4s's filterable log
sorts/filters by date, and the ticket's own volume AC ("10K rows, 4s stays
responsive") needs this beyond the existing check_run_id index — a raw
date-range query with no check_run_id filter would otherwise be a full
table scan.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-25 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
