"""Сводки через платформу в профиле dev (ADR-013): обработка, перенос результатов, правка,
подтверждение, сборка справки и выгрузка. Задачи исполняет тот же код, что и воркеры."""

import importlib
import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from rapporteur_backend import report_routes
from rapporteur_backend.app import create_app
from rapporteur_backend.processing import advance_started
from rapporteur_contracts.enums import ReportFormat, ResourceKind
from rapporteur_pipeline.graph import load_graph
from rapporteur_pipeline.report.stage import ReportConfig
from rapporteur_pipeline.storage import DirectoryStorage
from rapporteur_workers.queue import Queue, migrate
from rapporteur_workers.runtime import execute


@pytest.fixture
def queue(database: Engine) -> Queue:
    dsn = database.url.set(drivername="postgresql").render_as_string(hide_password=False)
    migrate(dsn)
    return Queue(dsn)


@pytest.fixture
def storage(tmp_path: Path) -> DirectoryStorage:
    (tmp_path / "data").mkdir()
    return DirectoryStorage(tmp_path / "data")


@pytest.fixture
def client(database: Engine, tmp_path: Path, storage: DirectoryStorage) -> Iterator[TestClient]:
    app = create_app(database_url=database.url, storage_dir=tmp_path / "data", mode="dev")
    with TestClient(app) as client:
        yield client


@pytest.fixture(autouse=True)
def without_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    # В образе backend нет LibreOffice; PDF проверяют тесты pipeline.
    fast = ReportConfig(formats=(ReportFormat.DOCX, ReportFormat.MARKDOWN))
    monkeypatch.setattr(report_routes, "ReportConfig", lambda: fast)


def presentation() -> bytes:
    deck = importlib.import_module("pptx").Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[0])
    slide.shapes.title.text = "Кэширование запросов"
    slide.placeholders[1].text = "Докладчик: Иванов И. И."
    buffer = io.BytesIO()
    deck.save(buffer)
    return buffer.getvalue()


def run_until_idle(
    database: Engine, queue: Queue, storage: DirectoryStorage, tmp_path: Path
) -> None:
    """Планировщик и воркеры по очереди, пока задачи не кончатся; затем перенос результатов."""
    for _ in range(20):
        advance_started(database, queue, "dev", load_graph(), storage)
        progressed = False
        for resource in (ResourceKind.CPU, ResourceKind.GPU):
            while (job := queue.claim(resource, "dev")) is not None:
                assert execute(queue, job, storage, tmp_path / "health"), job.stage
                progressed = True
        if not progressed:
            return
    raise AssertionError("обработка не завершилась")


def processed_talk(
    client: TestClient, database: Engine, queue: Queue, storage: DirectoryStorage, tmp_path: Path
) -> tuple[str, str]:
    conference = client.post("/api/v1/conferences", json={"title": "Тест сводок"}).json()
    base = f"/api/v1/conferences/{conference['id']}"
    upload = client.post(
        f"{base}/materials",
        params={"path": "Секция 1/01_Иванов/доклад.pptx"},
        headers={"Content-Type": "application/octet-stream"},
        content=presentation(),
    )
    assert upload.status_code == 201, upload.text
    review = client.get(f"{base}/materials")
    bindings = [
        {"source_file_id": item["source"]["id"], "talk_id": item["suggested_talk_id"]}
        for item in review.json()["files"]
    ]
    saved = client.put(
        f"{base}/materials/bindings",
        headers={"If-Match": review.headers["etag"]},
        json={"bindings": bindings},
    )
    assert saved.status_code == 200, saved.text
    assert client.post(f"{base}/processing").status_code == 202
    run_until_idle(database, queue, storage, tmp_path)
    return conference["id"], bindings[0]["talk_id"]


def edit(summary: dict[str, Any], sentence: str, claim_text: str | None = None) -> dict[str, Any]:
    key = summary["sentences"][0]["claim_ids"][0]
    return {
        "sentences": [{"text": sentence, "claim_ids": [key]}],
        "conclusions": summary["conclusions"],
        "claims": [{"key": key, "text": claim_text}] if claim_text else [],
    }


def test_processing_brings_summary_and_progress(
    client: TestClient, database: Engine, queue: Queue, storage: DirectoryStorage, tmp_path: Path
) -> None:
    conference_id, talk_id = processed_talk(client, database, queue, storage, tmp_path)

    response = client.get(f"/api/v1/conferences/{conference_id}/progress")
    assert response.status_code == 200, response.text
    progress = response.json()
    (talk,) = progress["talks"]
    assert talk["id"] == talk_id and talk["status"] == "processed"
    steps = ("pack", "facts", "verify", "summary")
    assert talk["stages"] == {stage: "succeeded" for stage in steps}
    assert (talk["summary_number"], talk["summary_status"]) == (1, "ready")
    assert progress["stages"]["slides"] == {"succeeded": 1}

    # Повторный проход не создаёт вторую версию той же сводки.
    advance_started(database, queue, "dev", load_graph(), storage)
    response = client.get(f"/api/v1/talks/{talk_id}/summary")
    assert response.status_code == 200 and response.headers["etag"] == '"1"'
    view = response.json()
    assert view["talk_status"] == "processed"
    assert view["summary"]["version"] == 1 and view["summary"]["edited_by"] is None

    evidence = view["summary"]["claims"][0]["evidence"][0]
    fragment = next(f for f in view["pack"]["fragments"] if f["label"] == evidence["label"])
    image = client.get(
        f"/api/v1/source-files/{fragment['source_file_id']}/slides/{fragment['locator']['page_no']}"
    )
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert image.content.startswith(b"\x89PNG")
    missing = client.get(f"/api/v1/source-files/{fragment['source_file_id']}/slides/99")
    assert missing.status_code == 404


