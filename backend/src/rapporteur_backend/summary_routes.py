"""Ход обработки, сводки докладов, их правка и подтверждение человеком (spec §6.8, ADR-013).

Каждая правка и каждое подтверждение создают новую версию сводки; ETag — её номер. Цитаты
и метки проверяются по пакету фрагментов той версии, из которой выросла правка.
"""

import datetime as dt
from collections import defaultdict
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy import Connection, RowMapping, text

from rapporteur_backend import store
from rapporteur_backend import tables as t
from rapporteur_backend.db import LOCAL_USER_ID
from rapporteur_backend.errors import API_ERRORS, ApiRequestError
from rapporteur_backend.jobs import require_queue
from rapporteur_backend.results import set_talk_status
from rapporteur_backend.routes import Version, engine, etag
from rapporteur_backend.schemas import SummaryEdit
from rapporteur_contracts import (
    SlidesArtifact,
    Summary,
    TalkFragmentPack,
    TalkSummaryBundle,
)
from rapporteur_contracts.enums import (
    ApiErrorCode,
    ClaimStatus,
    JobStatus,
    SummaryStatus,
    TalkStatus,
    can_transition,
)
from rapporteur_pipeline.storage import DirectoryStorage
from rapporteur_workers.queue import Mode

router = APIRouter(prefix="/api/v1", responses=API_ERRORS)
SUMMARY_STAGES = ("pack", "facts", "verify", "summary")


class TalkProgress(BaseModel):
    id: UUID
    section_id: UUID
    section: str
    order: int
    title: str | None
    status: TalkStatus
    # Последняя задача каждого шага сводки этого доклада.
    stages: dict[str, JobStatus]
    error_code: str | None
    summary_number: int | None
    summary_status: SummaryStatus | None


class Progress(BaseModel):
    processing_started: bool
    # Число задач этапа по статусам: этапы файлов и конференции идут без доклада.
    stages: dict[str, dict[JobStatus, int]]
    talks: list[TalkProgress]


class TalkSummaryView(BaseModel):
    summary: Summary
    pack: TalkFragmentPack
    talk_title: str | None
    talk_status: TalkStatus


def mode(request: Request) -> Mode:
    result: Mode = request.app.state.mode
    return result


def storage(request: Request) -> DirectoryStorage:
    return DirectoryStorage(request.app.state.storage_dir)


@router.get("/conferences/{conference_id}/progress", response_model=Progress)
def conference_progress(request: Request, conference_id: UUID) -> Progress:
    with engine(request).begin() as conn:
        conference = store.require(conn, t.conferences, conference_id)
        require_queue(conn)
        jobs = conn.execute(
            text(
                "SELECT stage, status, error_code, inputs->>'talk_id' AS talk_id "
                "FROM rapporteur_queue.jobs WHERE inputs->>'conference_id' = :id AND mode = :mode "
                "ORDER BY created_at"
            ),
            {"id": str(conference_id), "mode": mode(request)},
        ).mappings()
        counts: dict[str, dict[JobStatus, int]] = defaultdict(lambda: defaultdict(int))
        latest: dict[tuple[str, str], RowMapping] = {}
        for job in jobs:
            counts[job["stage"]][JobStatus(job["status"])] += 1
            if job["talk_id"] is not None:
                latest[job["talk_id"], job["stage"]] = job
        talks = conn.execute(
            sa.select(t.talks, t.sections.c.title.label("section"))
            .join(t.sections)
            .where(t.sections.c.conference_id == conference_id)
            .order_by(t.sections.c.order, t.talks.c.order)
        ).mappings()
        current = current_summaries(conn, conference_id)
        result = []
        for talk in talks:
            own = {
                stage: latest[str(talk["id"]), stage]
                for stage in SUMMARY_STAGES
                if (str(talk["id"]), stage) in latest
            }
            failed = [job["error_code"] for job in own.values() if job["status"] == "failed"]
            summary = current.get(talk["id"])
            result.append(
                TalkProgress(
                    id=talk["id"],
                    section_id=talk["section_id"],
                    section=talk["section"],
                    order=talk["order"],
                    title=talk["title"],
                    status=TalkStatus(talk["status"]),
                    stages={stage: JobStatus(job["status"]) for stage, job in own.items()},
                    error_code=failed[0] if failed else None,
                    summary_number=summary["number"] if summary else None,
                    summary_status=SummaryStatus(summary["summary"]["status"]) if summary else None,
                )
            )
    return Progress(
        processing_started=conference["processing_started"],
        stages={stage: dict(by_status) for stage, by_status in counts.items()},
        talks=result,
    )


