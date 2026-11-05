import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from rapporteur_contracts import NotesArtifact, StageInputs, StageOutput, TranscriptArtifact
from rapporteur_contracts.enums import JobStatus, ResourceKind
from rapporteur_pipeline.stage import StageContext
from rapporteur_pipeline.storage import DirectoryStorage
from rapporteur_pipeline.stubs import StubNotesStage
from rapporteur_workers import runtime
from rapporteur_workers.cli import database_dsn, enqueue_file, smoke
from rapporteur_workers.queue import Queue
from rapporteur_workers.runtime import BufferedStorage, execute, healthy, run, write_health


def test_health_is_fresh_and_belongs_to_worker(tmp_path: Path) -> None:
    path = tmp_path / "health.json"
    assert not healthy(path, ResourceKind.CPU, "dev")
    write_health(path, ResourceKind.CPU, "dev")
    assert healthy(path, ResourceKind.CPU, "dev")
    assert not healthy(path, ResourceKind.GPU, "dev")
    assert not healthy(path, ResourceKind.CPU, "gpu")
    path.write_text(json.dumps({"at": time.time() - 100, "resource": "cpu", "mode": "dev"}))
    assert not healthy(path, ResourceKind.CPU, "dev")
    path.write_text("broken")
    assert not healthy(path, ResourceKind.CPU, "dev")


def test_missing_database_config_names_missing_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ["DATABASE_URL", "PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD"]:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match="PGPASSWORD"):
        database_dsn()


def test_buffer_does_not_publish_until_commit(tmp_path: Path) -> None:
    target = DirectoryStorage(tmp_path / "target")
    target.put("blobs/source", b"original")
    pending = BufferedStorage(target, tmp_path / "pending")
    pending.put("artifacts/result.json", b"result")
    assert pending.get("blobs/source") == b"original"
    assert pending.get("artifacts/result.json") == b"result"
    assert not target.exists("artifacts/result.json")
    with pytest.raises(ValueError):
        pending.delete("blobs/source")
    pending.publish()
    assert target.get("artifacts/result.json") == b"result"


@pytest.mark.parametrize("stage,resource", [("notes", ResourceKind.CPU), ("asr", ResourceKind.GPU)])
def test_dev_job_produces_marked_artifact(
    queue: Queue, tmp_path: Path, stage: str, resource: ResourceKind
) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"synthetic input")
    storage = DirectoryStorage(tmp_path / "data")
    job_id = enqueue_file(queue, storage, stage, "dev", source)
    assert enqueue_file(queue, storage, stage, "dev", source) == job_id
    job = queue.claim(resource, "dev")
    assert job is not None
    assert execute(queue, job, storage, tmp_path / "health")
    result = queue.get(job_id)
    assert result is not None and result.output is not None
    assert result.output.meta.stub
    content = storage.get(result.output.artifact.storage_key)
    artifact = (NotesArtifact if stage == "notes" else TranscriptArtifact).model_validate_json(
        content
    )
    assert artifact.meta.stub


def test_gpu_mode_executes_real_notes_without_stub(queue: Queue, tmp_path: Path) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("Проверка настоящего разбора заметок.", encoding="utf-8")
    storage = DirectoryStorage(tmp_path / "data")
    job_id = enqueue_file(queue, storage, "notes", "gpu", source)
    assert queue.claim(ResourceKind.CPU, "dev") is None
    job = queue.claim(ResourceKind.CPU, "gpu")
    assert job is not None and execute(queue, job, storage, tmp_path / "health")
    result = queue.get(job_id)
    assert result is not None and result.output is not None and not result.output.meta.stub
    artifact = NotesArtifact.model_validate_json(storage.get(result.output.artifact.storage_key))
    assert artifact.fragments[0].raw_text == source.read_text(encoding="utf-8")


