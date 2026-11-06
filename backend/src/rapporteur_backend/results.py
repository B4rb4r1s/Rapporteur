"""Перенос результатов задач в предметную БД (ADR-013).

Планировщик вызывает перенос перед каждым продвижением графа. Перенос идемпотентен: задача,
уже записанная в summaries.job_id или report_versions.id, повторно не переносится.
"""

import datetime as dt
from uuid import UUID, uuid5

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy import Connection

from rapporteur_backend import tables as t
from rapporteur_backend.db import LOCAL_USER_ID
from rapporteur_contracts import PackArtifact, ReportArtifact, SummaryArtifact, TalkSummaryBundle
from rapporteur_contracts.enums import JobStatus, TalkStatus, can_transition
from rapporteur_pipeline.storage import Storage
from rapporteur_workers.queue import Mode, Queue, QueueJob
from rapporteur_workers.runtime import log

_IMPORTED = frozenset({"summary", "report"})


def sync_results(
    conn: Connection, queue: Queue, storage: Storage, conference_id: UUID, mode: Mode
) -> None:
    jobs = sorted(
        (
            job
            for job in queue.for_conference(conference_id, mode)
            if job.stage in _IMPORTED and job.status is JobStatus.SUCCEEDED and job.output
        ),
        key=lambda job: job.created_at,
    )
    if not jobs:
        return
    ids = [job.id for job in jobs]
    known = set(conn.scalars(sa.select(t.summaries.c.job_id).where(t.summaries.c.job_id.in_(ids))))
    known |= set(
        conn.scalars(sa.select(t.report_versions.c.id).where(t.report_versions.c.id.in_(ids)))
    )
    for job in jobs:
        if job.id in known:
            continue
        try:
            if job.stage == "summary":
                import_summary(conn, storage, job)
            else:
                import_report(conn, storage, job)
        except (OSError, ValidationError, ValueError, StopIteration):
            # Битый или удалённый артефакт не останавливает перенос остальных результатов.
            log("result_import_failed", job_id=str(job.id), stage=job.stage)


def next_number(conn: Connection, column: sa.Column[int], where: sa.ColumnElement[bool]) -> int:
    return int(conn.scalar(sa.select(sa.func.coalesce(sa.func.max(column), 0)).where(where))) + 1


def import_summary(conn: Connection, storage: Storage, job: QueueJob) -> None:
    assert job.output is not None
    summary = SummaryArtifact.model_validate_json(
        storage.get(job.output.artifact.storage_key)
    ).summary
    pack_ref = next(ref for ref in job.inputs.artifacts if ref.name == "pack")
    pack = PackArtifact.model_validate_json(storage.get(pack_ref.storage_key)).pack
    talks = t.talks
    talk = (
        conn.execute(sa.select(talks).where(talks.c.id == summary.talk_id).with_for_update())
        .mappings()
        .one_or_none()
    )
    if talk is None:
        return  # Доклад удалён, пока шла обработка.
    number = next_number(conn, t.summaries.c.number, t.summaries.c.talk_id == summary.talk_id)
    entity_id = uuid5(job.id, "summary")
    stored = summary.model_copy(update={"id": entity_id, "version": number})
    TalkSummaryBundle(pack=pack, summary=stored)  # сводка и её пакет согласованы
    conn.execute(
        sa.insert(t.summaries).values(
            id=entity_id,
            talk_id=summary.talk_id,
            number=number,
            summary=stored.model_dump(mode="json"),
            pack=pack.model_dump(mode="json"),
            job_id=job.id,
            created_at=summary.created_at,
            created_by=None,
        )
    )
    # Новая машинная сводка требует проверки, даже если прошлую уже проверили.
    if can_transition(TalkStatus(talk["status"]), TalkStatus.PROCESSED):
        set_talk_status(conn, summary.talk_id, talk["version"], TalkStatus.PROCESSED)


def set_talk_status(conn: Connection, talk_id: UUID, version: int, status: TalkStatus) -> None:
    conn.execute(
        sa.update(t.talks)
        .where(t.talks.c.id == talk_id)
        .values(status=status.value, version=version + 1, edited_by=LOCAL_USER_ID)
    )


def import_report(conn: Connection, storage: Storage, job: QueueJob) -> None:
    assert job.output is not None
    artifact = ReportArtifact.model_validate_json(storage.get(job.output.artifact.storage_key))
    report = conn.scalar(
        sa.select(t.reports.c.id).where(t.reports.c.id == artifact.report_id).with_for_update()
    )
    if report is None:
        return
    versions = t.report_versions
    conn.execute(
        sa.insert(versions).values(
            id=job.id,
            report_id=artifact.report_id,
            number=next_number(conn, versions.c.number, versions.c.report_id == report),
            input=artifact.input.model_dump(mode="json"),
            variables=artifact.variables.model_dump(mode="json"),
            files=[file.model_dump(mode="json") for file in artifact.files],
            created_at=artifact.meta.produced_at.astimezone(dt.UTC),
            created_by=LOCAL_USER_ID,
        )
    )
