"""Browser multipart transfers for large manuscripts (BUG-527).

Revision ID: 0046
Revises: 0045
"""

import sqlalchemy as sa

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("upload_attempt", sa.Column("multipart_upload_id", sa.String(1024)))
    op.add_column("upload_attempt", sa.Column("multipart_part_bytes", sa.BigInteger()))


def downgrade():
    op.drop_column("upload_attempt", "multipart_part_bytes")
    op.drop_column("upload_attempt", "multipart_upload_id")
