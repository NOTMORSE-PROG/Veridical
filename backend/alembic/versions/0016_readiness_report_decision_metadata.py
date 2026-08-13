"""Add readiness_report.decided_at/decided_by_instructor_id/decision_note
(V-038, F8.5): `decision` itself has existed since migration 0001 but was
never wired to anything. The report needs to show WHEN a decision was made
and its optional note without a second query against the audit log on
every report fetch -- same "inline what the screen needs, audit log
carries the full trail" precedent V-023's `CheckResult.detail.resolution`
already established. `decided_by_instructor_id` is stored for
completeness (and because it IS the audit-of-record's actor) even though
this ticket's UI doesn't need to render it yet -- V7 is the first point at
which "who" could ever differ from "the one instructor on this account".

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "readiness_report",
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "readiness_report",
        sa.Column(
            "decided_by_instructor_id",
            sa.BigInteger(),
            sa.ForeignKey("instructor.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "readiness_report",
        sa.Column("decision_note", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("readiness_report", "decision_note")
    op.drop_column("readiness_report", "decided_by_instructor_id")
    op.drop_column("readiness_report", "decided_at")
