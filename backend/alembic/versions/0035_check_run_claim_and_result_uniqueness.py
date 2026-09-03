"""BUG-144: check_run claim + check_result uniqueness backstop.

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "check_run",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # BUG-144: deduplicate existing doubled rows BEFORE the unique index
    # below, or its creation fails outright against production's own
    # already-doubled check_result rows (this ticket's own measured
    # evidence -- every reuse finding appears exactly twice). Keeps
    # whichever twin an instructor actually engaged with (a persisted
    # resolution, or an overridden flag) when exactly one twin shows
    # engagement, falling back to the earliest (lowest id) row when
    # NEITHER does -- a doubled result was always byte-identical WORK, but
    # the two rows could still carry different instructor-decision state
    # if only one was ever acted on, and that state must never be the
    # thing silently discarded.
    #
    # `backend-critic` finding (BUG-144 review): the genuinely AMBIGUOUS
    # case -- BOTH twins independently engaged (e.g. resolved to different
    # verdicts) -- has no safe automatic answer, and this runs against
    # real production data with no undo. Rather than guess, a group with
    # more than one engaged twin is left ENTIRELY UNTOUCHED, which makes
    # the unique index creation below fail loudly (a clear "duplicate key"
    # error naming the exact check_run_id) instead of silently deleting a
    # real instructor decision. Fail-safe over fail-silent.
    op.execute(
        """
        WITH engagement AS (
            -- `backend-critic` finding (BUG-144 follow-up review, empirically
            -- reproduced): `detail ? 'resolution'` is SQL NULL when `detail`
            -- itself is NULL (the column is nullable; no current write path
            -- leaves it NULL, but nothing at the DB level forbids it, and this
            -- runs against production data of unknown provenance). Under
            -- `ORDER BY ... DESC` Postgres sorts NULL FIRST -- the opposite of
            -- `ASC`'s default -- so an unengaged NULL-detail row would rank
            -- ABOVE a genuinely engaged one and the ENGAGED twin would be the
            -- one silently deleted, defeating this whole migration's purpose.
            -- COALESCE makes `engaged` always a real boolean, never NULL.
            SELECT check_run_id, criterion_id, id,
                   COALESCE(detail ? 'resolution', false) AS engaged
            FROM check_result
            WHERE criterion_id IS NOT NULL
        ),
        groups AS (
            SELECT check_run_id, criterion_id, COUNT(*) FILTER (WHERE engaged) AS n_engaged
            FROM engagement
            GROUP BY check_run_id, criterion_id
            HAVING COUNT(*) > 1
        ),
        ranked AS (
            SELECT e.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY e.check_run_id, e.criterion_id
                       ORDER BY e.engaged DESC, e.id ASC
                   ) AS rn
            FROM engagement e
            JOIN groups g ON g.check_run_id = e.check_run_id AND g.criterion_id = e.criterion_id
            WHERE g.n_engaged <= 1
        )
        DELETE FROM check_result WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
        """
    )
    op.execute(
        """
        WITH engagement AS (
            SELECT cr.check_run_id, cr.kind, cr.id,
                   EXISTS (
                       SELECT 1 FROM flag f
                       WHERE f.check_result_id = cr.id AND f.overridden = true
                   ) AS engaged
            FROM check_result cr
            WHERE cr.criterion_id IS NULL
        ),
        groups AS (
            SELECT check_run_id, kind, COUNT(*) FILTER (WHERE engaged) AS n_engaged
            FROM engagement
            GROUP BY check_run_id, kind
            HAVING COUNT(*) > 1
        ),
        ranked AS (
            SELECT e.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY e.check_run_id, e.kind
                       ORDER BY e.engaged DESC, e.id ASC
                   ) AS rn
            FROM engagement e
            JOIN groups g ON g.check_run_id = e.check_run_id AND g.kind = e.kind
            WHERE g.n_engaged <= 1
        )
        DELETE FROM check_result WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
        """
    )

    op.create_index(
        "uq_check_result_run_criterion",
        "check_result",
        ["check_run_id", "criterion_id"],
        unique=True,
        postgresql_where=sa.text("criterion_id IS NOT NULL"),
    )
    op.create_index(
        "uq_check_result_run_kind",
        "check_result",
        ["check_run_id", "kind"],
        unique=True,
        postgresql_where=sa.text("criterion_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_check_result_run_kind", table_name="check_result")
    op.drop_index("uq_check_result_run_criterion", table_name="check_result")
    op.drop_column("check_run", "claimed_at")
