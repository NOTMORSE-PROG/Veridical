"""Durable source-upload attempts and terminal receipts (V-081).

Revision ID: 0044
Revises: 0043
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "manuscript", sa.Column("native_compute_active_until", sa.DateTime(timezone=True))
    )
    op.create_table(
        "upload_attempt",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("instructor_id", sa.BigInteger(), sa.ForeignKey("instructor.id"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("source_bytes", sa.BigInteger(), nullable=False),
        sa.Column("staging_key", sa.String(1024), nullable=False, unique=True),
        sa.Column("source_etag", sa.String(255)),
        sa.Column("upload_transport", sa.String(8), server_default="r2", nullable=False),
        sa.Column("staging_deleted_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("manuscript_id", sa.BigInteger(), sa.ForeignKey("manuscript.id")),
        sa.Column("lease_id", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("response", postgresql.JSONB()),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_upload_attempt_instructor_id", "upload_attempt", ["instructor_id"])
    op.create_index("ix_upload_attempt_status", "upload_attempt", ["status"])
    op.create_index("ix_upload_attempt_owner_status", "upload_attempt", ["instructor_id", "status"])


def downgrade():
    op.drop_table("upload_attempt")
    op.drop_column("manuscript", "native_compute_active_until")
