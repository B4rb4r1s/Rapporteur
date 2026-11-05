from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from rapporteur_backend.app import create_app
from rapporteur_backend.cli import main
from rapporteur_backend.processing import load_state, run_scheduler
from rapporteur_contracts import InventoryArtifact
from rapporteur_contracts.enums import HumanGate, ResourceKind
from rapporteur_pipeline.graph import load_graph
from rapporteur_pipeline.storage import DirectoryStorage
from rapporteur_workers.queue import Queue, migrate
from rapporteur_workers.runtime import execute
from rapporteur_workers.scheduler import plan


@pytest.fixture
def queue(database: Engine) -> Queue:
    dsn = database.url.set(drivername="postgresql").render_as_string(hide_password=False)
    migrate(dsn)
    return Queue(dsn)


@pytest.fixture
def processing_client(database: Engine, tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(database_url=database.url, storage_dir=tmp_path)) as client:
        yield client


def create_conference(client: TestClient) -> tuple[UUID, str]:
    result = client.post("/api/v1/conferences", json={"title": "Тест"})
    assert result.status_code == 201, result.text
    conference_id = UUID(result.json()["id"])
    return conference_id, f"/api/v1/conferences/{conference_id}"


def upload_notes(client: TestClient, url: str, name: str) -> UUID:
    result = client.post(
        f"{url}/materials",
        params={"path": name},
        headers={"Content-Type": "application/octet-stream"},
        content=f"Заметки к докладу {name}".encode(),
    )
    assert result.status_code == 201, result.text
    return UUID(result.json()["id"])


def test_schedule_cli_uses_persisted_request_and_real_inventory(
    database: Engine,
    queue: Queue,
    processing_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = processing_client
    conference_id, url = create_conference(client)
    assert client.post(f"{url}/processing").status_code == 422
    source_id = upload_notes(client, url, "01_Иванов/notes.txt")
    inactive, inactive_url = create_conference(client)
    upload_notes(client, inactive_url, "other.txt")
    assert client.post(f"{url}/processing").status_code == 202
    again = client.post(f"{url}/processing")
    assert again.status_code == 202 and again.json()["status_url"].endswith(str(conference_id))
    assert client.get(f"{url}/materials").json()["processing_started"]

    monkeypatch.setenv("DATABASE_URL", database.url.render_as_string(hide_password=False))
    args = ["schedule", "--mode", "gpu", "--once"]
    assert main(args) == main(args) == 0
    assert len(queue.for_conference(conference_id, "gpu")) == 1
    assert not queue.for_conference(inactive, "gpu")
    storage = DirectoryStorage(tmp_path)
    job = queue.claim(ResourceKind.CPU, "gpu")
    assert job is not None and job.stage == "inventory"
    assert execute(queue, job, storage, tmp_path / "health")
    complete = queue.get(job.id)
    assert complete is not None and complete.output is not None
    artifact = InventoryArtifact.model_validate_json(
        storage.get(complete.output.artifact.storage_key)
    )
    assert not artifact.meta.stub
    assert [(f.original_name, f.kind.value, f.encoding) for f in artifact.files] == [
        ("notes.txt", "notes", "utf-8")
    ]

    assert main(args) == 0
    job = queue.claim(ResourceKind.CPU, "gpu")
    assert job is not None and job.stage == "notes"
    assert execute(queue, job, storage, tmp_path / "health")
    assert main(args) == 0 and len(queue.for_conference(conference_id, "gpu")) == 2

    review = client.get(f"{url}/materials")
    talk_id = UUID(review.json()["talks"][0]["id"])
    assert (
        client.put(
            f"{url}/materials/bindings",
            headers={"If-Match": review.headers["etag"]},
            json={"bindings": [{"source_file_id": str(source_id), "talk_id": str(talk_id)}]},
        ).status_code
        == 200
    )
    with database.connect() as conn:
        state = load_state(conn, conference_id)
    assert state.gates == {HumanGate.BINDING_CONFIRMED}
    assert state.talks[0].file_ids == [source_id]
    upload_notes(client, url, "another.txt")
    with database.connect() as conn:
        assert not load_state(conn, conference_id).gates
    assert main(args) == 0
    refreshed = queue.claim(ResourceKind.CPU, "gpu")
    assert refreshed is not None and refreshed.stage == "inventory"
    assert len(refreshed.inputs.artifacts) == 2


def test_background_scheduler_progresses_after_worker_completion(
    database: Engine, queue: Queue, processing_client: TestClient, tmp_path: Path
) -> None:
    cid, url = create_conference(processing_client)
    upload_notes(processing_client, url, "notes.txt")
    assert processing_client.post(f"{url}/processing").status_code == 202
    stop = Event()
    graph = load_graph()
    with ThreadPoolExecutor(max_workers=1) as pool:
        scheduled = pool.submit(run_scheduler, database, queue, "dev", graph, stop, 0.02)
        try:
            stages: list[str] = []
            deadline = monotonic() + 8
            while len(stages) < 2 and monotonic() < deadline:
                if job := queue.claim(ResourceKind.CPU, "dev"):
                    assert execute(queue, job, DirectoryStorage(tmp_path), tmp_path / "health")
                    stages.append(job.stage)
                else:
                    sleep(0.02)
            assert stages == ["inventory", "notes"]
        finally:
            stop.set()
        scheduled.result(timeout=8)
    with database.connect() as conn:
        state = load_state(conn, cid)
    results = plan(graph, state, queue.for_conference(cid, "dev"), "dev")
    assert [item.stage for item in results.decisions if item.state == "complete"] == stages


def test_background_scheduler_picks_up_binding_confirmation(
    database: Engine, queue: Queue, processing_client: TestClient, tmp_path: Path
) -> None:
    cid, url = create_conference(processing_client)
    source = upload_notes(processing_client, url, "01_Иванов/notes.txt")
    assert processing_client.post(f"{url}/processing").status_code == 202
    graph = load_graph()
    # Используем исполняемый этап для проверки интеграции шлюза, пока facts не в реестре.
    next(stage for stage in graph.stages if stage.name == "notes").gates.append(
        HumanGate.BINDING_CONFIRMED
    )
    stop = Event()
    storage = DirectoryStorage(tmp_path)
    with ThreadPoolExecutor(max_workers=1) as pool:
        scheduled = pool.submit(run_scheduler, database, queue, "dev", graph, stop, 0.02)
        try:
            deadline = monotonic() + 8
            while not (job := queue.claim(ResourceKind.CPU, "dev")) and monotonic() < deadline:
                sleep(0.02)
            assert job is not None and job.stage == "inventory"
            assert execute(queue, job, storage, tmp_path / "health")
            with database.connect() as conn:
                state = load_state(conn, cid)
            results = plan(graph, state, queue.for_conference(cid, "dev"), "dev")
            assert any(
                item.stage == "notes" and item.reason == "human_gates_not_confirmed"
                for item in results.decisions
            )
            review = processing_client.get(f"{url}/materials")
            assert (
                processing_client.put(
                    f"{url}/materials/bindings",
                    headers={"If-Match": review.headers["etag"]},
                    json={"bindings": [{"source_file_id": str(source), "talk_id": None}]},
                ).status_code
                == 200
            )
            deadline = monotonic() + 8
            while not (job := queue.claim(ResourceKind.CPU, "dev")) and monotonic() < deadline:
                sleep(0.02)
            assert job is not None and job.stage == "notes"
            assert execute(queue, job, storage, tmp_path / "health")
        finally:
            stop.set()
        scheduled.result(timeout=8)
