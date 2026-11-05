"""HTTP-представление очереди. Backend не импортирует код обработчиков конвейера."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import Connection, RowMapping, text

from rapporteur_backend.errors import API_ERRORS, ApiRequestError
from rapporteur_backend.routes import Limit, Offset, Page, engine
from rapporteur_backend.schemas import PriorityWrite
from rapporteur_contracts import Job, JobAccepted
from rapporteur_contracts.enums import ApiErrorCode, JobStatus, ResourceKind

router = APIRouter(prefix="/api/v1/jobs", responses=API_ERRORS)


def require_queue(conn: Connection) -> None:
    if conn.scalar(text("SELECT to_regclass('rapporteur_queue.jobs')")) is None:
        raise ApiRequestError(ApiErrorCode.INTERNAL_ERROR, "Сначала выполните миграцию очереди")


def job_model(row: RowMapping) -> Job:
    return Job(
        id=row["id"],
        type="stage",
        stage=row["stage"],
        conference_id=row["inputs"].get("conference_id"),
        talk_id=row["inputs"].get("talk_id"),
        resource=ResourceKind(row["resource"]),
        status=JobStatus(row["status"]),
        priority=row["priority"],
        attempt=row["attempt"],
        lease_until=row["lease_until"],
        heartbeat_at=row["heartbeat_at"],
        progress=None,
        current_step=row["stage"] if row["status"] == "running" else None,
        error_code=row["error_code"],
        created_at=row["created_at"],
    )


def require_job(conn: Connection, job_id: UUID, *, lock: bool = False) -> RowMapping:
    require_queue(conn)
    query = "SELECT * FROM rapporteur_queue.jobs WHERE id = :id" + (" FOR UPDATE" if lock else "")
    row = conn.execute(text(query), {"id": job_id}).mappings().one_or_none()
    if row is None:
        raise ApiRequestError(ApiErrorCode.NOT_FOUND, "Задача не найдена")
    return row


@router.get("", response_model=Page[Job])
def list_jobs(
    request: Request,
    limit: Limit = 50,
    offset: Offset = 0,
    conference_id: UUID | None = None,
    status: JobStatus | None = None,
    resource: ResourceKind | None = None,
) -> dict[str, Any]:
    clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    for name, value in [("status", status), ("resource", resource)]:
        if value is not None:
            clauses.append(f"{name} = :{name}")
            params[name] = value.value
    if conference_id is not None:
        clauses.append("inputs->>'conference_id' = :conference_id")
        params["conference_id"] = str(conference_id)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with engine(request).begin() as conn:
        require_queue(conn)
        total = conn.scalar(text("SELECT count(*) FROM rapporteur_queue.jobs" + where), params)
        rows = conn.execute(
            text(
                "SELECT * FROM rapporteur_queue.jobs"
                + where
                + " ORDER BY created_at, id LIMIT :limit OFFSET :offset"
            ),
            params,
        ).mappings()
        return {
            "items": [job_model(row) for row in rows],
            "page": {"total": total, "limit": limit, "offset": offset},
        }


@router.get("/{job_id}", response_model=Job)
def get_job(request: Request, job_id: UUID) -> Job:
    with engine(request).begin() as conn:
        return job_model(require_job(conn, job_id))


@router.post("/{job_id}/cancel", response_model=JobAccepted, status_code=202)
def cancel_job(request: Request, job_id: UUID) -> JobAccepted:
    with engine(request).begin() as conn:
        row = require_job(conn, job_id, lock=True)
        if row["status"] in {"queued", "retry_wait"}:
            conn.execute(
                text("UPDATE rapporteur_queue.jobs SET status = 'cancelled' WHERE id = :id"),
                {"id": job_id},
            )
        elif row["status"] == "running":
            conn.execute(
                text("UPDATE rapporteur_queue.jobs SET status = 'cancel_requested' WHERE id = :id"),
                {"id": job_id},
            )
        elif row["status"] not in {"cancel_requested", "cancelled"}:
            raise ApiRequestError(ApiErrorCode.VERSION_CONFLICT, "Задача уже завершена")
    return JobAccepted(job_id=job_id, status_url=f"/api/v1/jobs/{job_id}")


@router.post("/{job_id}/retry", response_model=JobAccepted, status_code=202)
def retry_job(request: Request, job_id: UUID) -> JobAccepted:
    with engine(request).begin() as conn:
        row = require_job(conn, job_id, lock=True)
        if row["status"] not in {"failed", "cancelled"}:
            raise ApiRequestError(
                ApiErrorCode.VERSION_CONFLICT, "Повтор разрешён после ошибки или отмены"
            )
        conn.execute(
            text("""UPDATE rapporteur_queue.jobs SET status = 'queued', attempt = 0,
            error_code = NULL, output = NULL, lease_token = NULL, lease_until = NULL,
            heartbeat_at = NULL, available_at = clock_timestamp() WHERE id = :id"""),
            {"id": job_id},
        )
    return JobAccepted(job_id=job_id, status_url=f"/api/v1/jobs/{job_id}")


@router.put("/{job_id}/priority", response_model=Job)
def priority_job(request: Request, job_id: UUID, data: PriorityWrite) -> Job:
    with engine(request).begin() as conn:
        row = require_job(conn, job_id, lock=True)
        if row["status"] not in {"queued", "retry_wait"}:
            raise ApiRequestError(
                ApiErrorCode.VERSION_CONFLICT, "Приоритет меняется у ожидающей задачи"
            )
        row = (
            conn.execute(
                text(
                    "UPDATE rapporteur_queue.jobs SET priority = :priority "
                    "WHERE id = :id RETURNING *"
                ),
                {"id": job_id, "priority": data.priority},
            )
            .mappings()
            .one()
        )
        return job_model(row)
