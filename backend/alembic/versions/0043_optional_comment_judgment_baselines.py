"""Recognize legacy ordinary judgments with optional empty comments (V-085).

Revision ID: 0043
Revises: 0042
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The later optional-comment contract recognizes ordinary instructor grades
    # independently of their comment. Do not change an applied migration, grade,
    # reason, attribution, or audit record; only assign a missing legacy token.
    op.get_bind().execute(
        sa.text("""
        UPDATE check_result AS result
        SET judgment_revision = 0
        FROM check_run AS run, criterion
        WHERE result.judgment_revision IS NULL
          AND result.check_run_id = run.id
          AND result.criterion_id = criterion.id
          AND criterion.rubric_id = run.rubric_id
          AND result.kind IN ('semantic', 'structural')
          AND jsonb_typeof(result.detail) = 'object'
          AND jsonb_typeof(result.detail->'resolution') = 'object'
          AND jsonb_typeof(result.detail#>'{resolution,reason}') = 'string'
          AND (
            (result.detail#>>'{resolution,type}' = 'mark_pass'
              AND result.outcome = 'passed' AND result.score = 100)
            OR (result.detail#>>'{resolution,type}' = 'mark_fail'
              AND result.outcome = 'failed' AND result.score = 0)
            OR (result.detail#>>'{resolution,type}' = 'mark_level'
              AND result.outcome = 'passed' AND result.score BETWEEN 0 AND 100)
          )
        """)
    )


def downgrade() -> None:
    # A zero token does not record which migration recognized it. Preserve
    # valid legacy identities rather than guessing and clearing another writer.
    pass
