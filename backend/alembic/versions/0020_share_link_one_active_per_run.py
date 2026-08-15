"""Add a partial unique index enforcing at most one active (non-revoked)
share_link per check_run (V-040, backend-critic finding, P1, live-
reproduced): the "only one active link at a time" invariant was
previously enforced only in the service layer (check-then-act), which a
real concurrency test (15 simultaneous POST /check-runs/{id}/share
requests) proved unsafe -- 13 of 15 ended up simultaneously "active" for
the same report, and a single revoke/regenerate then only killed one of
them, leaving the rest fully live and reachable. A double-click on
"Regenerate link" (a literal button in the UI) or two open tabs is
enough to trigger this in practice.

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-15 00:00:00.000002

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_share_link_one_active_per_run "
        "ON share_link (check_run_id) WHERE revoked_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_share_link_one_active_per_run")