def test_unsupported_real_stage_refused_before_file_access(queue: Queue, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Нет реализации"):
        enqueue_file(queue, DirectoryStorage(tmp_path), "asr", "gpu", tmp_path / "missing")


def test_cancelled_running_stage_never_publishes(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready, release = threading.Event(), threading.Event()

    class BlockingStage(StubNotesStage):
        def run(self, ctx: StageContext, inputs: StageInputs) -> StageOutput:
            output = super().run(ctx, inputs)
            ready.set()
            assert release.wait(5)
            return output

    source = tmp_path / "notes.txt"
    source.write_text("synthetic")
    storage = DirectoryStorage(tmp_path / "data")
    job_id = enqueue_file(queue, storage, "notes", "dev", source)
    job = queue.claim(ResourceKind.CPU, "dev")
    assert job is not None
    monkeypatch.setattr(runtime, "get_stage", lambda _name, _mode: BlockingStage())
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(execute, queue, job, storage, tmp_path / "health")
        assert ready.wait(5)
        queue.cancel(job_id)
        release.set()
        assert not result.result(timeout=5)
    state = queue.get(job_id)
    assert state is not None and state.status is JobStatus.CANCELLED
    assert not list((tmp_path / "data").glob("artifacts/**/*"))


def test_heartbeat_keeps_slow_stage_alive(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class SlowStage(StubNotesStage):
        def run(self, ctx: StageContext, inputs: StageInputs) -> StageOutput:
            time.sleep(0.8)
            return super().run(ctx, inputs)

    short = Queue(queue.dsn, lease_seconds=0.4)
    source = tmp_path / "notes.txt"
    source.write_text("synthetic")
    storage = DirectoryStorage(tmp_path / "data")
    enqueue_file(short, storage, "notes", "dev", source)
    job = short.claim(ResourceKind.CPU, "dev")
    assert job is not None
    monkeypatch.setattr(runtime, "get_stage", lambda _name, _mode: SlowStage())
    health = tmp_path / "health"
    assert execute(short, job, storage, health)
    assert healthy(health, ResourceKind.CPU, "dev")


def test_two_workers_complete_cli_smoke(queue: Queue, tmp_path: Path) -> None:
    storage = DirectoryStorage(tmp_path / "data")
    stop = threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        workers = [
            pool.submit(
                run,
                queue,
                storage,
                resource,
                "dev",
                tmp_path / resource.value,
                stop,
                poll_seconds=0.02,
            )
            for resource in ResourceKind
        ]
        try:
            assert smoke(queue, storage, "dev", timeout=10) == 0
        finally:
            stop.set()
            for worker in workers:
                worker.result(timeout=5)


def test_cli_processes_migrate_work_and_report_health(dsn: str, tmp_path: Path) -> None:
    env = {**os.environ, "DATABASE_URL": dsn, "PYTHONIOENCODING": "utf-8"}
    command = [
        sys.executable,
        "-m",
        "rapporteur_workers.cli",
        "--mode",
        "dev",
        "--data-dir",
        str(tmp_path / "data"),
    ]
    migrated = subprocess.run([*command, "migrate"], env=env, capture_output=True, timeout=10)
    assert migrated.returncode == 0, migrated.stderr.decode("utf-8")
    processes: list[subprocess.Popen[bytes]] = []
    try:
        for resource in ResourceKind:
            processes.append(
                subprocess.Popen(
                    [
                        *command,
                        "run",
                        "--resource",
                        resource.value,
                        "--health-file",
                        str(tmp_path / f"health-{resource.value}"),
                    ],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
            )
        result = subprocess.run([*command, "smoke"], env=env, capture_output=True, timeout=15)
        assert result.returncode == 0, result.stdout.decode("utf-8")
        assert b"smoke_passed" in result.stdout
        for resource in ResourceKind:
            health = subprocess.run(
                [
                    *command,
                    "health",
                    "--resource",
                    resource.value,
                    "--health-file",
                    str(tmp_path / f"health-{resource.value}"),
                ],
                env=env,
                timeout=10,
            )
            assert health.returncode == 0
    finally:
        for process in processes:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
