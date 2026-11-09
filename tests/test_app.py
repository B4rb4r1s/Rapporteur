"""Приложение целиком: API, SQLite, фоновая обработка и сборка справки (ADR-014).

Модель заменена ScriptedClient, остальное настоящее: разбор pptx через LibreOffice, сверка
цитат, проверки сводки, DOCX, Markdown и PDF.
"""

import hashlib
import io
import time
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml
from fastapi.testclient import TestClient
from PIL import Image

from rapporteur.app import create_app
from rapporteur.contracts import ConferenceRegistry, source_file_id
from rapporteur.llm.client import ScriptedClient
from rapporteur.processing import Processor
from rapporteur.storage import blob_key

from .conftest import SlideSpec, make_pptx
from .fictional_talk import NOTE, SLIDES, facts_answer, summary_answer


class Fixture:
    def __init__(self, client: TestClient, scripted: ScriptedClient, data: Path) -> None:
        self.client = client
        self.scripted = scripted
        self.data = data

    @property
    def processor(self) -> Processor:
        processor: Processor = self.client.app.state.processor  # type: ignore[attr-defined]
        return processor

    def conference(self, title: str = "Конференция") -> str:
        response = self.client.post("/api/v1/conferences", json={"title": title})
        assert response.status_code == 201, response.text
        return f"/api/v1/conferences/{response.json()['id']}"

    def upload(self, base: str, path: str, data: bytes) -> Any:
        return self.client.post(
            f"{base}/materials",
            params={"path": path},
            headers={"Content-Type": "application/octet-stream"},
            content=data,
        )

    def confirm_suggestions(self, base: str) -> dict[str, Any]:
        review = self.client.get(f"{base}/materials").json()
        choices = [
            {"source_file_id": item["source"]["id"], "talk_id": item["suggested_talk_id"]}
            for item in review["files"]
        ]
        saved = self.client.put(f"{base}/materials/bindings", json={"bindings": choices})
        assert saved.status_code == 200, saved.text
        result: dict[str, Any] = saved.json()
        return result


@pytest.fixture
def app(tmp_path: Path) -> Iterator[Fixture]:
    scripted = ScriptedClient([])
    application = create_app(tmp_path, client=lambda _profile: scripted, process=False)
    with TestClient(application) as client:
        yield Fixture(client, scripted, tmp_path)


def pdf() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), color="white").save(buffer, format="PDF")
    return buffer.getvalue()


def titled_pptx(title: str, subtitle: str | None = None) -> bytes:
    import pptx

    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[0])
    slide.shapes.title.text = title
    if subtitle is not None:
        slide.placeholders[1].text = subtitle
    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def recording() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", ""))
        audio.writeframes(b"\x00" * 16_000)
    return buffer.getvalue()


# --- материалы ------------------------------------------------------------------------------


def test_title_and_duplicate_presentation_format(app: Fixture) -> None:
    base = app.conference()
    primary = app.upload(
        base, "01_Иванов/доклад.pptx", titled_pptx("Точность моделей", "Докладчик: Иванов И. И.")
    )
    assert primary.status_code == 201, primary.text
    alternate = app.upload(base, "01_Иванов/доклад.pdf", pdf())
    assert alternate.status_code == 201, alternate.text

    review = app.client.get(f"{base}/materials").json()
    assert [talk["title"] for talk in review["talks"]] == ["Точность моделей"]
    files = {item["source"]["id"]: item for item in review["files"]}
    pptx, copy = files[primary.json()["id"]], files[alternate.json()["id"]]
    assert pptx["title_candidates"]["speakers"] == ["Иванов И. И."]
    assert pptx["confidence"] == 0.98
    assert copy["alternate_of"] == pptx["source"]["id"]


def test_folders_named_by_speaker_become_talks_in_upload_order(app: Fixture) -> None:
    base = app.conference()
    assert app.upload(base, "Иванов/доклад.pptx", titled_pptx("Точность")).status_code == 201
    assert app.upload(base, "Петров/слайды.pdf", pdf()).status_code == 201

    review = app.client.get(f"{base}/materials").json()
    talks = {talk["order"]: talk for talk in review["talks"]}
    assert sorted(talks) == [1, 2]
    assert talks[1]["title"] == "Точность" and talks[1]["section"] == "Основная секция"
    assert talks[2]["title"] == "Петров"
    suggested = {item["relative_path"]: item["suggested_talk_id"] for item in review["files"]}
    assert suggested == {"Иванов/доклад.pptx": talks[1]["id"], "Петров/слайды.pdf": talks[2]["id"]}


