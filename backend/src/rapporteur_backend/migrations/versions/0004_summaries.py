"""Версии сводок докладов вместе с пакетом фрагментов, по которому они собраны (ADR-013)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0004_summaries"
down_revision = "0003_processing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "summaries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "talk_id",
            UUID(as_uuid=True),
            sa.ForeignKey("talks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("summary", JSONB, nullable=False),
        sa.Column("pack", JSONB, nullable=False),
        sa.Column("job_id", UUID(as_uuid=True), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.UniqueConstraint("talk_id", "number", name="summaries_talk_number_key"),
    )


def downgrade() -> None:
    op.drop_table("summaries")
