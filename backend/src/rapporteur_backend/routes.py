"""CRUD MVP. Все изменения защищены If-Match и коммитятся до отправки ответа."""

import re
from typing import Annotated, Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import Engine

from rapporteur_backend import store
from rapporteur_backend import tables as t
from rapporteur_backend.db import LOCAL_USER_ID
from rapporteur_backend.errors import API_ERRORS, ApiRequestError
from rapporteur_backend.schemas import (
    ConferenceWrite,
    PersonWrite,
    ReportWrite,
    SectionWrite,
    TalkWrite,
)
from rapporteur_contracts import Conference, PageMeta, Person, Report, ReportVersion, Section, Talk
from rapporteur_contracts.enums import ApiErrorCode

router = APIRouter(prefix="/api/v1", responses=API_ERRORS)
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


class Page[T](BaseModel):
    items: list[T]
    page: PageMeta


def engine(request: Request) -> Engine:
    result: Engine | None = request.app.state.database
    if result is None:
        raise ApiRequestError(
            ApiErrorCode.INTERNAL_ERROR, "База не настроена: укажите переменные PG*"
        )
    return result


def expected_version(
    if_match: Annotated[
        str,
        Header(
            alias="If-Match",
            max_length=32,
            description='Сильный ETag последнего чтения, например "1"',
        ),
    ],
) -> int:
    match = re.fullmatch(r'"([1-9][0-9]*)"', if_match)
    if match is None:
        raise ApiRequestError(ApiErrorCode.VALIDATION_ERROR, 'If-Match должен иметь вид "1"')
    return int(match[1])


Version = Annotated[int, Depends(expected_version)]


def etag(response: Response, version: int) -> None:
    response.headers["ETag"] = f'"{version}"'


def get_one(request: Request, response: Response, table: sa.Table, entity_id: UUID) -> BaseModel:
    with engine(request).begin() as conn:
        row = store.require(conn, table, entity_id)
        etag(response, row["version"])
        result = store.encode(conn, table, row)
    return result


def delete_one(request: Request, table: sa.Table, entity_id: UUID, version: int) -> Response:
    with engine(request).begin() as conn:
        store.delete(conn, table, entity_id, version)
    return Response(status_code=204)


@router.post("/conferences", response_model=Conference, status_code=201)
def create_conference(request: Request, response: Response, data: ConferenceWrite) -> BaseModel:
    with engine(request).begin() as conn:
        row = store.create(
            conn, t.conferences, {**data.model_dump(mode="json"), "owner_id": LOCAL_USER_ID}
        )
        result = store.encode(conn, t.conferences, row)
    etag(response, 1)
    return result


@router.get("/conferences", response_model=Page[Conference])
def list_conferences(request: Request, limit: Limit = 50, offset: Offset = 0) -> dict[str, Any]:
    with engine(request).begin() as conn:
        return store.page(conn, t.conferences, limit, offset, [])


@router.get("/conferences/{conference_id}", response_model=Conference)
def get_conference(request: Request, response: Response, conference_id: UUID) -> BaseModel:
    return get_one(request, response, t.conferences, conference_id)


@router.put("/conferences/{conference_id}", response_model=Conference)
def update_conference(
    request: Request,
    response: Response,
    conference_id: UUID,
    data: ConferenceWrite,
    version: Version,
) -> BaseModel:
    with engine(request).begin() as conn:
        row = store.replace(
            conn, t.conferences, conference_id, version, data.model_dump(mode="json")
        )
        result = store.encode(conn, t.conferences, row)
    etag(response, row["version"])
    return result


@router.delete("/conferences/{conference_id}", status_code=204)
def delete_conference(request: Request, conference_id: UUID, version: Version) -> Response:
    with engine(request).begin() as conn:
        store.delete(conn, t.conferences, conference_id, version)
        # Задачи удалённой конференции не должны занимать воркеры, в том числе модель.
        if conn.scalar(sa.text("SELECT to_regclass('rapporteur_queue.jobs')")) is not None:
            conn.execute(
                sa.text(
                    "UPDATE rapporteur_queue.jobs SET status = CASE WHEN status = 'running' "
                    "THEN 'cancel_requested' ELSE 'cancelled' END "
                    "WHERE inputs->>'conference_id' = :id "
                    "AND status IN ('queued', 'retry_wait', 'running')"
                ),
                {"id": str(conference_id)},
            )
    return Response(status_code=204)