def test_content_deduplicated_on_disk_and_per_conference(app: Fixture) -> None:
    first, second = app.conference(), app.conference()
    data = "Результаты обсуждения".encode("cp1251")
    original = app.upload(first, "неверное расширение.pdf", data)
    assert original.status_code == 201, original.text
    same = app.upload(first, "заметки.txt", data)
    assert same.status_code == 200 and same.json() == original.json()
    other = app.upload(second, "другая папка/заметки.txt", data)
    assert other.status_code == 201 and other.json()["id"] != original.json()["id"]
    assert (other.json()["kind"], other.json()["encoding"]) == ("notes", "cp1251")

    sha = hashlib.sha256(data).hexdigest()
    assert (app.data / blob_key(sha)).read_bytes() == data
    assert len(list((app.data / "blobs").glob("*/*"))) == 1
    assert not list((app.data / "tmp").iterdir())
    # id файла вычисляется из конференции и SHA-256, как в пакете фрагментов (ADR-013).
    assert original.json()["id"] == str(source_file_id(UUID(first.rsplit("/", 1)[1]), sha))


def test_confirm_reassign_and_export_registry(app: Fixture) -> None:
    base = app.conference()
    slides = app.upload(base, "Модели/01_Иванов/доклад.bin", pdf())
    assert slides.json()["kind"] == "presentation"
    notes = app.upload(base, "Модели/02_Петров/заметки.txt", b"Listener notes")
    loose = app.upload(base, "_общее/заметки.txt", b"General notes")
    review = app.client.get(f"{base}/materials").json()
    first, second = review["talks"]
    files = {item["source"]["id"]: item for item in review["files"]}
    assert files[slides.json()["id"]]["suggested_talk_id"] == first["id"]
    assert files[loose.json()["id"]]["suggested_talk_id"] is None

    choices = [
        {"source_file_id": slides.json()["id"], "talk_id": second["id"]},
        {"source_file_id": notes.json()["id"], "talk_id": second["id"]},
        {"source_file_id": loose.json()["id"], "talk_id": None},
    ]
    saved = app.client.put(f"{base}/materials/bindings", json={"bindings": choices})
    assert saved.status_code == 200, saved.text
    assert [talk["status"] for talk in saved.json()["talks"]] == ["insufficient_data", "matched"]
    partial = app.client.put(f"{base}/materials/bindings", json={"bindings": choices[:1]})
    assert partial.status_code == 422

    export = app.client.get(f"{base}/conference.yaml")
    assert export.headers["content-disposition"] == 'attachment; filename="conference.yaml"'
    registry = ConferenceRegistry.model_validate(yaml.safe_load(export.text))
    assert not registry.talks[0].sources.slides
    assert registry.talks[1].sources.slides == ["Модели/01_Иванов/доклад.bin"]
    assert registry.talks[1].sources.notes == ["Модели/02_Петров/заметки.txt"]


def test_file_errors_are_isolated(app: Fixture) -> None:
    base = app.conference()
    assert app.upload(base, "заметки.txt", b"a" * (5 * 2**20 + 1)).status_code == 413
    broken = app.upload(base, "bad.pdf", b"%PDF-1.7\nbad")
    assert broken.status_code == 422 and broken.json()["message"]
    assert app.upload(base, "../escape.txt", b"Notes").status_code == 422
    assert app.upload(base, r"..\escape.txt", b"Notes").status_code == 422
    assert app.upload(base, "заметки.txt", b"Valid notes").status_code == 201
    assert app.upload(base, "заметки.txt", b"Different notes").status_code == 409
    assert len(app.client.get(f"{base}/materials").json()["files"]) == 1


def test_recording_duration_from_content(app: Fixture) -> None:
    response = app.upload(app.conference(), "запись.bin", recording())

    assert response.status_code == 201, response.text
    assert (response.json()["kind"], response.json()["duration_ms"]) == ("audio", 1000)


def test_manual_talk_and_speakers(app: Fixture) -> None:
    base = app.conference()
    created = app.client.post(f"{base}/talks", json={"title": "Доклад вручную"})
    assert created.status_code == 201, created.text
    talk = created.json()["talks"][0]
    assert (talk["section"], talk["order"]) == ("Основная секция", 1)

    speakers = [{"name": "Иванов И. И.", "affiliation": "Институт примера"}]
    updated = app.client.put(
        f"/api/v1/talks/{talk['id']}", json={"title": "Новое название", "speakers": speakers}
    )
    assert updated.status_code == 204
    registry = yaml.safe_load(app.client.get(f"{base}/conference.yaml").text)
    assert registry["talks"][0]["title"] == "Новое название"
    assert registry["talks"][0]["speakers"] == ["Иванов И. И."]
    assert app.client.post(f"{base}/talks", json={"title": ""}).status_code == 422


# --- обработка ------------------------------------------------------------------------------


def processed_talk(app: Fixture) -> tuple[str, str]:
    """Доклад с презентацией и заметками, прошедший обработку с ответами вымышленной модели."""
    base = app.conference("Тест сводок")
    deck = make_pptx([SlideSpec(body=[text]) for text in SLIDES])
    assert app.upload(base, "Хранение/01_Иванов/доклад.pptx", deck).status_code == 201
    assert app.upload(base, "Хранение/01_Иванов/заметки.txt", NOTE.encode()).status_code == 201
    app.confirm_suggestions(base)

    started = app.client.post(f"{base}/processing")
    assert started.status_code == 202, started.text
    (talk,) = started.json()["talks"]
    assert talk["job"] == "queued"

    app.scripted.responses.extend([facts_answer(), summary_answer()])
    assert app.processor.run_once()
    assert not app.processor.run_once()
    return base, talk["id"]


def test_processing_brings_summary_and_progress(app: Fixture) -> None:
    base, talk_id = processed_talk(app)

    (talk,) = app.client.get(f"{base}/progress").json()["talks"]
    assert (talk["status"], talk["job"], talk["error"]) == ("processed", "done", None)
    assert (talk["summary_number"], talk["summary_status"]) == (1, "ready")
    view = app.client.get(f"/api/v1/talks/{talk_id}/summary").json()
    assert view["summary"]["sentences"][0]["text"].startswith("В докладе рассмотрено")
    assert [f["label"] for f in view["pack"]["fragments"]] == ["S1", "S2", "S3", "S4", "S5", "N1"]
    file_id = view["pack"]["fragments"][0]["source_file_id"]
    image = app.client.get(f"/api/v1/files/{file_id}/slides/1")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    listed = app.client.get("/api/v1/conferences").json()
    assert [(item["talks"], item["summaries"], item["reviewed"]) for item in listed] == [(1, 1, 0)]


def test_retry_reuses_cached_steps_without_a_new_version(app: Fixture) -> None:
    base, talk_id = processed_talk(app)
    requests = len(app.scripted.requests)

    assert app.client.post(f"/api/v1/talks/{talk_id}/retry").status_code == 202
    assert app.processor.run_once()

    assert len(app.scripted.requests) == requests
    (talk,) = app.client.get(f"{base}/progress").json()["talks"]
    assert (talk["job"], talk["summary_number"]) == ("done", 1)


def test_rebinding_makes_summary_stale_and_requeues(app: Fixture) -> None:
    base, talk_id = processed_talk(app)
    review = app.client.get(f"{base}/materials").json()
    choices = [
        {"source_file_id": item["source"]["id"], "talk_id": talk_id}
        for item in review["files"]
        if item["source"]["kind"] == "presentation"
    ] + [
        {"source_file_id": item["source"]["id"], "talk_id": None}
        for item in review["files"]
        if item["source"]["kind"] == "notes"
    ]

    saved = app.client.put(f"{base}/materials/bindings", json={"bindings": choices})

    assert saved.json()["talks"][0]["status"] == "stale"
    (talk,) = app.client.get(f"{base}/progress").json()["talks"]
    assert talk["job"] == "queued"


def test_talk_without_text_fails_with_a_clear_message(app: Fixture) -> None:
    base = app.conference()
    assert app.upload(base, "01_Иванов/запись.wav", recording()).status_code == 201
    app.confirm_suggestions(base)
    app.client.post(f"{base}/processing")

    assert app.processor.run_once()

    (talk,) = app.client.get(f"{base}/progress").json()["talks"]
    assert talk["job"] == "failed"
    assert talk["error"].startswith("no_sources:") and "Ф2" in talk["error"]
    assert app.client.get(f"/api/v1/talks/{talk['id']}/summary").status_code == 404
    assert app.client.get("/api/v1/system").json()["queue"] == {"failed": 1}


def test_background_thread_processes_the_queue(tmp_path: Path) -> None:
    application = create_app(tmp_path, client=lambda _profile: ScriptedClient([]))
    with TestClient(application) as client:
        base = f"/api/v1/conferences/{client.post('/api/v1/conferences', json={}).json()['id']}"
        talk = client.post(f"{base}/talks", json={"title": "Без материалов"}).json()["talks"][0]
        assert client.post(f"/api/v1/talks/{talk['id']}/retry").status_code == 202

        for _ in range(100):
            (state,) = client.get(f"{base}/progress").json()["talks"]
            if state["job"] == "failed":
                break
            time.sleep(0.05)
        assert state["job"] == "failed" and state["error"].startswith("no_sources")


# --- сводка и справка -----------------------------------------------------------------------


def edit(summary: dict[str, Any], text: str, claim: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "version": summary["version"],
        "sentences": [{**summary["sentences"][0], "text": text}, *summary["sentences"][1:]],
        "conclusions": summary["conclusions"],
    }
    if claim is not None:
        body["claims"] = [{"key": "c1", "text": claim}]
    return body


def test_edit_and_review_create_versions(app: Fixture) -> None:
    _, talk_id = processed_talk(app)
    url = f"/api/v1/talks/{talk_id}/summary"
    summary = app.client.get(url).json()["summary"]

    stale = app.client.put(url, json={**edit(summary, "Новый текст."), "version": 2})
    assert stale.status_code == 409
    unknown = edit(summary, "Новый текст.")
    unknown["sentences"][0]["claim_ids"] = ["c99"]
    assert app.client.put(url, json=unknown).status_code == 422

    edited = app.client.put(url, json=edit(summary, "Новый текст.", "Тема доклада."))
    assert edited.status_code == 200, edited.text
    body = edited.json()["summary"]
    assert (body["version"], body["human_edit"]) == (2, True)
    assert body["sentences"][0]["text"] == "Новый текст."
    assert (body["claims"][0]["text"], body["claims"][0]["status"]) == (
        "Тема доклада.",
        "needs_human",
    )

    reviewed = app.client.post(f"{url}/review", json={"version": 2})
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["talk_status"] == "reviewed"
    assert reviewed.json()["summary"]["claims"][0]["status"] == "confirmed"

    # Правка проверенной сводки возвращает доклад в processed (spec §6.8).
    again = app.client.put(url, json={**edit(summary, "Ещё правка."), "version": 3})
    assert again.status_code == 200 and again.json()["talk_status"] == "processed"


def test_report_is_built_from_current_summaries(app: Fixture) -> None:
    base, talk_id = processed_talk(app)
    url = f"/api/v1/talks/{talk_id}/summary"
    summary = app.client.get(url).json()["summary"]
    assert app.client.put(url, json=edit(summary, "Текст после правки.")).status_code == 200

    unreviewed = app.client.post(f"{base}/reports", json={})
    assert unreviewed.status_code == 422 and "непроверенные" in unreviewed.json()["message"]
    undated = app.client.post(f"{base}/reports", json={"unreviewed_confirmed": True})
    assert undated.status_code == 422 and "даты" in undated.json()["message"]

    dated = app.client.put(
        base, json={"title": "Тест сводок", "start_date": "2026-10-20", "end_date": "2026-10-20"}
    )
    assert dated.status_code == 200, dated.text
    built = app.client.post(f"{base}/reports", json={"unreviewed_confirmed": True})
    assert built.status_code == 201, built.text
    version = built.json()
    assert version["number"] == 1 and version["variables"]["talks_count"] == 1
    assert {file["format"] for file in version["files"]} == {"docx", "md", "pdf"}
    assert any("не проверена" in warning for warning in version["warnings"])

    markdown = app.client.get(f"/api/v1/report-versions/{version['id']}/files/md")
    assert markdown.status_code == 200 and "Текст после правки." in markdown.text
    assert "attachment" in markdown.headers["content-disposition"]
    assert [item["id"] for item in app.client.get(f"{base}/reports").json()] == [version["id"]]


def test_conference_crud_and_cascade(app: Fixture) -> None:
    base, talk_id = processed_talk(app)

    bad = app.client.put(base, json={"start_date": "2026-10-21", "end_date": "2026-10-20"})
    assert bad.status_code == 422 and bad.json()["message"]
    assert app.client.delete(base).status_code == 204

    assert app.client.get(base).status_code == 404
    assert app.client.get(f"/api/v1/talks/{talk_id}/summary").status_code == 404
    assert app.client.get("/api/v1/conferences").json() == []


def test_system_shows_profiles_and_templates(app: Fixture) -> None:
    info = app.client.get("/api/v1/system").json()

    assert {"local", "local-fast"} <= {profile["name"] for profile in info["profiles"]}
    assert [p["name"] for p in info["profiles"] if p["default"]] == ["local"]
    assert info["templates"] == [{"name": "starter", "version": "starter-1"}]
    # В тестах сети нет: Ollama недоступна, но страница управления открывается.
    assert info["ollama_available"] is False and info["queue"] == {}


# --- статика --------------------------------------------------------------------------------


def test_frontend_and_api_share_one_process(tmp_path: Path) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>Репортёр</title>")

    with TestClient(create_app(tmp_path / "data", static, process=False)) as client:
        assert "Репортёр" in client.get("/").text
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/api/v1/conferences/missing").status_code == 404
        assert client.get("/missing.js").status_code == 404


def test_incomplete_frontend_build_fails_at_startup(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match=r"index\.html"):
        create_app(tmp_path / "data", tmp_path, process=False)
