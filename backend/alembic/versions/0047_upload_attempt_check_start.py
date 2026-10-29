"""Background uploads can start their check on the server (V-090).

Revision ID: 0047
Revises: 0046
"""

import sqlalchemy as sa

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "upload_attempt",
        sa.Column(
            "start_rubric_id", sa.BigInteger(), sa.ForeignKey("rubric.id", ondelete="SET NULL")
        ),
    )
    op.add_column(
        "upload_attempt",
        sa.Column(
            "check_run_id", sa.BigInteger(), sa.ForeignKey("check_run.id", ondelete="SET NULL")
        ),
    )
    op.add_column("upload_attempt", sa.Column("check_start_error", sa.Text()))


def downgrade():
    op.drop_column("upload_attempt", "check_start_error")
    op.drop_column("upload_attempt", "check_run_id")
    op.drop_column("upload_attempt", "start_rubric_id")
