import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import psycopg
import pytest

from rapporteur_contracts import ArtifactMeta, ArtifactRef, StageInputs, StageOutput
from rapporteur_contracts.enums import JobStatus, ResourceKind
from rapporteur_workers.queue import Queue, QueueJob, migrate


def enqueue(
    queue: Queue, key: str = "test", priority: int = 0, max_attempts: int = 3, stage: str = "notes"
) -> UUID:
    return queue.enqueue(
        key=key,
        stage=stage,
        resource=ResourceKind.CPU,
        mode="dev",
        inputs=StageInputs(conference_id=None, talk_id=None, artifacts=[], config_hash="a" * 64),
        config={},
        priority=priority,
        max_attempts=max_attempts,
    )


def claim(queue: Queue) -> QueueJob:
    job = queue.claim(ResourceKind.CPU, "dev")
    assert job is not None
    return job


def output(job: QueueJob) -> StageOutput:
    return StageOutput(
        artifact=ArtifactRef(
            name="notes", storage_key="artifacts/notes/test.json", sha256="a" * 64
        ),
        meta=ArtifactMeta(
            stage="notes",
            stage_version="test",
            contract_version="0.2.0",
            cache_key="b" * 64,
            input_hashes=[],
            config_hash="a" * 64,
            produced_at=job.created_at,
            stub=True,
        ),
        warnings=["ЗАГЛУШКА"],
    )


def expire(queue: Queue, job_id: UUID) -> None:
    with psycopg.connect(queue.dsn) as conn:
        conn.execute(
            "UPDATE rapporteur_queue.jobs SET lease_until = "
            "clock_timestamp() - interval '1 second' WHERE id = %s",
            (job_id,),
        )


def make_ready(queue: Queue, job_id: UUID) -> None:
    with psycopg.connect(queue.dsn) as conn:
        conn.execute(
            "UPDATE rapporteur_queue.jobs SET available_at = "
            "clock_timestamp() - interval '1 second' WHERE id = %s",
            (job_id,),
        )


def test_migration_is_idempotent(dsn: str) -> None:
    migrate(dsn)
    Queue(dsn).check()


def test_unknown_migration_version_is_rejected(dsn: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE rapporteur_queue.schema_version SET version = 99")
    with pytest.raises(RuntimeError, match="Неизвестная версия"):
        migrate(dsn)


def test_enqueue_is_deduplicated_and_persistent(queue: Queue) -> None:
    job_id = enqueue(queue)
    assert enqueue(queue) == job_id
    other = Queue(queue.dsn)
    job = other.get(job_id)
    assert job is not None and job.status is JobStatus.QUEUED
    assert claim(other).id == job_id


def test_concurrent_workers_do_not_claim_same_job(queue: Queue) -> None:
    ids = {enqueue(queue, "one"), enqueue(queue, "two")}
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda _: claim(queue), range(2)))
    assert {job.id for job in jobs} == ids
    assert queue.claim(ResourceKind.CPU, "dev") is None


def test_locked_job_does_not_block_another(queue: Queue) -> None:
    first, second = enqueue(queue, "first"), enqueue(queue, "second")
    with psycopg.connect(queue.dsn) as conn:
        conn.execute("SELECT id FROM rapporteur_queue.jobs WHERE id = %s FOR UPDATE", (first,))
        assert claim(queue).id == second


def test_interactive_priority_precedes_stage_grouping(queue: Queue) -> None:
    enqueue(queue, "batch")
    interactive = enqueue(queue, "interactive", priority=100, stage="verify")
    assert queue.claim(ResourceKind.CPU, "dev", prefer_stage="notes").id == interactive  # type: ignore[union-attr]


def test_stage_grouping_only_breaks_priority_ties(queue: Queue) -> None:
    enqueue(queue, "other-stage", stage="verify")
    preferred = enqueue(queue, "preferred-stage")
    job = queue.claim(ResourceKind.CPU, "dev", prefer_stage="notes")
    assert job is not None and job.id == preferred


def test_expired_lease_requeues_with_new_token(queue: Queue) -> None:
    job_id = enqueue(queue)
    old = claim(queue)
    expire(queue, job_id)
    assert queue.claim(ResourceKind.CPU, "dev") is None  # backoff
    make_ready(queue, job_id)
    new = claim(queue)
    assert new.id == old.id and new.attempt == 2
    assert new.lease_token != old.lease_token
    assert not queue.heartbeat(old)
    assert queue.heartbeat(new)


