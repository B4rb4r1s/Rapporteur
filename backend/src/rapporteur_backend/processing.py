"""Предметный снимок для advance и периодический запуск планировщика."""

from collections import defaultdict
from threading import Event
from uuid import UUID

import psycopg
import sqlalchemy as sa
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from rapporteur_backend import tables as t
from rapporteur_contracts import ArtifactRef, PipelineGraph
from rapporteur_contracts.artifacts import InventoryItem
from rapporteur_contracts.enums import FileKind, HumanGate, TalkStatus
from rapporteur_pipeline.ingest.inventory_stage import InventoryConfig, InventoryStage
from rapporteur_pipeline.stage import SOURCE_INPUT
from rapporteur_pipeline.storage import blob_key
from rapporteur_workers.queue import Mode, Queue
from rapporteur_workers.runtime import log
from rapporteur_workers.scheduler import ConferenceState, FileState, Scheduler, TalkState


def load_state(conn: Connection, conference_id: UUID) -> ConferenceState:
    files, bindings = t.source_files, t.file_bindings
    rows = (
        conn.execute(
            sa.select(files, bindings.c.confirmed, bindings.c.talk_id)
            .outerjoin(bindings)
            .where(files.c.conference_id == conference_id)
            .order_by(files.c.sha256)
        )
        .mappings()
        .all()
    )
    by_talk: dict[UUID, list[UUID]] = defaultdict(list)
    for row in rows:
        if row["confirmed"] and row["talk_id"] is not None:
            by_talk[row["talk_id"]].append(row["id"])
    talks, sections = t.talks, t.sections
    talk_rows = conn.execute(
        sa.select(talks.c.id, talks.c.status)
        .join(sections)
        .where(sections.c.conference_id == conference_id)
    )
    inventory = InventoryConfig(
        files=[
            InventoryItem.model_validate({key: row[key] for key in InventoryItem.model_fields})
            for row in rows
        ]
    )
    return ConferenceState(
        conference_id=conference_id,
        files=[
            FileState(
                id=row["id"],
                kind=FileKind(row["kind"]),
                source=ArtifactRef(
                    name=SOURCE_INPUT, storage_key=blob_key(row["sha256"]), sha256=row["sha256"]
                ),
            )
            for row in rows
        ],
        talks=[
            TalkState(id=talk_id, status=TalkStatus(status), file_ids=by_talk[talk_id])
            for talk_id, status in talk_rows
        ],
        gates={HumanGate.BINDING_CONFIRMED}
        if rows and all(row["confirmed"] for row in rows)
        else set(),
        configs={InventoryStage.name: inventory.model_dump(mode="json")},
    )


def advance_started(database: Engine, queue: Queue, mode: Mode, graph: PipelineGraph) -> int:
    conferences = t.conferences
    with database.connect() as conn:
        started = list(
            conn.scalars(
                sa.select(conferences.c.id)
                .where(conferences.c.processing_started)
                .order_by(conferences.c.id)
            )
        )
    failures = 0
    for conference_id in started:
        try:
            with (
                database.connect().execution_options(isolation_level="REPEATABLE READ") as conn,
                conn.begin(),
            ):
                # Загрузка и подтверждение привязок держат ту же строку до commit.
                active = conn.scalar(
                    sa.select(conferences.c.id)
                    .where(conferences.c.id == conference_id, conferences.c.processing_started)
                    .with_for_update(skip_locked=True)
                )
                if active is not None:
                    Scheduler(
                        queue, lambda target: load_state(conn, target), mode, graph=graph
                    ).advance(conference_id)
        except (SQLAlchemyError, psycopg.Error):
            # Другие конференции всё равно получат свой проход. Следующий цикл повторит этот.
            failures += 1
            log("schedule_failed", conference_id=str(conference_id))
    return failures


def run_scheduler(
    database: Engine,
    queue: Queue,
    mode: Mode,
    graph: PipelineGraph,
    stop: Event,
    poll_seconds: float = 2,
) -> None:
    queue.check()
    while not stop.is_set():
        try:
            advance_started(database, queue, mode, graph)
        except SQLAlchemyError:
            log("scheduler_poll_failed")
        stop.wait(poll_seconds)