@router.post("/conferences/{conference_id}/sections", response_model=Section, status_code=201)
def create_section(
    request: Request, response: Response, conference_id: UUID, data: SectionWrite
) -> BaseModel:
    with engine(request).begin() as conn:
        store.require(conn, t.conferences, conference_id, lock=True)
        count = conn.scalar(
            sa.select(sa.func.count())
            .select_from(t.sections)
            .where(t.sections.c.conference_id == conference_id)
        )
        if count is not None and count >= 5:
            raise ApiRequestError(
                ApiErrorCode.VALIDATION_ERROR, "В конференции не больше пяти секций"
            )
        row = store.create(
            conn, t.sections, {**data.model_dump(mode="json"), "conference_id": conference_id}
        )
        result = store.encode(conn, t.sections, row)
    etag(response, 1)
    return result


@router.get("/conferences/{conference_id}/sections", response_model=Page[Section])
def list_sections(
    request: Request, conference_id: UUID, limit: Limit = 50, offset: Offset = 0
) -> dict[str, Any]:
    with engine(request).begin() as conn:
        store.require(conn, t.conferences, conference_id)
        return store.page(
            conn, t.sections, limit, offset, [t.sections.c.conference_id == conference_id]
        )


@router.get("/sections/{section_id}", response_model=Section)
def get_section(request: Request, response: Response, section_id: UUID) -> BaseModel:
    return get_one(request, response, t.sections, section_id)


@router.put("/sections/{section_id}", response_model=Section)
def update_section(
    request: Request, response: Response, section_id: UUID, data: SectionWrite, version: Version
) -> BaseModel:
    with engine(request).begin() as conn:
        row = store.replace(conn, t.sections, section_id, version, data.model_dump(mode="json"))
        result = store.encode(conn, t.sections, row)
    etag(response, row["version"])
    return result


@router.delete("/sections/{section_id}", status_code=204)
def delete_section(request: Request, section_id: UUID, version: Version) -> Response:
    return delete_one(request, t.sections, section_id, version)


@router.post("/people", response_model=Person, status_code=201)
def create_person(request: Request, response: Response, data: PersonWrite) -> BaseModel:
    with engine(request).begin() as conn:
        row = store.create(conn, t.people, data.model_dump())
        result = store.encode(conn, t.people, row)
    etag(response, 1)
    return result


@router.get("/people", response_model=Page[Person])
def list_people(request: Request, limit: Limit = 50, offset: Offset = 0) -> dict[str, Any]:
    with engine(request).begin() as conn:
        return store.page(conn, t.people, limit, offset, [])


@router.get("/people/{person_id}", response_model=Person)
def get_person(request: Request, response: Response, person_id: UUID) -> BaseModel:
    return get_one(request, response, t.people, person_id)


@router.put("/people/{person_id}", response_model=Person)
def update_person(
    request: Request, response: Response, person_id: UUID, data: PersonWrite, version: Version
) -> BaseModel:
    with engine(request).begin() as conn:
        row = store.replace(conn, t.people, person_id, version, data.model_dump())
        result = store.encode(conn, t.people, row)
    etag(response, row["version"])
    return result


@router.delete("/people/{person_id}", status_code=204)
def delete_person(request: Request, person_id: UUID, version: Version) -> Response:
    return delete_one(request, t.people, person_id, version)


@router.post("/sections/{section_id}/talks", response_model=Talk, status_code=201)
def create_talk(
    request: Request, response: Response, section_id: UUID, data: TalkWrite
) -> BaseModel:
    with engine(request).begin() as conn:
        store.require(conn, t.sections, section_id)
        values = store.talk_values(conn, data)
        row = store.create(conn, t.talks, {**values, "section_id": section_id})
        store.save_participants(conn, row["id"], data)
        result = store.encode(conn, t.talks, row)
    etag(response, 1)
    return result


