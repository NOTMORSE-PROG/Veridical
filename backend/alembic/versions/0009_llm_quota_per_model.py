"""Make the LLM quota counter per-MODEL, not per-day-globally (V-049).

Gemini's free tier meters `GenerateRequestsPerDayPerProjectPerModel-FreeTier`
— the 429's `quotaDimensions` name the model — so one key holds an
independent daily allowance per model id. A single global per-day counter
could only ever describe one model's island; the queue now spends a pool of
them and needs a counter per island.

Existing rows are backfilled with a historical marker rather than a model id:
they were written before the pool existed and their true serving model is not
recoverable from the row itself (the audit_log payload has it, per call).

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-26 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Not a model id: an honest label for counts recorded before per-model
# metering existed, so they can never be mistaken for a real island's usage.
_LEGACY_MODEL = "legacy-pre-pool"


def upgrade() -> None:
    op.add_column(
        "llm_quota_counter",
        sa.Column("model", sa.String(length=100), nullable=False, server_default=_LEGACY_MODEL),
    )
    op.alter_column("llm_quota_counter", "model", server_default=None)
    op.drop_constraint("uq_llm_quota_counter_quota_day", "llm_quota_counter", type_="unique")
    op.create_unique_constraint(
        "uq_llm_quota_counter_day_model", "llm_quota_counter", ["quota_day", "model"]
    )


def downgrade() -> None:
    # Collapsing back to one row per day would violate the old unique
    # constraint as soon as two models were used on the same day, so the
    # surplus islands are dropped (the audit_log keeps the real history).
    op.execute(
        """
        DELETE FROM llm_quota_counter a
        USING llm_quota_counter b
        WHERE a.quota_day = b.quota_day AND a.id > b.id
        """
    )
    op.drop_constraint("uq_llm_quota_counter_day_model", "llm_quota_counter", type_="unique")
    op.create_unique_constraint(
        "uq_llm_quota_counter_quota_day", "llm_quota_counter", ["quota_day"]
    )
    op.drop_column("llm_quota_counter", "model")
