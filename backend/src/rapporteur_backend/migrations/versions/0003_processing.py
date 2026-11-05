"""Сохраняет запрос на обработку конференции между перезапусками."""

import sqlalchemy as sa
from alembic import op

revision = "0003_processing"
down_revision = "0002_materials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conferences",
        sa.Column("processing_started", sa.Boolean, nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("conferences", "processing_started")
