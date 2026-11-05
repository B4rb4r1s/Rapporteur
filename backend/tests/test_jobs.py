import datetime as dt
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from rapporteur_contracts import Job, JobAccepted


@pytest.fixture
def queued_job(database: Engine) -> str:
    # Схема берётся из миграции самой очереди, а не дублируется в backend.
    schema = Path(__file__).resolve().parents[2] / "workers/src/rapporteur_workers/schema.sql"
    job_id = uuid4()
    with database.begin() as conn:
        conn.exec_driver_sql(schema.read_text(encoding="utf-8"))
        jobs = sa.Table("jobs", sa.MetaData(), schema="rapporteur_queue", autoload_with=conn)
        conn.execute(
            jobs.insert().values(
                id=job_id,
                dedup_key="api-job",
                stage="notes",
                resource="cpu",
                mode="dev",
                inputs={
                    "conference_id": None,
                    "talk_id": None,
                    "artifacts": [],
                    "config_hash": "a" * 64,
                },
                config={},
                priority=0,
                max_attempts=4,
            )
        )
    return str(job_id)


def test_jobs_read_cancel_retry_and_priority(client: TestClient, queued_job: str) -> None:
    jid = queued_job
    job = client.get(f"/api/v1/jobs/{jid}")
    assert job.status_code == 200, job.text
    assert Job.model_validate(job.json()).created_at.utcoffset() == dt.timedelta(0)
    assert client.get("/api/v1/jobs?status=queued&resource=cpu").json()["page"]["total"] == 1
    assert client.get("/api/v1/jobs?status=failed").json()["items"] == []
    priority = client.put(f"/api/v1/jobs/{jid}/priority", json={"priority": 100})
    assert priority.status_code == 200 and priority.json()["priority"] == 100
    cancelled = client.post(f"/api/v1/jobs/{jid}/cancel")
    assert cancelled.status_code == 202
    assert JobAccepted.model_validate(cancelled.json()).job_id == UUID(jid)
    assert client.post(f"/api/v1/jobs/{jid}/cancel").status_code == 202
    assert client.get(f"/api/v1/jobs/{jid}").json()["status"] == "cancelled"
    assert client.post(f"/api/v1/jobs/{jid}/retry").status_code == 202
    assert client.post(f"/api/v1/jobs/{jid}/retry").status_code == 409
    assert client.get(f"/api/v1/jobs/{jid}").json()["status"] == "queued"


def test_running_cancel_preserves_lease_and_completed_cannot_retry(
    client: TestClient, database: Engine, queued_job: str
) -> None:
    with database.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE rapporteur_queue.jobs SET status='running', lease_token=:token, "
                "lease_until=clock_timestamp()+interval '60 seconds' WHERE id=:id"
            ),
            {"token": uuid4(), "id": queued_job},
        )
    assert (
        client.put(f"/api/v1/jobs/{queued_job}/priority", json={"priority": 9}).status_code == 409
    )
    assert client.post(f"/api/v1/jobs/{queued_job}/cancel").status_code == 202
    assert client.get(f"/api/v1/jobs/{queued_job}").json()["status"] == "cancel_requested"
    with database.begin() as conn:
        assert (
            conn.scalar(
                sa.text("SELECT lease_token FROM rapporteur_queue.jobs WHERE id=:id"),
                {"id": queued_job},
            )
            is not None
        )
        conn.execute(
            sa.text(
                "UPDATE rapporteur_queue.jobs SET status='succeeded', lease_token=NULL, "
                "lease_until=NULL WHERE id=:id"
            ),
            {"id": queued_job},
        )
    assert client.post(f"/api/v1/jobs/{queued_job}/cancel").status_code == 409
    assert client.post(f"/api/v1/jobs/{queued_job}/retry").status_code == 409


def test_queue_is_not_silently_faked_when_unconfigured(client: TestClient) -> None:
    response = client.get("/api/v1/jobs")
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "SELECT" not in response.text
