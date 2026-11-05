"""Таблицы предметной области. Историю миграций задают отдельные ревизии Alembic."""

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = sa.MetaData()


def entity_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("edited_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
    ]


users = sa.Table(
    "users",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("login", sa.Text, nullable=False, unique=True),
)
conferences = sa.Table(
    "conferences",
    metadata,
    *entity_columns(),
    sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("title", sa.Text),
    sa.Column("start_date", sa.Date),
    sa.Column("end_date", sa.Date),
    sa.Column("place", sa.Text),
    sa.Column("organizer", sa.Text),
)
sections = sa.Table(
    "sections",
    metadata,
    *entity_columns(),
    sa.Column(
        "conference_id",
        UUID(as_uuid=True),
        sa.ForeignKey("conferences.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("date", sa.Date),
    sa.Column("hall", sa.Text),
    sa.Column("order", sa.Integer, nullable=False),
)
people = sa.Table(
    "people",
    metadata,
    *entity_columns(),
    sa.Column("full_name", sa.Text, nullable=False),
    sa.Column("affiliation", sa.Text),
)
talks = sa.Table(
    "talks",
    metadata,
    *entity_columns(),
    sa.Column(
        "section_id",
        UUID(as_uuid=True),
        sa.ForeignKey("sections.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("order", sa.Integer, nullable=False),
    sa.Column("title", sa.Text),
    sa.Column("date", sa.Date),
    sa.Column("talk_type", sa.Text),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("match_confidence", sa.Float),
    sa.Column("import_folder", sa.Text),
    sa.UniqueConstraint("section_id", "import_folder", name="talks_import_folder_key"),
)
participants = sa.Table(
    "talk_participants",
    metadata,
    sa.Column(
        "talk_id",
        UUID(as_uuid=True),
        sa.ForeignKey("talks.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("person_id", UUID(as_uuid=True), sa.ForeignKey("people.id"), primary_key=True),
    sa.Column("role", sa.Text, primary_key=True),
    sa.Column("order", sa.Integer, nullable=False),
)
reports = sa.Table(
    "reports",
    metadata,
    *entity_columns(),
    sa.Column("author_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
    sa.Column(
        "conference_id",
        UUID(as_uuid=True),
        sa.ForeignKey("conferences.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("template_version", sa.Text, nullable=False),
    sa.Column("days", JSONB, nullable=False),
)
report_talks = sa.Table(
    "report_talks",
    metadata,
    sa.Column(
        "report_id",
        UUID(as_uuid=True),
        sa.ForeignKey("reports.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column(
        "talk_id",
        UUID(as_uuid=True),
        sa.ForeignKey("talks.id", deferrable=True, initially="DEFERRED"),
        primary_key=True,
    ),
    sa.Column("position", sa.Integer, nullable=False),
)
report_sections = sa.Table(
    "report_sections",
    metadata,
    sa.Column(
        "report_id",
        UUID(as_uuid=True),
        sa.ForeignKey("reports.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column(
        "section_id",
        UUID(as_uuid=True),
        sa.ForeignKey("sections.id", deferrable=True, initially="DEFERRED"),
        primary_key=True,
    ),
    sa.Column("position", sa.Integer, nullable=False),
)
report_versions = sa.Table(
    "report_versions",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "report_id",
        UUID(as_uuid=True),
        sa.ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("number", sa.Integer, nullable=False),
    sa.Column("input", JSONB, nullable=False),
    sa.Column("variables", JSONB, nullable=False),
    sa.Column("files", JSONB, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
)

source_files = sa.Table(
    "source_files",
    metadata,
    *entity_columns(),
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
    sa.UniqueConstraint("conference_id", "relative_path", name="source_files_conference_path_key"),
)

file_bindings = sa.Table(
    "file_bindings",
    metadata,
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

sa.Index("sections_conference", sections.c.conference_id)
sa.Index("talks_section", talks.c.section_id)
sa.Index("reports_conference", reports.c.conference_id)
sa.Index("file_bindings_talk", file_bindings.c.talk_id)
participants.append_constraint(
    sa.UniqueConstraint(participants.c.talk_id, participants.c.role, participants.c.order)
)
report_versions.append_constraint(
    sa.UniqueConstraint(report_versions.c.report_id, report_versions.c.number)
)