def test_old_worker_cannot_publish_or_fail_new_attempt(queue: Queue) -> None:
    job_id = enqueue(queue)
    old = claim(queue)
    expire(queue, job_id)
    queue.claim(ResourceKind.CPU, "dev")
    make_ready(queue, job_id)
    new = claim(queue)
    calls: list[str] = []
    assert not queue.finish(old, output(old), lambda: calls.append("old"))
    assert not queue.fail(old, "old_error")
    assert queue.finish(new, output(new), lambda: calls.append("new"))
    assert calls == ["new"]
    assert not queue.finish(old, output(old), lambda: calls.append("old"))
    assert queue.get(job_id).status is JobStatus.SUCCEEDED  # type: ignore[union-attr]


def test_expired_worker_cannot_resurrect_itself(queue: Queue) -> None:
    enqueue(queue)
    job = claim(queue)
    expire(queue, job.id)
    assert not queue.heartbeat(job)
    assert not queue.finish(job, output(job), lambda: pytest.fail("published expired output"))


def test_cancellation_prevents_publication(queue: Queue) -> None:
    job_id = enqueue(queue)
    job = claim(queue)
    assert queue.cancel(job_id)
    assert not queue.heartbeat(job)
    assert not queue.finish(job, output(job), lambda: pytest.fail("published cancelled output"))
    assert queue.get(job_id).status is JobStatus.CANCELLED  # type: ignore[union-attr]


def test_queued_cancellation_is_terminal(queue: Queue) -> None:
    job_id = enqueue(queue)
    assert queue.cancel(job_id)
    assert not queue.cancel(job_id)
    assert queue.claim(ResourceKind.CPU, "dev") is None


def test_expired_cancel_request_is_not_retried(queue: Queue) -> None:
    job_id = enqueue(queue)
    claim(queue)
    queue.cancel(job_id)
    expire(queue, job_id)
    assert queue.claim(ResourceKind.CPU, "dev") is None
    assert queue.get(job_id).status is JobStatus.CANCELLED  # type: ignore[union-attr]


def test_invalid_input_is_not_retried(queue: Queue) -> None:
    job_id = enqueue(queue)
    job = claim(queue)
    assert queue.fail(job, "invalid_input")
    make_ready(queue, job_id)
    assert queue.claim(ResourceKind.CPU, "dev") is None
    assert queue.get(job_id).attempt == 1  # type: ignore[union-attr]


def test_transient_retry_is_delayed_and_bounded(queue: Queue) -> None:
    job_id = enqueue(queue, max_attempts=2)
    assert queue.fail(claim(queue), "network", transient=True)
    assert queue.claim(ResourceKind.CPU, "dev") is None
    make_ready(queue, job_id)
    assert queue.fail(claim(queue), "network", transient=True)
    make_ready(queue, job_id)
    assert queue.claim(ResourceKind.CPU, "dev") is None
    assert queue.get(job_id).status is JobStatus.FAILED  # type: ignore[union-attr]


def test_repeated_worker_deaths_are_bounded(queue: Queue) -> None:
    job_id = enqueue(queue, max_attempts=1)
    claim(queue)
    expire(queue, job_id)
    assert queue.claim(ResourceKind.CPU, "dev") is None
    assert queue.get(job_id).status is JobStatus.FAILED  # type: ignore[union-attr]


def test_dev_tasks_never_reach_gpu_mode_worker(queue: Queue) -> None:
    enqueue(queue)
    assert queue.claim(ResourceKind.CPU, "gpu") is None
    assert queue.claim(ResourceKind.GPU, "dev") is None


def test_failed_publication_is_not_success(queue: Queue) -> None:
    job_id = enqueue(queue)
    job = claim(queue)
    with pytest.raises(OSError):
        queue.finish(job, output(job), lambda: (_ for _ in ()).throw(OSError("disk")))
    state = queue.get(job_id)
    assert state is not None and state.status is JobStatus.RUNNING and state.output is None


@pytest.mark.parametrize("action", ["heartbeat", "finish", "fail"])
def test_waiting_for_lock_does_not_revive_expired_lease(queue: Queue, action: str) -> None:
    short = Queue(queue.dsn, lease_seconds=0.5)
    enqueue(short)
    job = claim(short)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with psycopg.connect(queue.dsn) as conn:
            conn.execute("SELECT id FROM rapporteur_queue.jobs WHERE id = %s FOR UPDATE", (job.id,))
            if action == "heartbeat":
                pending = pool.submit(short.heartbeat, job)
            elif action == "fail":
                pending = pool.submit(short.fail, job, "late_error")
            else:
                pending = pool.submit(
                    short.finish,
                    job,
                    output(job),
                    lambda: pytest.fail("published after waiting past lease"),
                )
            time.sleep(0.8)
        assert not pending.result(timeout=5)
