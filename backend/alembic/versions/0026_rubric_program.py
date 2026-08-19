"""V-064: `program` on the rubric FAMILY (AC1) -- an instructor's rubric
for CS and rubric for IT are genuinely different documents, but nothing
in the schema could say which is which.

Denormalized across every version row of a family (this schema has no
separate "family" table -- `rubric_family_id` is just a UUID shared
across `Rubric` rows, and every other family-level fact, e.g. `title`,
already works this way), kept in sync by
`app/rubric/service.py::set_rubric_family_program` writing to every
sibling row at once, never just the latest version -- so an OLDER version
of a family (still readable, per this ticket's "history is immutable"
convention, V-013) never silently disagrees with its own family's current
program.

NULL is the default and stays fully eligible for everything (AC3) --
never guessed from anything (title text, criteria content); an
instructor sets it explicitly on the rubric management screen (4m), or
never sets it and nothing about program-eligibility changes for them.

No backfill needed: every existing rubric row simply starts at NULL,
which is exactly the "not set, eligible for everything" state this
ticket's own backward-compatibility requirement (AC3) asks for.

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("rubric", sa.Column("program_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_rubric_program_id_program", "rubric", "program", ["program_id"], ["id"]
    )
    op.create_index("ix_rubric_program_id", "rubric", ["program_id"])


def downgrade() -> None:
    op.drop_index("ix_rubric_program_id", table_name="rubric")
    op.drop_constraint("fk_rubric_program_id_program", "rubric", type_="foreignkey")
    op.drop_column("rubric", "program_id")