def test_edit_and_review_create_versions(
    client: TestClient, database: Engine, queue: Queue, storage: DirectoryStorage, tmp_path: Path
) -> None:
    _, talk_id = processed_talk(client, database, queue, storage, tmp_path)
    url = f"/api/v1/talks/{talk_id}/summary"
    summary = client.get(url).json()["summary"]

    stale = client.put(url, headers={"If-Match": '"2"'}, json=edit(summary, "Новый текст."))
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"
    unknown = edit(summary, "Новый текст.")
    unknown["sentences"][0]["claim_ids"] = ["c99"]
    rejected = client.put(url, headers={"If-Match": '"1"'}, json=unknown)
    assert rejected.status_code == 422, rejected.text

    edited = client.put(
        url, headers={"If-Match": '"1"'}, json=edit(summary, "Новый текст.", "Тема доклада.")
    )
    assert edited.status_code == 200, edited.text
    assert edited.headers["etag"] == '"2"'
    body = edited.json()["summary"]
    assert body["sentences"][0]["text"] == "Новый текст."
    assert body["claims"][0]["text"] == "Тема доклада."
    assert body["claims"][0]["status"] == "needs_human"
    assert body["edited_by"] is not None

    reviewed = client.post(f"{url}/review", headers={"If-Match": '"2"'})
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["talk_status"] == "reviewed"
    assert reviewed.json()["summary"]["claims"][0]["status"] == "confirmed"

    # Правка проверенной сводки возвращает доклад в processed (spec §6.8).
    again = client.put(url, headers={"If-Match": '"3"'}, json=edit(summary, "Ещё правка."))
    assert again.status_code == 200 and again.json()["talk_status"] == "processed"
    assert client.get(f"/api/v1/talks/{talk_id}").json()["status"] == "processed"


def test_report_is_built_from_current_summaries(
    client: TestClient, database: Engine, queue: Queue, storage: DirectoryStorage, tmp_path: Path
) -> None:
    conference_id, talk_id = processed_talk(client, database, queue, storage, tmp_path)
    summary_url = f"/api/v1/talks/{talk_id}/summary"
    summary = client.get(summary_url).json()["summary"]
    edited = client.put(
        summary_url, headers={"If-Match": '"1"'}, json=edit(summary, "Текст после правки.")
    )
    assert edited.status_code == 200
    selection = {"days": [], "section_ids": [], "talk_ids": [talk_id]}
    report = client.post(
        f"/api/v1/conferences/{conference_id}/reports", json={"selection": selection}
    ).json()
    build = f"/api/v1/reports/{report['id']}/build"

    unreviewed = client.post(build, json={})
    assert unreviewed.status_code == 422 and "непроверенные" in unreviewed.json()["message"]
    undated = client.post(build, json={"unreviewed_confirmed": True})
    assert undated.status_code == 422 and "даты" in undated.json()["message"]

    conference = client.get(f"/api/v1/conferences/{conference_id}")
    dated = client.put(
        f"/api/v1/conferences/{conference_id}",
        headers={"If-Match": conference.headers["etag"]},
        json={"title": "Тест сводок", "start_date": "2026-10-20", "end_date": "2026-10-20"},
    )
    assert dated.status_code == 200, dated.text
    accepted = client.post(build, json={"unreviewed_confirmed": True})
    assert accepted.status_code == 202, accepted.text
    repeated = client.post(build, json={"unreviewed_confirmed": True})
    assert repeated.json()["job_id"] == accepted.json()["job_id"]

    run_until_idle(database, queue, storage, tmp_path)
    versions = client.get(f"/api/v1/reports/{report['id']}/versions").json()["items"]
    assert len(versions) == 1 and versions[0]["number"] == 1
    assert {file["format"] for file in versions[0]["files"]} == {"docx", "md"}
    markdown = client.get(f"/api/v1/report-versions/{versions[0]['id']}/files/md")
    assert markdown.status_code == 200
    assert "Текст после правки." in markdown.text
    assert "attachment" in markdown.headers["content-disposition"]
    absent = client.get(f"/api/v1/report-versions/{versions[0]['id']}/files/pdf")
    assert absent.status_code == 404
    assert UUID(versions[0]["id"]) == UUID(accepted.json()["job_id"])


def test_deleting_a_conference_cancels_its_jobs(
    client: TestClient, database: Engine, queue: Queue, storage: DirectoryStorage
) -> None:
    conference = client.post("/api/v1/conferences", json={"title": "Удаляемая"})
    base = f"/api/v1/conferences/{conference.json()['id']}"
    client.post(
        f"{base}/materials",
        params={"path": "Иванов/доклад.pptx"},
        headers={"Content-Type": "application/octet-stream"},
        content=presentation(),
    )
    assert client.post(f"{base}/processing").status_code == 202
    advance_started(database, queue, "dev", load_graph(), storage)
    (job,) = queue.for_conference(UUID(conference.json()["id"]), "dev")
    assert job.status.value == "queued"

    deleted = client.delete(base, headers={"If-Match": client.get(base).headers["etag"]})

    assert deleted.status_code == 204
    cancelled = queue.get(job.id)
    assert cancelled is not None and cancelled.status.value == "cancelled"
    assert client.get(base).status_code == 404


def test_system_shows_profiles_templates_and_queue(client: TestClient, queue: Queue) -> None:
    info = client.get("/api/v1/system")

    assert info.status_code == 200, info.text
    body = info.json()
    assert body["mode"] == "dev" and body["migration"] == "0004_summaries"
    assert [item["name"] for item in body["profiles"] if item["default"]] == ["local"]
    assert {"name": "starter", "version": "starter-1"} in body["templates"]
    assert body["queue"] == {}
