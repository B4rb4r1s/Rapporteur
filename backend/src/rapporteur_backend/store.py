"""Операции предметной БД выполняются в транзакции запроса."""

from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy import Connection, RowMapping, Table

from rapporteur_backend import tables as t
from rapporteur_backend.db import LOCAL_USER_ID
from rapporteur_backend.errors import ApiRequestError
from rapporteur_backend.schemas import ReportWrite, TalkWrite
from rapporteur_contracts import Conference, Person, Report, Section, Talk
from rapporteur_contracts.entities import TalkParticipant
from rapporteur_contracts.enums import ApiErrorCode, TalkStatus, can_transition
from rapporteur_contracts.reports import ReportSelection


def require(conn: Connection, table: Table, entity_id: UUID, *, lock: bool = False) -> RowMapping:
    statement = sa.select(table).where(table.c.id == entity_id)
    if lock:
        statement = statement.with_for_update()
    row = conn.execute(statement).mappings().one_or_none()
    if row is None:
        raise ApiRequestError(ApiErrorCode.NOT_FOUND, "Ресурс не найден")
    return row


def check_version(row: RowMapping, expected: int) -> None:
    if row["version"] != expected:
        raise ApiRequestError(ApiErrorCode.VERSION_CONFLICT, "Объект изменён: обновите данные")


def create(
    conn: Connection, table: Table, values: dict[str, Any], *, entity_id: UUID | None = None
) -> RowMapping:
    row = (
        conn.execute(
            sa.insert(table)
            .values(id=entity_id or uuid4(), version=1, edited_by=LOCAL_USER_ID, **values)
            .returning(table)
        )
        .mappings()
        .one()
    )
    return row


def replace(
    conn: Connection, table: Table, entity_id: UUID, expected: int, values: dict[str, Any]
) -> RowMapping:
    check_version(require(conn, table, entity_id, lock=True), expected)
    return (
        conn.execute(
            sa.update(table)
            .where(table.c.id == entity_id)
            .values(**values, version=expected + 1, edited_by=LOCAL_USER_ID)
            .returning(table)
        )
        .mappings()
        .one()
    )


def delete(conn: Connection, table: Table, entity_id: UUID, expected: int) -> None:
    check_version(require(conn, table, entity_id, lock=True), expected)
    conn.execute(sa.delete(table).where(table.c.id == entity_id))


def encode(
    conn: Connection, table: Table, row: RowMapping
) -> Conference | Section | Person | Talk | Report:
    model: type[BaseModel]
    if table is t.conferences:
        model = Conference
    elif table is t.sections:
        model = Section
    elif table is t.people:
        model = Person
    elif table is t.talks:
        values = {name: row[name] for name in Talk.model_fields if name != "participants"}
        values["participants"] = [
            TalkParticipant.model_validate(dict(item))
            for item in conn.execute(
                sa.select(t.participants.c.person_id, t.participants.c.role, t.participants.c.order)
                .where(t.participants.c.talk_id == row["id"])
                .order_by(t.participants.c.order, t.participants.c.role, t.participants.c.person_id)
            ).mappings()
        ]
        return Talk.model_validate(values)
    elif table is t.reports:
        values = {name: row[name] for name in Report.model_fields if name != "selection"}
        section_ids = list(
            conn.execute(
                sa.select(t.report_sections.c.section_id)
                .where(t.report_sections.c.report_id == row["id"])
                .order_by(t.report_sections.c.position)
            ).scalars()
        )
        talk_ids = list(
            conn.execute(
                sa.select(t.report_talks.c.talk_id)
                .where(t.report_talks.c.report_id == row["id"])
                .order_by(t.report_talks.c.position)
            ).scalars()
        )
        values["selection"] = ReportSelection(
            days=row["days"], section_ids=section_ids, talk_ids=talk_ids
        )
        return Report.model_validate(values)
    else:
        raise ValueError("Неизвестная таблица API")
    return model.model_validate({name: row[name] for name in model.model_fields})


