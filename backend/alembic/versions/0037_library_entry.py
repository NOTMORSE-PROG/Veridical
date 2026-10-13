"""V-077 (D-028): the Library as its own entity, and the manuscript origin.

Adds `manuscript.origin` (every existing row is a dashboard check upload) and
the `library_entry` table, then carries the pre-D-028 corpus over: every
non-purged manuscript that already has F7 comparison data becomes an active
`legacy_check_upload` entry with a snapshot of its group's title, team name,
members and program. A manuscript with no comparison data (never finished a
check, failed, or purged) was never a comparison source and is not carried.

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-12 00:00:00.000000

"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "manuscript",
        sa.Column("origin", sa.String(length=32), server_default="check_upload", nullable=False),
    )
    op.create_index("ix_manuscript_origin", "manuscript", ["origin"])

    op.create_table(
        "library_entry",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("manuscript_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("team_name", sa.String(length=200), nullable=True),
        sa.Column(
            "authors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("program_id", sa.BigInteger(), nullable=True),
        sa.Column("school_year", sa.String(length=20), nullable=True),
        sa.Column(
            "proposed_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("possible_duplicate_of_id", sa.BigInteger(), nullable=True),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removal_reason", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(
            ["manuscript_id"],
            ["manuscript.id"],
            name="fk_library_entry_manuscript_id_manuscript",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["program_id"], ["program.id"], name="fk_library_entry_program_id_program"
        ),
        sa.ForeignKeyConstraint(
            ["possible_duplicate_of_id"],
            ["library_entry.id"],
            name="fk_library_entry_possible_duplicate_of_id_library_entry",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_library_entry"),
        sa.UniqueConstraint("manuscript_id", name="uq_library_entry_manuscript_id"),
    )
    op.create_index("ix_library_entry_status", "library_entry", ["status"])

    op.execute(
        """
        INSERT INTO library_entry
            (manuscript_id, source, status, title, team_name, authors, program_id, added_at)
        SELECT
            m.id,
            'legacy_check_upload',
            'active',
            g.title,
            CASE WHEN lower(trim(m.group_label)) = 'ungrouped' THEN NULL ELSE m.group_label END,
            COALESCE(
                (SELECT jsonb_agg(gm.name ORDER BY gm.id)
                   FROM group_member gm WHERE gm.group_id = m.group_id),
                '[]'::jsonb
            ),
            g.program_id,
            COALESCE(
                (SELECT a.created_at FROM manuscript_archive a WHERE a.manuscript_id = m.id),
                m.created_at
            )
        FROM manuscript m
        LEFT JOIN manuscript_group g ON g.id = m.group_id
        WHERE m.purged_at IS NULL
          AND (
            EXISTS (SELECT 1 FROM manuscript_archive a WHERE a.manuscript_id = m.id)
            OR EXISTS (SELECT 1 FROM manuscript_chapter_archive c WHERE c.manuscript_id = m.id)
            OR EXISTS (SELECT 1 FROM manuscript_passage_archive p WHERE p.manuscript_id = m.id)
          )
        """
    )
    # AC7: say what was and was not carried, in the migration log.
    bind = op.get_bind()
    carried = bind.execute(sa.text("SELECT count(*) FROM library_entry")).scalar()
    not_carried = bind.execute(
        sa.text(
            "SELECT count(*) FROM manuscript m"
            " WHERE NOT EXISTS (SELECT 1 FROM library_entry e WHERE e.manuscript_id = m.id)"
        )
    ).scalar()
    logging.getLogger("alembic.runtime.migration").info(
        "V-077: carried %s manuscript(s) with comparison data into the Library; "
        "%s manuscript(s) had none (never finished a check, failed, or purged) and "
        "were not carried.",
        carried,
        not_carried,
    )


def downgrade() -> None:
    op.drop_index("ix_library_entry_status", table_name="library_entry")
    op.drop_table("library_entry")
    op.drop_index("ix_manuscript_origin", table_name="manuscript")
    op.drop_column("manuscript", "origin")
