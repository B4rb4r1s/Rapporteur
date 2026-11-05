"""Небольшая очередь без брокера. Владение попыткой ограждено UUID и lease в БД.

Схема очереди отдельная: будущие миграции backend (P1-04) ею не владеют.
Соединение и транзакция короткие; обработчик этапа выполняется вне транзакции.
"""

import random
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, JsonValue, field_validator

from rapporteur_contracts import StageInputs, StageOutput
from rapporteur_contracts.enums import JobStatus, ResourceKind

Mode = Literal["dev", "gpu"]


class QueueJob(BaseModel):
    id: UUID
    dedup_key: str
    stage: str
    resource: ResourceKind
    mode: Mode
    inputs: StageInputs
    config: dict[str, JsonValue]
    status: JobStatus
    priority: int
    attempt: int
    max_attempts: int
    lease_token: UUID | None
    lease_until: datetime | None
    heartbeat_at: datetime | None
    output: StageOutput | None
    error_code: str | None
    created_at: datetime

    @field_validator("lease_until", "heartbeat_at", "created_at")
    @classmethod
    def utc_datetime(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None


def migrate(dsn: str) -> None:
    """Явная, идемпотентная миграция; параллельные запуски сериализованы в PostgreSQL."""
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(728103101)")
        exists = conn.execute("SELECT to_regclass('rapporteur_queue.schema_version')").fetchone()
        if exists is not None and exists[0] is not None:
            versions = conn.execute(
                "SELECT version FROM rapporteur_queue.schema_version ORDER BY version"
            ).fetchall()
            if versions != [(1,)]:
                raise RuntimeError("Неизвестная версия схемы очереди; нужен совместимый образ")
        else:
            conn.execute(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
        conn.execute(
            "CREATE INDEX IF NOT EXISTS jobs_conference_mode "
            "ON rapporteur_queue.jobs ((inputs->>'conference_id'), mode)"
        )


class Queue:
    def __init__(self, dsn: str, *, lease_seconds: float = 60) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds должен быть положительным")
        self.dsn = dsn
        self.lease_seconds = lease_seconds

    def check(self) -> None:
        with psycopg.connect(self.dsn, connect_timeout=5) as conn:
            row = conn.execute("SELECT version FROM rapporteur_queue.schema_version").fetchall()
            if row != [(1,)]:
                raise RuntimeError("Сначала выполните make migrate для очереди")

    def enqueue(
        self,
        *,
        key: str,
        stage: str,
        resource: ResourceKind,
        mode: Mode,
        inputs: StageInputs,
        config: dict[str, JsonValue],
        priority: int = 0,
        max_attempts: int = 4,
    ) -> UUID:
        if max_attempts < 1:
            raise ValueError("max_attempts должен быть положительным")
        with psycopg.connect(self.dsn, connect_timeout=5) as conn:
            row = conn.execute(
                """INSERT INTO rapporteur_queue.jobs
                (id, dedup_key, stage, resource, mode, inputs, config, priority, max_attempts)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (dedup_key) DO NOTHING
                RETURNING id""",
                (
                    uuid4(),
                    key,
                    stage,
                    resource.value,
                    mode,
                    Jsonb(inputs.model_dump(mode="json")),
                    Jsonb(config),
                    priority,
                    max_attempts,
                ),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT id FROM rapporteur_queue.jobs WHERE dedup_key = %s", (key,)
                ).fetchone()
            assert row is not None
            return UUID(str(row[0]))

    def get(self, job_id: UUID) -> QueueJob | None:
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as conn:
            row = conn.execute(
                "SELECT * FROM rapporteur_queue.jobs WHERE id = %s", (job_id,)
            ).fetchone()
            return QueueJob.model_validate(row) if row is not None else None

    def for_conference(self, conference_id: UUID, mode: Mode) -> list[QueueJob]:
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as conn:
            rows = conn.execute(
                "SELECT * FROM rapporteur_queue.jobs WHERE inputs->>'conference_id' = %s "
                "AND mode = %s",
                (str(conference_id), mode),
            ).fetchall()
            return [QueueJob.model_validate(row) for row in rows]

    def claim(self, resource: ResourceKind, mode: Mode, prefer_stage: str = "") -> QueueJob | None:
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as conn:
            # Reaper не ждёт чужую публикацию и не отбирает живую попытку.
            conn.execute(
                """WITH expired AS (
                    SELECT id FROM rapporteur_queue.jobs
                    WHERE status IN ('running', 'cancel_requested')
                      AND lease_until <= clock_timestamp()
                    FOR UPDATE SKIP LOCKED
                ) UPDATE rapporteur_queue.jobs j SET
                    status = CASE WHEN status = 'cancel_requested' THEN 'cancelled'
                                  WHEN attempt >= max_attempts THEN 'failed' ELSE 'retry_wait' END,
                    error_code = CASE WHEN status = 'cancel_requested' THEN NULL
                                      ELSE 'lease_expired' END,
                    available_at = clock_timestamp() +
                        (LEAST(60, power(2, attempt)) * (0.5 + random())) * interval '1 second',
                    lease_token = NULL, lease_until = NULL
                FROM expired WHERE j.id = expired.id"""
            )
            row = conn.execute(
                """WITH ready AS (
                    SELECT id FROM rapporteur_queue.jobs
                    WHERE resource = %s AND mode = %s AND status IN ('queued', 'retry_wait')
                      AND available_at <= clock_timestamp()
                    ORDER BY priority DESC, (stage = %s) DESC, created_at, id
                    FOR UPDATE SKIP LOCKED LIMIT 1
                ) UPDATE rapporteur_queue.jobs j SET status = 'running', attempt = attempt + 1,
                    lease_token = %s,
                    lease_until = clock_timestamp() + %s * interval '1 second',
                    heartbeat_at = clock_timestamp(), error_code = NULL
                FROM ready WHERE j.id = ready.id RETURNING j.*""",
                (resource.value, mode, prefer_stage, uuid4(), self.lease_seconds),
            ).fetchone()
            return QueueJob.model_validate(row) if row is not None else None

    def heartbeat(self, job: QueueJob) -> bool:
        with psycopg.connect(self.dsn, connect_timeout=5) as conn:
            # При ожидании блокировки срок проверяется после её получения, а не до него.
            conn.execute(
                "SELECT id FROM rapporteur_queue.jobs WHERE id = %s AND lease_token = %s "
                "AND status = 'running' FOR UPDATE",
                (job.id, job.lease_token),
            )
            return (
                conn.execute(
                    """UPDATE rapporteur_queue.jobs SET heartbeat_at = clock_timestamp(),
                    lease_until = clock_timestamp() + %s * interval '1 second'
                WHERE id = %s AND lease_token = %s AND status = 'running'
                  AND lease_until > clock_timestamp()""",
                    (self.lease_seconds, job.id, job.lease_token),
                ).rowcount
                == 1
            )

    def cancel(self, job_id: UUID) -> bool:
        with psycopg.connect(self.dsn, connect_timeout=5) as conn:
            return (
                conn.execute(
                    """UPDATE rapporteur_queue.jobs
                SET status = CASE WHEN status = 'running' THEN 'cancel_requested'
                                  ELSE 'cancelled' END
                WHERE id = %s AND status IN ('queued', 'retry_wait', 'running')""",
                    (job_id,),
                ).rowcount
                == 1
            )

    def finish(
        self, job: QueueJob, output: StageOutput, publish: Callable[[], StageOutput | None]
    ) -> bool:
        """Завершение линейризуется захватом строки с действующим lease.

        Временные файлы публикуются только после проверки владения под блокировкой.
        Отмена и новая попытка сериализованы той же строкой. Сбой публикации откатывает
        транзакцию: частичные файлы не становятся результатом задачи.
        """
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as conn:
            row = conn.execute(
                """SELECT status FROM rapporteur_queue.jobs WHERE id = %s AND lease_token = %s
                AND lease_until > clock_timestamp() FOR UPDATE""",
                (job.id, job.lease_token),
            ).fetchone()
            if row is None:
                return False
            # Разные задания могут вычислять один ключ кэша. Публикуется первая готовая
            # версия: второй воркер переиспользует её, сохраняя SHA-256 прежних ссылок.
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 728103102))",
                (output.meta.cache_key,),
            )
            # FOR UPDATE мог ждать блокировку: перепроверяем время уже после её захвата.
            valid = conn.execute(
                "SELECT lease_until > clock_timestamp() AS valid FROM rapporteur_queue.jobs "
                "WHERE id = %s",
                (job.id,),
            ).fetchone()
            if valid is None or not valid["valid"]:
                return False
            if row["status"] == "cancel_requested":
                conn.execute(
                    """UPDATE rapporteur_queue.jobs SET status = 'cancelled',
                    lease_token = NULL, lease_until = NULL WHERE id = %s""",
                    (job.id,),
                )
                return False
            if row["status"] != "running":
                return False
            output = publish() or output
            conn.execute(
                """UPDATE rapporteur_queue.jobs SET status = 'succeeded', output = %s,
                lease_token = NULL, lease_until = NULL WHERE id = %s""",
                (Jsonb(output.model_dump(mode="json")), job.id),
            )
            return True

    def fail(self, job: QueueJob, code: str, *, transient: bool = False) -> bool:
        delay = min(60, 2**job.attempt) * random.uniform(0.5, 1.5)
        status = "retry_wait" if transient and job.attempt < job.max_attempts else "failed"
        with psycopg.connect(self.dsn, connect_timeout=5) as conn:
            conn.execute(
                "SELECT id FROM rapporteur_queue.jobs WHERE id = %s AND lease_token = %s "
                "FOR UPDATE",
                (job.id, job.lease_token),
            )
            return (
                conn.execute(
                    """UPDATE rapporteur_queue.jobs SET
                    status = CASE WHEN status = 'cancel_requested' THEN 'cancelled' ELSE %s END,
                    error_code = CASE WHEN status = 'cancel_requested' THEN NULL ELSE %s END,
                    available_at = clock_timestamp() + %s * interval '1 second',
                    lease_token = NULL, lease_until = NULL
                WHERE id = %s AND lease_token = %s
                  AND status IN ('running', 'cancel_requested')
                  AND lease_until > clock_timestamp()""",
                    (status, code, delay, job.id, job.lease_token),
                ).rowcount
                == 1
            )
