"""Предметные таблицы MVP и встроенный пользователь local."""

from pathlib import Path

from alembic import op

revision = "0001_domain"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    script = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(script)


def downgrade() -> None:
    for name in [
        "report_versions",
        "report_sections",
        "report_talks",
        "reports",
        "talk_participants",
        "talks",
        "people",
        "sections",
        "conferences",
        "users",
    ]:
        op.drop_table(name)