@router.get("/sections/{section_id}/talks", response_model=Page[Talk])
def list_talks(
    request: Request, section_id: UUID, limit: Limit = 50, offset: Offset = 0
) -> dict[str, Any]:
    with engine(request).begin() as conn:
        store.require(conn, t.sections, section_id)
        return store.page(conn, t.talks, limit, offset, [t.talks.c.section_id == section_id])


@router.get("/talks/{talk_id}", response_model=Talk)
def get_talk(request: Request, response: Response, talk_id: UUID) -> BaseModel:
    return get_one(request, response, t.talks, talk_id)


@router.put("/talks/{talk_id}", response_model=Talk)
def update_talk(
    request: Request, response: Response, talk_id: UUID, data: TalkWrite, version: Version
) -> BaseModel:
    with engine(request).begin() as conn:
        old = store.require(conn, t.talks, talk_id, lock=True)
        store.check_version(old, version)
        row = store.replace(
            conn, t.talks, talk_id, version, store.talk_values(conn, data, old["status"])
        )
        store.save_participants(conn, talk_id, data)
        result = store.encode(conn, t.talks, row)
    etag(response, row["version"])
    return result


@router.delete("/talks/{talk_id}", status_code=204)
def delete_talk(request: Request, talk_id: UUID, version: Version) -> Response:
    return delete_one(request, t.talks, talk_id, version)


@router.post("/conferences/{conference_id}/reports", response_model=Report, status_code=201)
def create_report(
    request: Request, response: Response, conference_id: UUID, data: ReportWrite
) -> BaseModel:
    with engine(request).begin() as conn:
        store.require(conn, t.conferences, conference_id)
        values = store.report_values(conn, conference_id, data)
        row = store.create(
            conn, t.reports, {**values, "conference_id": conference_id, "author_id": LOCAL_USER_ID}
        )
        store.save_selection(conn, row["id"], data)
        result = store.encode(conn, t.reports, row)
    etag(response, 1)
    return result


@router.get("/conferences/{conference_id}/reports", response_model=Page[Report])
def list_reports(
    request: Request, conference_id: UUID, limit: Limit = 50, offset: Offset = 0
) -> dict[str, Any]:
    with engine(request).begin() as conn:
        store.require(conn, t.conferences, conference_id)
        return store.page(
            conn, t.reports, limit, offset, [t.reports.c.conference_id == conference_id]
        )


@router.get("/reports/{report_id}", response_model=Report)
def get_report(request: Request, response: Response, report_id: UUID) -> BaseModel:
    return get_one(request, response, t.reports, report_id)


@router.put("/reports/{report_id}", response_model=Report)
def update_report(
    request: Request, response: Response, report_id: UUID, data: ReportWrite, version: Version
) -> BaseModel:
    with engine(request).begin() as conn:
        old = store.require(conn, t.reports, report_id, lock=True)
        store.check_version(old, version)
        row = store.replace(
            conn,
            t.reports,
            report_id,
            version,
            store.report_values(conn, old["conference_id"], data),
        )
        store.save_selection(conn, report_id, data)
        result = store.encode(conn, t.reports, row)
    etag(response, row["version"])
    return result


@router.delete("/reports/{report_id}", status_code=204)
def delete_report(request: Request, report_id: UUID, version: Version) -> Response:
    return delete_one(request, t.reports, report_id, version)


@router.get("/reports/{report_id}/versions", response_model=Page[ReportVersion])
def list_report_versions(
    request: Request, report_id: UUID, limit: Limit = 50, offset: Offset = 0
) -> dict[str, Any]:
    with engine(request).begin() as conn:
        store.require(conn, t.reports, report_id)
        query = sa.select(t.report_versions).where(t.report_versions.c.report_id == report_id)
        total = conn.scalar(sa.select(sa.func.count()).select_from(query.subquery()))
        rows = conn.execute(
            query.order_by(t.report_versions.c.number.desc()).limit(limit).offset(offset)
        ).mappings()
        return {
            "items": [
                ReportVersion.model_validate(
                    {name: row[name] for name in ReportVersion.model_fields}
                )
                for row in rows
            ],
            "page": {"total": total, "limit": limit, "offset": offset},
        }
