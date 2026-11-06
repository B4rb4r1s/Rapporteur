"""Сборка справки задачей report и выгрузка файлов её версий (spec §6.9, ADR-013).

API собирает снимок ReportRequest из БД, кладёт его на том и ставит задачу в очередь.
Версию справки из результата задачи создаёт перенос результатов планировщика (results.py).
"""

from urllib.parse import quote
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Request, Response
from sqlalchemy import Connection

from rapporteur_backend import store
from rapporteur_backend import tables as t
from rapporteur_backend.errors import API_ERRORS, ApiRequestError
from rapporteur_backend.jobs import require_queue
from rapporteur_backend.routes import engine
from rapporteur_backend.schemas import ReportBuild
from rapporteur_backend.summary_routes import current_summaries, mode, storage
from rapporteur_contracts import (
    ArtifactRef,
    Conference,
    JobAccepted,
    Person,
    Report,
    ReportRequest,
    Section,
    StageInputs,
    Summary,
    Talk,
)
from rapporteur_contracts.enums import ApiErrorCode, ReportFormat, ResourceKind, TalkStatus
from rapporteur_contracts.reports import ReportFile
from rapporteur_pipeline.report.stage import REQUEST_INPUT, ReportConfig
from rapporteur_pipeline.stage import config_hash
from rapporteur_pipeline.storage import blob_key, sha256_hex
from rapporteur_workers.queue import Queue

router = APIRouter(prefix="/api/v1", responses=API_ERRORS)
_MEDIA = {
    ReportFormat.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ReportFormat.MARKDOWN: "text/markdown; charset=utf-8",
    ReportFormat.PDF: "application/pdf",
}
# Сборку ждёт человек: она идёт раньше пакетных задач обработки.
_PRIORITY = 10


def report_request(conn: Connection, report_id: UUID, data: ReportBuild) -> ReportRequest:
    report = store.encode(conn, t.reports, store.require(conn, t.reports, report_id))
    assert isinstance(report, Report)
    conference = store.encode(
        conn, t.conferences, store.require(conn, t.conferences, report.conference_id)
    )
    assert isinstance(conference, Conference)
    sections = [
        store.encode(conn, t.sections, row)
        for row in conn.execute(
            sa.select(t.sections).where(t.sections.c.conference_id == conference.id)
        ).mappings()
    ]
    talks = [
        store.encode(conn, t.talks, row)
        for row in conn.execute(
            sa.select(t.talks).join(t.sections).where(t.sections.c.conference_id == conference.id)
        ).mappings()
    ]
    person_ids = {
        p.person_id for talk in talks if isinstance(talk, Talk) for p in talk.participants
    }
    people = [
        store.encode(conn, t.people, row)
        for row in conn.execute(sa.select(t.people).where(t.people.c.id.in_(person_ids))).mappings()
    ]
    summaries = [
        Summary.model_validate(row["summary"])
        for row in current_summaries(conn, conference.id).values()
    ]
    request = ReportRequest(
        report=report,
        conference=conference,
        sections=[item for item in sections if isinstance(item, Section)],
        talks=[item for item in talks if isinstance(item, Talk)],
        people=[item for item in people if isinstance(item, Person)],
        summaries=summaries,
        unreviewed_confirmed=data.unreviewed_confirmed,
    )
    check_request(request)
    return request


def check_request(request: ReportRequest) -> None:
    """Частые причины отказа сборки проверяются до постановки задачи: человек сразу видит,
    что исправить. Остальное проверяет этап report (код report_invalid)."""
    selected = set(request.report.selection.talk_ids)
    talks = {talk.id: talk for talk in request.talks if talk.id in selected}
    summarized = {s.talk_id for s in request.summaries if s.talk_id in selected and s.sentences}
    if not summarized:
        raise ApiRequestError(
            ApiErrorCode.VALIDATION_ERROR, "Ни у одного выбранного доклада нет готовой сводки"
        )
    unreviewed = [
        talk_id for talk_id in summarized if talks[talk_id].status is not TalkStatus.REVIEWED
    ]
    if unreviewed and not request.unreviewed_confirmed:
        raise ApiRequestError(
            ApiErrorCode.VALIDATION_ERROR,
            "В справке есть непроверенные доклады: подтвердите сборку без проверки",
            details={"unreviewed_talk_ids": [str(talk_id) for talk_id in sorted(unreviewed)]},
        )
    sections = {section.id: section for section in request.sections}
    dated = any(
        (talks[talk_id].date or sections[talks[talk_id].section_id].date) for talk_id in summarized
    )
    if not (request.report.selection.days or dated or request.conference.start_date):
        raise ApiRequestError(
            ApiErrorCode.VALIDATION_ERROR,
            "Укажите даты конференции: без них в справке нет периода проведения",
        )


@router.post("/reports/{report_id}/build", response_model=JobAccepted, status_code=202)
def build_report(request: Request, report_id: UUID, data: ReportBuild) -> JobAccepted:
    database = engine(request)
    with database.begin() as conn:
        require_queue(conn)
        snapshot = report_request(conn, report_id, data)
    payload = snapshot.model_dump_json().encode()
    sha = sha256_hex(payload)
    storage(request).put(blob_key(sha), payload)
    config = ReportConfig()
    inputs = StageInputs(
        conference_id=snapshot.conference.id,
        talk_id=None,
        artifacts=[ArtifactRef(name=REQUEST_INPUT, storage_key=blob_key(sha), sha256=sha)],
        config_hash=config_hash(config),
    )
    queue = Queue(database.url.set(drivername="postgresql").render_as_string(hide_password=False))
    # Тот же снимок — та же задача: повторное нажатие не создаёт новую версию справки.
    job_id = queue.enqueue(
        key=f"report:{report_id}:{mode(request)}:{sha}",
        stage="report",
        resource=ResourceKind.CPU,
        mode=mode(request),
        inputs=inputs,
        config=config.model_dump(mode="json"),
        priority=_PRIORITY,
    )
    return JobAccepted(job_id=job_id, status_url=f"/api/v1/jobs/{job_id}")


@router.get(
    "/report-versions/{version_id}/files/{report_format}",
    response_class=Response,
    responses={200: {"content": {media: {} for media in _MEDIA.values()}}},
)
def report_file(request: Request, version_id: UUID, report_format: ReportFormat) -> Response:
    with engine(request).begin() as conn:
        row = (
            conn.execute(sa.select(t.report_versions).where(t.report_versions.c.id == version_id))
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise ApiRequestError(ApiErrorCode.NOT_FOUND, "Версия справки не найдена")
    files = [ReportFile.model_validate(item) for item in row["files"]]
    file = next((item for item in files if item.format is report_format), None)
    if file is None:
        raise ApiRequestError(ApiErrorCode.NOT_FOUND, "В этой версии нет такого формата")
    data = storage(request).get(file.storage_key)
    if sha256_hex(data) != file.sha256:
        raise ApiRequestError(ApiErrorCode.INTERNAL_ERROR, "Файл справки повреждён")
    extension = {ReportFormat.DOCX: "docx", ReportFormat.MARKDOWN: "md", ReportFormat.PDF: "pdf"}
    name = f"Справка, версия {row['number']}.{extension[report_format]}"
    return Response(
        data,
        media_type=_MEDIA[report_format],
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )
