"""Сборка справки из текущих данных конференции и её версии (spec §6.9, ADR-006).

Справка собирается по запросу пользователя прямо в запросе: DOCX по бланку и PDF через
LibreOffice занимают секунды. Каждая сборка — новая версия со снимком входа.
"""

import datetime as dt
import json
import sqlite3
import tempfile
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from rapporteur import db
from rapporteur.contracts import ReportSelection, ReportVersion, Summary
from rapporteur.contracts.enums import TalkStatus
from rapporteur.contracts.reports import ReportFile
from rapporteur.errors import NotFoundError, UserError
from rapporteur.materials import require_conference
from rapporteur.report.assemble import ReportError, ReportSources
from rapporteur.report.build import build_report
from rapporteur.storage import Storage


class ReportBuild(BaseModel):
    """Непроверенные доклады входят только с явным подтверждением (ADR-006). Без списка
    докладов в справку идут все доклады конференции."""

    model_config = ConfigDict(extra="forbid")

    unreviewed_confirmed: bool = False
    talk_ids: list[UUID] | None = None


class ReportVersionView(ReportVersion):
    warnings: list[str]


def _sources(conn: sqlite3.Connection, conference_id: str) -> ReportSources:
    conference = db.conference(require_conference(conn, conference_id))
    sections = conn.execute(
        "SELECT * FROM sections WHERE conference_id = ?", (conference_id,)
    ).fetchall()
    return ReportSources(
        conference=conference,
        sections=[db.section(row) for row in sections],
        talks=[db.talk(row) for row in db.conference_talks(conn, conference_id)],
        summaries=[
            Summary.model_validate_json(row["summary"])
            for row in db.current_summaries(conn, conference_id).values()
        ],
    )


def _check(sources: ReportSources, selection: ReportSelection, unreviewed_confirmed: bool) -> None:
    """Частые причины отказа проверяются заранее, чтобы человек сразу видел, что исправить."""
    talks = {talk.id: talk for talk in sources.talks if talk.id in selection.talk_ids}
    summarized = {s.talk_id for s in sources.summaries if s.talk_id in talks and s.sentences}
    if not summarized:
        raise UserError("Ни у одного выбранного доклада нет готовой сводки")
    unreviewed = [
        talk_id for talk_id in summarized if talks[talk_id].status is not TalkStatus.REVIEWED
    ]
    if unreviewed and not unreviewed_confirmed:
        raise UserError("В справке есть непроверенные доклады: подтвердите сборку без проверки")
    dated = any(sources.talk_day(talks[talk_id]) for talk_id in summarized)
    if not (selection.days or dated or sources.conference.start_date):
        raise UserError("Укажите даты конференции: без них в справке нет периода проведения")


def build(
    database: db.Database, storage: Storage, conference_id: str, data: ReportBuild
) -> ReportVersionView:
    with database.tx() as conn:
        sources = _sources(conn, conference_id)
    ordered = [talk.id for talk in sources.ordered_talks()]
    talk_ids = ordered if data.talk_ids is None else data.talk_ids
    if not talk_ids:
        raise UserError("В конференции нет докладов")
    if set(talk_ids) - set(ordered):
        raise UserError("В выборке есть доклад другой конференции")
    selection = ReportSelection(days=[], section_ids=[], talk_ids=talk_ids)
    _check(sources, selection, data.unreviewed_confirmed)

    version_id = db.new_id()
    try:
        with tempfile.TemporaryDirectory(prefix="report-") as work:
            built = build_report(
                sources,
                selection,
                data.unreviewed_confirmed,
                storage,
                f"reports/{version_id}",
                work=Path(work),
            )
    except ReportError as error:
        raise UserError(f"Справка не собрана: {error}") from error

    created = dt.datetime.now(dt.UTC)
    with database.tx() as conn:
        require_conference(conn, conference_id)
        number = conn.execute(
            "SELECT coalesce(max(number), 0) + 1 FROM report_versions WHERE conference_id = ?",
            (conference_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO report_versions "
            "(id, conference_id, number, input, variables, files, warnings, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                version_id,
                conference_id,
                number,
                built.input.model_dump_json(),
                built.variables.model_dump_json(),
                db.dumps([file.model_dump(mode="json") for file in built.files]),
                db.dumps(built.warnings),
                created.isoformat(),
            ),
        )
        row = db.one(conn, "SELECT * FROM report_versions WHERE id = ?", version_id)
    assert row is not None
    return version(row)


def version(row: db.Row) -> ReportVersionView:
    return ReportVersionView.model_validate(
        {
            "id": row["id"],
            "conference_id": row["conference_id"],
            "number": row["number"],
            "input": json.loads(row["input"]),
            "variables": json.loads(row["variables"]),
            "files": json.loads(row["files"]),
            "warnings": json.loads(row["warnings"]),
            "created_at": row["created_at"],
        }
    )


def versions(conn: sqlite3.Connection, conference_id: str) -> list[ReportVersionView]:
    require_conference(conn, conference_id)
    rows = conn.execute(
        "SELECT * FROM report_versions WHERE conference_id = ? ORDER BY number DESC",
        (conference_id,),
    ).fetchall()
    return [version(row) for row in rows]


def report_file(
    conn: sqlite3.Connection, version_id: str, report_format: str
) -> tuple[int, ReportFile]:
    row = db.one(conn, "SELECT number, files FROM report_versions WHERE id = ?", version_id)
    if row is None:
        raise NotFoundError("Версия справки не найдена")
    files = [ReportFile.model_validate(item) for item in json.loads(row["files"])]
    file = next((item for item in files if item.format.value == report_format), None)
    if file is None:
        raise NotFoundError("В этой версии нет такого формата")
    return row["number"], file