def page(
    conn: Connection, table: Table, limit: int, offset: int, filters: list[sa.ColumnElement[bool]]
) -> dict[str, Any]:
    total = conn.scalar(sa.select(sa.func.count()).select_from(table).where(*filters))
    ordering = [table.c.order, table.c.id] if "order" in table.c else [table.c.id]
    rows = conn.execute(
        sa.select(table).where(*filters).order_by(*ordering).limit(limit).offset(offset)
    ).mappings()
    return {
        "items": [encode(conn, table, row) for row in rows],
        "page": {"total": total, "limit": limit, "offset": offset},
    }


def talk_values(conn: Connection, data: TalkWrite, old_status: str | None = None) -> dict[str, Any]:
    if old_status is not None and old_status != data.status.value:
        if not can_transition(TalkStatus(old_status), data.status):
            raise ApiRequestError(
                ApiErrorCode.VERSION_CONFLICT, "Недопустимый переход статуса доклада"
            )
    elif old_status is None and data.status not in {TalkStatus.DRAFT, TalkStatus.INSUFFICIENT_DATA}:
        raise ApiRequestError(
            ApiErrorCode.VALIDATION_ERROR, "Новый доклад создаётся в статусе draft"
        )
    ids = {person.person_id for person in data.participants}
    known = set(conn.execute(sa.select(t.people.c.id).where(t.people.c.id.in_(ids))).scalars())
    if ids != known:
        raise ApiRequestError(ApiErrorCode.VALIDATION_ERROR, "Участник доклада не найден")
    return data.model_dump(mode="json", exclude={"participants"})


def save_participants(conn: Connection, talk_id: UUID, data: TalkWrite) -> None:
    conn.execute(sa.delete(t.participants).where(t.participants.c.talk_id == talk_id))
    if data.participants:
        conn.execute(
            sa.insert(t.participants),
            [{"talk_id": talk_id, **person.model_dump()} for person in data.participants],
        )


def report_values(conn: Connection, conference_id: UUID, data: ReportWrite) -> dict[str, Any]:
    selection = data.selection
    sections = set(
        conn.execute(
            sa.select(t.sections.c.id).where(
                t.sections.c.conference_id == conference_id,
                t.sections.c.id.in_(selection.section_ids),
            )
        ).scalars()
    )
    if sections != set(selection.section_ids):
        raise ApiRequestError(ApiErrorCode.VALIDATION_ERROR, "Секция не принадлежит конференции")
    rows = (
        conn.execute(
            sa.select(
                t.talks.c.id,
                t.talks.c.section_id,
                sa.func.coalesce(t.talks.c.date, t.sections.c.date).label("date"),
            )
            .join(t.sections)
            .where(
                t.sections.c.conference_id == conference_id, t.talks.c.id.in_(selection.talk_ids)
            )
        )
        .mappings()
        .all()
    )
    if {row["id"] for row in rows} != set(selection.talk_ids):
        raise ApiRequestError(ApiErrorCode.VALIDATION_ERROR, "Доклад не принадлежит конференции")
    if selection.section_ids and any(row["section_id"] not in sections for row in rows):
        raise ApiRequestError(ApiErrorCode.VALIDATION_ERROR, "Доклад не входит в выбранные секции")
    if selection.days and any(row["date"] not in selection.days for row in rows):
        raise ApiRequestError(ApiErrorCode.VALIDATION_ERROR, "Доклад не входит в выбранные дни")
    return {
        "template_version": data.template_version,
        "days": [day.isoformat() for day in selection.days],
    }


def save_selection(conn: Connection, report_id: UUID, data: ReportWrite) -> None:
    for table, column, values in [
        (t.report_sections, "section_id", data.selection.section_ids),
        (t.report_talks, "talk_id", data.selection.talk_ids),
    ]:
        conn.execute(sa.delete(table).where(table.c.report_id == report_id))
        if values:
            conn.execute(
                sa.insert(table),
                [
                    {"report_id": report_id, column: value, "position": index}
                    for index, value in enumerate(values)
                ],
            )