def current_summaries(conn: Connection, conference_id: UUID) -> dict[UUID, RowMapping]:
    """Текущая версия сводки каждого доклада конференции — с наибольшим номером."""
    summaries = t.summaries
    newest = (
        sa.select(summaries.c.talk_id, sa.func.max(summaries.c.number).label("number"))
        .select_from(
            summaries.join(t.talks, t.talks.c.id == summaries.c.talk_id).join(
                t.sections, t.sections.c.id == t.talks.c.section_id
            )
        )
        .where(t.sections.c.conference_id == conference_id)
        .group_by(summaries.c.talk_id)
        .subquery()
    )
    rows = conn.execute(
        sa.select(summaries).join(
            newest,
            sa.and_(summaries.c.talk_id == newest.c.talk_id, summaries.c.number == newest.c.number),
        )
    ).mappings()
    return {row["talk_id"]: row for row in rows}


def current_summary(conn: Connection, talk_id: UUID) -> RowMapping:
    row = (
        conn.execute(
            sa.select(t.summaries)
            .where(t.summaries.c.talk_id == talk_id)
            .order_by(t.summaries.c.number.desc())
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ApiRequestError(ApiErrorCode.NOT_FOUND, "У доклада ещё нет сводки")
    return row


def view(row: RowMapping, talk: RowMapping, status: TalkStatus | None = None) -> TalkSummaryView:
    return TalkSummaryView(
        summary=Summary.model_validate(row["summary"]),
        pack=TalkFragmentPack.model_validate(row["pack"]),
        talk_title=talk["title"],
        talk_status=status or TalkStatus(talk["status"]),
    )


@router.get("/talks/{talk_id}/summary", response_model=TalkSummaryView)
def get_summary(request: Request, response: Response, talk_id: UUID) -> TalkSummaryView:
    with engine(request).begin() as conn:
        talk = store.require(conn, t.talks, talk_id)
        row = current_summary(conn, talk_id)
    etag(response, row["number"])
    return view(row, talk)


def save_version(
    conn: Connection, talk: RowMapping, row: RowMapping, values: dict[str, Any]
) -> RowMapping:
    """Новая версия сводки поверх текущей; правило сводки и пакета проверяет контракт."""
    number = row["number"] + 1
    entity_id = uuid4()
    try:
        summary = Summary.model_validate(
            {
                **row["summary"],
                **values,
                "id": str(entity_id),
                "version": number,
                "edited_by": str(LOCAL_USER_ID),
                "created_at": dt.datetime.now(dt.UTC).isoformat(),
            }
        )
        TalkSummaryBundle(pack=TalkFragmentPack.model_validate(row["pack"]), summary=summary)
    except ValidationError as error:
        problems = [
            {
                "loc": [str(part) for part in item["loc"]],
                "type": item["type"],
                "message": item["msg"],
            }
            for item in error.errors()
        ]
        raise ApiRequestError(
            ApiErrorCode.VALIDATION_ERROR,
            "Правка не согласуется с утверждениями сводки",
            details={"errors": problems},
        ) from error
    return (
        conn.execute(
            sa.insert(t.summaries)
            .values(
                id=entity_id,
                talk_id=talk["id"],
                number=number,
                summary=summary.model_dump(mode="json"),
                pack=row["pack"],
                job_id=None,
                created_at=summary.created_at,
                created_by=LOCAL_USER_ID,
            )
            .returning(t.summaries)
        )
        .mappings()
        .one()
    )


def locked(conn: Connection, talk_id: UUID, expected: int) -> tuple[RowMapping, RowMapping]:
    talk = store.require(conn, t.talks, talk_id, lock=True)
    row = current_summary(conn, talk_id)
    if row["number"] != expected:
        raise ApiRequestError(
            ApiErrorCode.VERSION_CONFLICT, "Сводку уже изменили: обновите её и повторите правку"
        )
    return talk, row


@router.put("/talks/{talk_id}/summary", response_model=TalkSummaryView)
def edit_summary(
    request: Request, response: Response, talk_id: UUID, version: Version, data: SummaryEdit
) -> TalkSummaryView:
    with engine(request).begin() as conn:
        talk, row = locked(conn, talk_id, version)
        texts = {item.key: item.text for item in data.claims}
        claims = []
        for claim in row["summary"]["claims"]:
            changed = claim["key"] in texts and texts[claim["key"]] != claim["text"]
            if changed and claim["status"] == ClaimStatus.REJECTED.value:
                raise ApiRequestError(
                    ApiErrorCode.VALIDATION_ERROR,
                    "Утверждение без подтверждённой цитаты не правится: его нет в сводке",
                )
            claims.append(
                {**claim, "text": texts[claim["key"]], "status": ClaimStatus.NEEDS_HUMAN.value}
                if changed
                else claim
            )
        unknown = set(texts) - {claim["key"] for claim in claims}
        if unknown:
            raise ApiRequestError(
                ApiErrorCode.VALIDATION_ERROR,
                f"Неизвестные утверждения: {', '.join(sorted(unknown))}",
            )
        saved = save_version(
            conn,
            talk,
            row,
            {
                "claims": claims,
                "sentences": [line.model_dump(mode="json") for line in data.sentences],
                "conclusions": [line.model_dump(mode="json") for line in data.conclusions],
                "status": SummaryStatus.READY.value,
            },
        )
        # Правка проверенной сводки снимает проверку доклада (spec §6.8).
        status = TalkStatus(talk["status"])
        if status is TalkStatus.REVIEWED:
            set_talk_status(conn, talk_id, talk["version"], TalkStatus.PROCESSED)
            status = TalkStatus.PROCESSED
    etag(response, saved["number"])
    return view(saved, talk, status)


@router.post("/talks/{talk_id}/summary/review", response_model=TalkSummaryView)
def review_summary(
    request: Request, response: Response, talk_id: UUID, version: Version
) -> TalkSummaryView:
    """Человек проверил сводку: утверждения из её текста подтверждены, доклад проверен."""
    with engine(request).begin() as conn:
        talk, row = locked(conn, talk_id, version)
        status = TalkStatus(talk["status"])
        if not can_transition(status, TalkStatus.REVIEWED):
            raise ApiRequestError(
                ApiErrorCode.VALIDATION_ERROR,
                f"Доклад в статусе {status.value} нельзя отметить проверенным",
            )
        summary = row["summary"]
        if not summary["sentences"]:
            raise ApiRequestError(
                ApiErrorCode.VALIDATION_ERROR, "В сводке нет текста: сначала допишите её"
            )
        used = {
            key
            for line in summary["sentences"] + summary["conclusions"]
            for key in line["claim_ids"]
        }
        claims = [
            {**claim, "status": ClaimStatus.CONFIRMED.value}
            if claim["key"] in used and claim["status"] != ClaimStatus.REJECTED.value
            else claim
            for claim in summary["claims"]
        ]
        saved = save_version(
            conn, talk, row, {"claims": claims, "status": SummaryStatus.READY.value}
        )
        set_talk_status(conn, talk_id, talk["version"], TalkStatus.REVIEWED)
    etag(response, saved["number"])
    return view(saved, talk, TalkStatus.REVIEWED)


@router.get(
    "/source-files/{file_id}/slides/{slide_no}",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def slide_image(request: Request, file_id: UUID, slide_no: int) -> Response:
    """Картинка слайда из результата разбора презентации: к ней ведут ссылки сводки."""
    with engine(request).begin() as conn:
        file = store.require(conn, t.source_files, file_id)
        require_queue(conn)
        key = conn.scalar(
            text(
                "SELECT output->'artifact'->>'storage_key' FROM rapporteur_queue.jobs "
                "WHERE stage = 'slides' AND status = 'succeeded' AND mode = :mode "
                "AND inputs->>'conference_id' = :conference "
                "AND inputs->'artifacts'->0->>'sha256' = :sha ORDER BY created_at DESC LIMIT 1"
            ),
            # Разбор того же режима: в dev картинки слайдов — заглушки.
            {
                "conference": str(file["conference_id"]),
                "sha": file["sha256"],
                "mode": mode(request),
            },
        )
    if key is None:
        raise ApiRequestError(ApiErrorCode.NOT_FOUND, "Презентация ещё не разобрана")
    files = storage(request)
    slides = SlidesArtifact.model_validate_json(files.get(key))
    slide = next((item for item in slides.slides if item.slide_no == slide_no), None)
    if slide is None:
        raise ApiRequestError(ApiErrorCode.NOT_FOUND, "Такого слайда нет")
    return Response(
        files.get(slide.image_path),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )
