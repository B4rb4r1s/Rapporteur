import hashlib
import importlib
import io
import shutil
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine

from rapporteur_backend.app import create_app
from rapporteur_contracts import ConferenceRegistry, source_file_id
from rapporteur_pipeline.storage import blob_key


@pytest.fixture
def materials_client(database: Engine, tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(database_url=database.url, storage_dir=tmp_path)) as client:
        yield client


def conference(client: TestClient) -> str:
    response = client.post("/api/v1/conferences", json={"title": "Конференция"})
    assert response.status_code == 201, response.text
    return f"/api/v1/conferences/{response.json()['id']}"


def upload(client: TestClient, base: str, name: str, data: bytes) -> Any:
    return client.post(
        f"{base}/materials",
        params={"path": name},
        headers={"Content-Type": "application/octet-stream"},
        content=data,
    )


def pdf() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), color="white").save(buffer, format="PDF")
    return buffer.getvalue()


def test_title_and_duplicate_presentation_format(materials_client: TestClient) -> None:
    client = materials_client
    base = conference(client)
    presentation = importlib.import_module("pptx").Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[0])
    slide.shapes.title.text = "Точность моделей"
    slide.placeholders[1].text = "Докладчик: Иванов И. И."
    buffer = io.BytesIO()
    presentation.save(buffer)
    primary = upload(client, base, "01_Иванов/доклад.pptx", buffer.getvalue())
    assert primary.status_code == 201, primary.text
    alternate = upload(client, base, "01_Иванов/доклад.pdf", pdf())
    assert alternate.status_code == 201, alternate.text
    snapshot = client.get(f"{base}/materials").json()
    assert len(snapshot["talks"]) == 1
    assert snapshot["talks"][0]["title"] == "Точность моделей"
    files = {item["source"]["id"]: item for item in snapshot["files"]}
    pptx, pdf_version = files[primary.json()["id"]], files[alternate.json()["id"]]
    assert pptx["title_candidates"]["speakers"] == ["Иванов И. И."]
    assert pptx["confidence"] == 0.98
    assert pdf_version["alternate_of"] == pptx["source"]["id"]


def test_content_deduplicated_on_disk_and_per_conference(
    materials_client: TestClient, tmp_path: Path
) -> None:
    client = materials_client
    first, second = conference(client), conference(client)
    data = "Результаты обсуждения".encode("cp1251")
    original = upload(client, first, "неверное расширение.pdf", data)
    assert original.status_code == 201, original.text
    same = upload(client, first, "заметки.txt", data)
    assert same.status_code == 200 and same.json() == original.json()
    other = upload(client, second, "другая папка/заметки.txt", data)
    assert other.status_code == 201 and other.json()["id"] != original.json()["id"]
    assert other.json()["kind"] == "notes" and other.json()["encoding"] == "cp1251"
    assert (tmp_path / blob_key(hashlib.sha256(data).hexdigest())).read_bytes() == data
    assert len(list((tmp_path / "blobs").glob("*/*"))) == 1
    assert not list((tmp_path / "tmp").iterdir())
    # id файла вычисляется из конференции и SHA-256, как в этапе pack (ADR-013).
    sha = hashlib.sha256(data).hexdigest()
    assert original.json()["id"] == str(source_file_id(UUID(first.rsplit("/", 1)[1]), sha))
    assert other.json()["id"] == str(source_file_id(UUID(second.rsplit("/", 1)[1]), sha))


def test_confirm_and_reassign_materials(materials_client: TestClient) -> None:
    client = materials_client
    base = conference(client)
    slides = upload(client, base, "Модели/01_Иванов/доклад.bin", pdf())
    assert slides.status_code == 201, slides.text
    assert slides.json()["kind"] == "presentation"
    notes = upload(client, base, "Модели/02_Петров/заметки.txt", b"Listener notes")
    assert notes.status_code == 201, notes.text
    loose = upload(client, base, "_общее/заметки.txt", b"General notes")
    assert loose.status_code == 201, loose.text
    snapshot = client.get(f"{base}/materials")
    review = snapshot.json()
    first, second = review["talks"]
    files = {item["source"]["id"]: item for item in review["files"]}
    assert files[slides.json()["id"]]["suggested_talk_id"] == first["id"]
    assert files[loose.json()["id"]]["suggested_talk_id"] is None
    choices = [
        {"source_file_id": slides.json()["id"], "talk_id": second["id"]},
        {"source_file_id": notes.json()["id"], "talk_id": second["id"]},
        {"source_file_id": loose.json()["id"], "talk_id": None},
    ]
    headers = {"If-Match": snapshot.headers["etag"]}
    endpoint = f"{base}/materials/bindings"
    saved = client.put(endpoint, headers=headers, json={"bindings": choices})
    assert saved.status_code == 200, saved.text
    assert [talk["status"] for talk in saved.json()["talks"]] == ["insufficient_data", "matched"]
    assert client.put(endpoint, headers=headers, json={"bindings": choices}).status_code == 409
    export = client.get(f"{base}/conference.yaml")
    assert export.headers["content-disposition"] == 'attachment; filename="conference.yaml"'
    registry = ConferenceRegistry.model_validate(yaml.safe_load(export.text))
    assert not registry.talks[0].sources.slides
    assert registry.talks[1].sources.slides == ["Модели/01_Иванов/доклад.bin"]
    assert registry.talks[1].sources.notes == ["Модели/02_Петров/заметки.txt"]
    assert registry.talks[1].confidence is None
    assert client.get(f"{base}/materials").json() == saved.json()


def test_file_size_format_and_path_errors_are_isolated(materials_client: TestClient) -> None:
    client = materials_client
    base = conference(client)
    large = upload(client, base, "заметки.txt", b"a" * (5 * 2**20 + 1))
    assert large.status_code == 413 and large.json()["details"]["file_code"] == "file_too_large"
    broken = upload(client, base, "bad.pdf", b"%PDF-1.7\nbad")
    assert broken.status_code == 422 and broken.json()["details"]["file_code"] == "broken_file"
    assert upload(client, base, "../escape.txt", b"Notes").status_code == 422
    assert upload(client, base, r"..\escape.txt", b"Notes").status_code == 422
    good = upload(client, base, "заметки.txt", b"Valid notes")
    assert good.status_code == 201, good.text
    assert upload(client, base, "заметки.txt", b"Different notes").status_code == 409
    assert len(client.get(f"{base}/materials").json()["files"]) == 1


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="Нужен ffprobe")
def test_recording_duration_from_content(materials_client: TestClient) -> None:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as recording:
        recording.setparams((1, 2, 8000, 0, "NONE", ""))
        recording.writeframes(b"\x00" * 16_000)
    base = conference(materials_client)
    response = upload(materials_client, base, "запись.bin", buffer.getvalue())
    assert response.status_code == 201, response.text
    assert (response.json()["kind"], response.json()["duration_ms"]) == ("audio", 1000)
