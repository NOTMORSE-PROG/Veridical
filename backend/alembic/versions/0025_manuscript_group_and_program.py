r"""V-062: a real `Group` entity + `program` reference table, replacing the
free-text `Manuscript.group_label` as the identity mechanism.

`program` is a table, not a Python enum (ground rule 7 / ticket's own
DECIDED 2026-08-16): CS and IT are TIP's programs today; another school
(or TIP next year) may need a different list, and adding one must be a
data change, never a redeploy. The two rows below are a ONE-TIME seed of
the values known when this migration was written -- a historical fact
about what shipped, hardcoded the same way migration 0009's
`_LEGACY_MODEL` constant is, NOT read from live `Settings` at migration-run
time (BUG-042's lesson: a migration must never read dynamic settings for
something it's supposed to lock in, or environments with a stray env var
diverge silently). Programs added later go through
`scripts/seed_programs.py` (reads `Settings.default_programs`, idempotent,
safe in any environment) or a normal INSERT, never a new migration.

The table is named `manuscript_group`, not `group` (a reserved SQL
keyword) -- matches the existing `manuscript_archive` /
`manuscript_chapter_archive` naming.

Backfill (BUG-043 fixed first, per this ticket's own hard blocker: the FK
below would otherwise inherit the endpoint's old silently-dropped-label
path): one `Group` per distinct (instructor_id, normalized group_label)
pair already in `manuscript`, `program` left NULL ("Not set", never
guessed -- same convention as `ingest_failure_reason`). The normalization
(`lower(trim(regexp_replace(x, '\s+', ' ', 'g')))`) must stay byte-for-byte
in sync with `app/groups/service.py::normalize_group_name` — see that
function's own docstring for why. COLLAPSE THEN TRIM, not the other order
(`backend-critic`, V-062 review, live-reproduced): trimming first only
strips a boundary ASCII space (Postgres `trim()`'s default), so a
boundary TAB survived untouched, then got silently converted into a
boundary SPACE by the collapse step -- two real uploads of visually
identical text landed in different groups. Collapsing first turns any
boundary whitespace into a single space before the trim ever runs, so
the trim always has exactly one space to remove, regardless of what kind
of whitespace was actually typed.

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NORMALIZE_SQL = r"lower(trim(regexp_replace(group_label, '\s+', ' ', 'g')))"


def upgrade() -> None:
    op.create_table(
        "program",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_program"),
        sa.UniqueConstraint("name", name="uq_program_name"),
    )
    # One-time seed of the values known when this migration was written --
    # see module docstring for why this is hardcoded, not settings-driven.
    op.execute("INSERT INTO program (name) VALUES ('CS'), ('IT')")

    op.create_table(
        "manuscript_group",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("instructor_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("name_normalized", sa.String(length=200), nullable=False),
        sa.Column("program_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_manuscript_group"),
        sa.ForeignKeyConstraint(
            ["instructor_id"],
            ["instructor.id"],
            name="fk_manuscript_group_instructor_id_instructor",
        ),
        sa.ForeignKeyConstraint(
            ["program_id"], ["program.id"], name="fk_manuscript_group_program_id_program"
        ),
        sa.UniqueConstraint(
            "instructor_id", "name_normalized", name="uq_manuscript_group_instructor_name"
        ),
    )
    op.create_index("ix_manuscript_group_instructor_id", "manuscript_group", ["instructor_id"])

    op.add_column("manuscript", sa.Column("group_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_manuscript_group_id_manuscript_group",
        "manuscript",
        "manuscript_group",
        ["group_id"],
        ["id"],
    )
    op.create_index("ix_manuscript_group_id", "manuscript", ["group_id"])

    # Backfill: one Group per distinct (instructor_id, normalized label)
    # already present, program left NULL. `(array_agg(... ORDER BY id))[1]`
    # picks the EARLIEST submission's exact casing as the group's display
    # name -- the same "first spelling wins" rule
    # `resolve_or_create_group` uses for new uploads, so a pre-existing
    # group's name doesn't retroactively disagree with how a NEW upload
    # under the same label would have been named.
    op.execute(
        f"""
        INSERT INTO manuscript_group (instructor_id, name, name_normalized, program_id, created_at)
        SELECT instructor_id,
               (array_agg(group_label ORDER BY id))[1],
               {_NORMALIZE_SQL},
               NULL,
               now()
        FROM manuscript
        GROUP BY instructor_id, {_NORMALIZE_SQL}
        """
    )
    op.execute(
        f"""
        UPDATE manuscript m
        SET group_id = g.id
        FROM manuscript_group g
        WHERE g.instructor_id = m.instructor_id
          AND g.name_normalized = {_NORMALIZE_SQL.replace("group_label", "m.group_label")}
        """
    )


def downgrade() -> None:
    op.drop_index("ix_manuscript_group_id", table_name="manuscript")
    op.drop_constraint("fk_manuscript_group_id_manuscript_group", "manuscript", type_="foreignkey")
    op.drop_column("manuscript", "group_id")
    op.drop_index("ix_manuscript_group_instructor_id", table_name="manuscript_group")
    op.drop_table("manuscript_group")
    op.drop_table("program")
