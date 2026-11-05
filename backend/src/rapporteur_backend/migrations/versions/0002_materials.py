"""Оригиналы и подтверждённые привязки материалов."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0002_materials"
down_revision = "0001_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("talks", sa.Column("import_folder", sa.Text))
    op.create_unique_constraint("talks_import_folder_key", "talks", ["section_id", "import_folder"])
    op.create_table(
        "source_files",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("edited_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "conference_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conferences.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("original_name", sa.Text, nullable=False),
        sa.Column("relative_path", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("media_type", sa.Text, nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("duration_ms", sa.BigInteger),
        sa.Column("encoding", sa.Text),
        sa.Column("title_candidates", JSONB),
        sa.Column("warning", sa.Text),
        sa.UniqueConstraint("conference_id", "sha256", name="source_files_conference_sha_key"),
        sa.UniqueConstraint(
            "conference_id", "relative_path", name="source_files_conference_path_key"
        ),
    )
    op.create_table(
        "file_bindings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_file_id",
            UUID(as_uuid=True),
            sa.ForeignKey("source_files.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        sa.Column("talk_id", UUID(as_uuid=True), sa.ForeignKey("talks.id", ondelete="SET NULL")),
        sa.Column("confirmed", sa.Boolean, nullable=False),
        sa.Column("confidence", sa.Float),
    )
    op.create_index("file_bindings_talk", "file_bindings", ["talk_id"])


def downgrade() -> None:
    op.drop_table("file_bindings")
    op.drop_table("source_files")
    op.drop_constraint("talks_import_folder_key", "talks", type_="unique")
    op.drop_column("talks", "import_folder")
