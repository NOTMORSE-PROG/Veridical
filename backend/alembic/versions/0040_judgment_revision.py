"""V-085: version current instructor judgments without rewriting history.

Revision ID: 0040
Revises: 0039
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("check_result", sa.Column("judgment_revision", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        op.f("ck_check_result_judgment_revision_nonnegative"),
        "check_result",
        "judgment_revision >= 0",
    )
    # Preserve legacy JSON and audit events byte-for-byte. Missing optional
    # attribution/rung facts do not erase a recognizable manual judgment.
    # This frozen recognition contract is tested against the runtime decoder.
    candidates = op.get_bind().execute(
        sa.text("""
        SELECT result.id, result.detail#>>'{resolution,reason}' AS reason
        FROM check_result AS result
        JOIN check_run AS run ON result.check_run_id = run.id
        JOIN criterion ON result.criterion_id = criterion.id AND criterion.rubric_id = run.rubric_id
        WHERE result.kind IN ('semantic', 'structural')
          AND jsonb_typeof(result.detail) = 'object'
          AND jsonb_typeof(result.detail->'resolution') = 'object'
          AND jsonb_typeof(result.detail#>'{resolution,reason}') = 'string'
          AND (
            (result.detail#>>'{resolution,type}' = 'mark_pass'
              AND result.outcome = 'passed' AND result.score = 100)
            OR (result.detail#>>'{resolution,type}' = 'mark_fail'
              AND result.outcome = 'failed' AND result.score = 0)
            OR (result.detail#>>'{resolution,type}' = 'needs_document'
              AND result.outcome = 'not_applicable' AND result.score IS NULL)
            OR (result.detail#>>'{resolution,type}' IN ('mark_level', 'accept_majority')
              AND result.outcome = 'passed' AND result.score BETWEEN 0 AND 100)
            OR (result.detail#>>'{resolution,type}' = 'accept_majority'
              AND result.outcome = 'failed' AND result.score = 0)
          )
    """)
    )
    # Python's original input trimming also covers Unicode whitespace. Do not
    # let the database locale change which stored reasons count as empty.
    ids = [row.id for row in candidates if row.reason.strip()]
    if ids:
        op.get_bind().execute(
            sa.text("UPDATE check_result SET judgment_revision = 0 WHERE id IN :ids").bindparams(
                sa.bindparam("ids", expanding=True)
            ),
            {"ids": ids},
        )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_check_result_judgment_revision_nonnegative"), "check_result", type_="check"
    )
    op.drop_column("check_result", "judgment_revision")
