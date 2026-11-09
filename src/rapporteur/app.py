"""Веб-приложение: HTTP API, собранный фронтенд и фоновая обработка в одном процессе (ADR-014).

Запуск: uvicorn rapporteur.app:create_app --factory. Пользователей нет (ADR-011), доступ
ограничивает сеть.
"""

import datetime as dt
import hashlib
import json
import logging
import sqlite3
import tempfile
import urllib.request
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self
from urllib.parse import quote

import anyio
import yaml
from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool

from rapporteur import db, materials, processing, reports, settings, summaries
from rapporteur.contracts import ModelProfile, SourceFile, Speaker
from rapporteur.contracts.base import NonEmptyStr
from rapporteur.contracts.enums import ReportFormat, SummaryStatus, TalkStatus
from rapporteur.errors import FailureCode, NotFoundError, StageError, UserError
from rapporteur.ingest.inventory import HEADER_BYTES, MAX_UPLOAD_BYTES, inspect_file, upload_limit
from rapporteur.ingest.limits import check_size
from rapporteur.llm.client import make_client
from rapporteur.llm.profiles import ProfilesFile
from rapporteur.processing import ClientFactory, Processor
from rapporteur.storage import Storage, sha256_hex

log = logging.getLogger("rapporteur")
api = APIRouter(prefix="/api/v1")

_MEDIA = {
    ReportFormat.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ReportFormat.MARKDOWN: "text/markdown; charset=utf-8",
    ReportFormat.PDF: "application/pdf",
}


def create_app(
    data_dir: Path | None = None,
    static_dir: Path | None = None,
    client: ClientFactory | None = None,
    *,
    process: bool = True,
) -> FastAPI:
    """process=False — без фонового потока: тесты вызывают processor.run_once() сами."""
    # uvicorn настраивает только свои логгеры; журнал обработки пишется рядом с ними.
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s: %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    data_dir = data_dir or settings.DATA_DIR
    database = db.Database(data_dir / "rapporteur.db")
    storage = Storage(data_dir)
    processor = Processor(
        database,
        storage,
        client or (lambda profile: make_client(profile, settings.OLLAMA_URL)),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if process:
            processor.start()
        try:
            yield
        finally:
            processor.stop()

    app = FastAPI(
        title="Репортёр",
        version=version("rapporteur"),
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.database = database
    app.state.storage = storage
    app.state.processor = processor

    @app.exception_handler(UserError)
    async def user_error(_request: Request, error: UserError) -> JSONResponse:
        return JSONResponse({"message": error.message}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, error: RequestValidationError) -> JSONResponse:
        first = error.errors()[0] if error.errors() else {}
        where = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
        message = f"Некорректный запрос: {where} — {first.get('msg', 'ошибка')}".strip()
        return JSONResponse({"message": message}, status_code=422)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": app.version}

    app.include_router(api)

    # Статика после API: /health и /api/v1 имеют приоритет.
    if static_dir is None and settings.STATIC_DIR:
        static_dir = Path(settings.STATIC_DIR)
    if static_dir is not None:
        if not (static_dir / "index.html").is_file():
            raise RuntimeError(f"Не найдена сборка фронтенда: {static_dir / 'index.html'}")
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
    return app


# --- зависимости ---------------------------------------------------------------------------


def _database(request: Request) -> db.Database:
    database: db.Database = request.app.state.database
    return database


def _tx(request: Request) -> Iterator[sqlite3.Connection]:
    with _database(request).tx() as conn:
        yield conn


Tx = Annotated[sqlite3.Connection, Depends(_tx)]


def _storage(request: Request) -> Storage:
    storage: Storage = request.app.state.storage
    return storage


def _wake(request: Request) -> None:
    processor: Processor = request.app.state.processor
    processor.wake()


# --- конференции ---------------------------------------------------------------------------


class ConferenceWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: NonEmptyStr | None = None
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    place: NonEmptyStr | None = None
    organizer: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _dates(self) -> Self:
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("Дата окончания раньше начала")
        return self


class ConferenceItem(BaseModel):
    id: str
    title: str | None
    start_date: dt.date | None
    end_date: dt.date | None
    place: str | None
    organizer: str | None
    processing_started: bool
    talks: int
    summaries: int
    reviewed: int


_CONFERENCE_ITEM = """
SELECT conferences.*,
    (SELECT count(*) FROM talks JOIN sections ON sections.id = talks.section_id
     WHERE sections.conference_id = conferences.id) AS talks,
    (SELECT count(DISTINCT summaries.talk_id) FROM summaries
     JOIN talks ON talks.id = summaries.talk_id JOIN sections ON sections.id = talks.section_id
     WHERE sections.conference_id = conferences.id) AS summaries,
    (SELECT count(*) FROM talks JOIN sections ON sections.id = talks.section_id
     WHERE sections.conference_id = conferences.id AND talks.status = 'reviewed') AS reviewed
FROM conferences
"""


def _conference_item(row: db.Row) -> ConferenceItem:
    return ConferenceItem.model_validate(dict(row))


def _conference(conn: sqlite3.Connection, conference_id: str) -> ConferenceItem:
    row = db.one(conn, _CONFERENCE_ITEM + "WHERE conferences.id = ?", conference_id)
    if row is None:
        raise NotFoundError("Конференция не найдена")
    return _conference_item(row)


@api.get("/conferences")
def list_conferences(conn: Tx) -> list[ConferenceItem]:
    rows = conn.execute(_CONFERENCE_ITEM + "ORDER BY created_at DESC").fetchall()
    return [_conference_item(row) for row in rows]


@api.post("/conferences", status_code=201)
def create_conference(conn: Tx, data: ConferenceWrite) -> ConferenceItem:
    conference_id = db.new_id()
    conn.execute(
        "INSERT INTO conferences (id, title, start_date, end_date, place, organizer, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (conference_id, *_conference_values(data), db.now()),
    )
    return _conference(conn, conference_id)


def _conference_values(data: ConferenceWrite) -> tuple[str | None, ...]:
    def day(value: dt.date | None) -> str | None:
        return value.isoformat() if value else None

    return data.title, day(data.start_date), day(data.end_date), data.place, data.organizer


@api.get("/conferences/{conference_id}")
def get_conference(conn: Tx, conference_id: str) -> ConferenceItem:
    return _conference(conn, conference_id)


@api.put("/conferences/{conference_id}")
def update_conference(conn: Tx, conference_id: str, data: ConferenceWrite) -> ConferenceItem:
    materials.require_conference(conn, conference_id)
    conn.execute(
        "UPDATE conferences SET title = ?, start_date = ?, end_date = ?, place = ?, organizer = ? "
        "WHERE id = ?",
        (*_conference_values(data), conference_id),
    )
    return _conference(conn, conference_id)


@api.delete("/conferences/{conference_id}", status_code=204)
def delete_conference(conn: Tx, conference_id: str) -> Response:
    materials.require_conference(conn, conference_id)
    conn.execute("DELETE FROM conferences WHERE id = ?", (conference_id,))
    return Response(status_code=204)


# --- материалы и реестр --------------------------------------------------------------------

_UPLOAD_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}


@api.post("/conferences/{conference_id}/materials", status_code=201, openapi_extra=_UPLOAD_SCHEMA)
async def upload_material(
    request: Request,
    response: Response,
    conference_id: str,
    path: Annotated[str, Query(min_length=1, max_length=1024)],
    program: bool = False,
) -> SourceFile:
    """Файл потоком на том. Тип определяется по содержимому; повтор — ответ 200."""
    path = materials.safe_path(path)
    if request.headers.get("content-type") != "application/octet-stream":
        raise UserError("Передайте содержимое файла как application/octet-stream", 415)
    database = _database(request)
    storage = _storage(request)

    def require() -> None:
        with database.tx() as conn:
            materials.require_conference(conn, conference_id)

    await run_in_threadpool(require)
    work = storage.root / "tmp"
    await anyio.Path(work).mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="upload-", dir=work) as tmp:
            staged = Path(tmp) / "source"
            digest = hashlib.sha256()
            size = 0
            limit = MAX_UPLOAD_BYTES
            head = bytearray()
            async with await anyio.open_file(staged, "wb") as stream:
                async for chunk in request.stream():
                    if len(head) < HEADER_BYTES:
                        head.extend(chunk[: HEADER_BYTES - len(head)])
                        if len(head) == HEADER_BYTES:
                            limit = upload_limit(bytes(head))
                    size += len(chunk)
                    check_size(size, limit)
                    digest.update(chunk)
                    await stream.write(chunk)

            def register() -> tuple[SourceFile, bool]:
                name = PurePosixPath(path).name
                marked = program or PurePosixPath(name).stem.casefold() in {"программа", "program"}
                inspection = inspect_file(staged, program=marked)
                sha = digest.hexdigest()
                item = inspection.item(staged, name, sha)
                with database.tx() as conn:
                    result = materials.add_file(conn, conference_id, path, item, inspection)
                    storage.add_blob(sha, staged)
                return result

            source, created = await run_in_threadpool(register)
    except StageError as error:
        status = 413 if error.code is FailureCode.FILE_TOO_LARGE else 422
        raise UserError(error.message, status) from error
    if not created:
        response.status_code = 200
    return source


@api.get("/conferences/{conference_id}/materials")
def review_materials(conn: Tx, conference_id: str) -> materials.Materials:
    return materials.review(conn, conference_id)


@api.put("/conferences/{conference_id}/materials/bindings")
def confirm_materials(
    request: Request, conn: Tx, conference_id: str, data: materials.BindingReview
) -> materials.Materials:
    conference = materials.require_conference(conn, conference_id)
    materials.confirm(conn, conference_id, data)
    # После запуска обработки новые и перепривязанные доклады обрабатываются сразу.
    if conference["processing_started"] and processing.enqueue_ready(conn, conference_id):
        _wake(request)
    return materials.review(conn, conference_id)


@api.get("/conferences/{conference_id}/conference.yaml", response_class=Response)
def export_registry(conn: Tx, conference_id: str) -> Response:
    registry = materials.registry(conn, conference_id)
    return Response(
        yaml.safe_dump(registry.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
        media_type="application/yaml",
        headers={"Content-Disposition": 'attachment; filename="conference.yaml"'},
    )


class TalkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: NonEmptyStr
    section_id: str | None = None


class TalkUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: NonEmptyStr | None
    speakers: list[Speaker] = Field(default_factory=list)


@api.post("/conferences/{conference_id}/talks", status_code=201)
def create_talk(conn: Tx, conference_id: str, data: TalkCreate) -> materials.Materials:
    materials.add_talk(conn, conference_id, data.title, data.section_id)
    return materials.review(conn, conference_id)


@api.put("/talks/{talk_id}", status_code=204)
def update_talk(conn: Tx, talk_id: str, data: TalkUpdate) -> Response:
    """Название и докладчики реестра: они идут в справку вместо ответа модели (ADR-008)."""
    updated = conn.execute(
        "UPDATE talks SET title = ?, speakers = ? WHERE id = ?",
        (
            data.title,
            db.dumps([speaker.model_dump(mode="json") for speaker in data.speakers]),
            talk_id,
        ),
    ).rowcount
    if not updated:
        raise NotFoundError("Доклад не найден")
    return Response(status_code=204)


# --- обработка -----------------------------------------------------------------------------


class TalkProgress(BaseModel):
    id: str
    section_id: str
    section: str
    order: int
    title: str | None
    status: TalkStatus
    # queued, running, done или failed; step — текущий шаг: parse, facts, verify, summary.
    job: Literal["queued", "running", "done", "failed"] | None
    step: str | None
    error: str | None
    summary_number: int | None
    summary_status: SummaryStatus | None


class Progress(BaseModel):
    processing_started: bool
    files: int
    talks: list[TalkProgress]


@api.post("/conferences/{conference_id}/processing", status_code=202)
def start_processing(request: Request, conn: Tx, conference_id: str) -> Progress:
    materials.require_conference(conn, conference_id)
    if not db.one(conn, "SELECT 1 FROM files WHERE conference_id = ?", conference_id):
        raise UserError("Сначала загрузите материалы")
    conn.execute("UPDATE conferences SET processing_started = 1 WHERE id = ?", (conference_id,))
    processing.enqueue_ready(conn, conference_id)
    _wake(request)
    return conference_progress(conn, conference_id)


@api.get("/conferences/{conference_id}/progress")
def conference_progress(conn: Tx, conference_id: str) -> Progress:
    conference = materials.require_conference(conn, conference_id)
    current = db.current_summaries(conn, conference_id)
    talks = []
    for row in db.conference_talks(conn, conference_id):
        summary = current.get(row["id"])
        talks.append(
            TalkProgress(
                id=row["id"],
                section_id=row["section_id"],
                section=row["section_title"],
                order=row["position"],
                title=row["title"],
                status=row["status"],
                job=row["job"],
                step=row["step"],
                error=row["error"],
                summary_number=summary["number"] if summary else None,
                summary_status=json.loads(summary["summary"])["status"] if summary else None,
            )
        )
    files = conn.execute(
        "SELECT count(*) FROM files WHERE conference_id = ?", (conference_id,)
    ).fetchone()[0]
    return Progress(
        processing_started=bool(conference["processing_started"]), files=files, talks=talks
    )


@api.post("/talks/{talk_id}/retry", status_code=202)
def retry_talk(request: Request, conn: Tx, talk_id: str) -> Response:
    row = db.one(conn, "SELECT job FROM talks WHERE id = ?", talk_id)
    if row is None:
        raise NotFoundError("Доклад не найден")
    if row["job"] in ("queued", "running"):
        raise UserError("Доклад уже в обработке", 409)
    processing.enqueue(conn, [talk_id])
    _wake(request)
    return Response(status_code=202)


# --- сводки --------------------------------------------------------------------------------


@api.get("/talks/{talk_id}/summary")
def get_summary(conn: Tx, talk_id: str) -> summaries.TalkSummaryView:
    return summaries.view(conn, talk_id)


@api.put("/talks/{talk_id}/summary")
def edit_summary(conn: Tx, talk_id: str, data: summaries.SummaryEdit) -> summaries.TalkSummaryView:
    summaries.edit(conn, talk_id, data)
    return summaries.view(conn, talk_id)


@api.post("/talks/{talk_id}/summary/review")
def review_summary(
    conn: Tx, talk_id: str, data: summaries.SummaryReview
) -> summaries.TalkSummaryView:
    summaries.review(conn, talk_id, data)
    return summaries.view(conn, talk_id)


@api.get("/files/{file_id}/slides/{slide_no}", response_class=Response)
def slide_image(request: Request, conn: Tx, file_id: str, slide_no: int) -> Response:
    """Картинка слайда из разбора презентации: к ней ведут ссылки сводки."""
    row = db.one(conn, "SELECT sha256 FROM files WHERE id = ?", file_id)
    if row is None:
        raise NotFoundError("Файл не найден")
    storage = _storage(request)
    key = processing.slide_image_key(row["sha256"], slide_no)
    if not storage.exists(key):
        raise NotFoundError("Слайд ещё не разобран")
    return Response(
        storage.get(key), media_type="image/png", headers={"Cache-Control": "private, max-age=3600"}
    )


# --- справка -------------------------------------------------------------------------------


@api.get("/conferences/{conference_id}/reports")
def list_reports(conn: Tx, conference_id: str) -> list[reports.ReportVersionView]:
    return reports.versions(conn, conference_id)


@api.post("/conferences/{conference_id}/reports", status_code=201)
def build_report(
    request: Request, conference_id: str, data: reports.ReportBuild
) -> reports.ReportVersionView:
    return reports.build(_database(request), _storage(request), conference_id, data)


@api.get("/report-versions/{version_id}/files/{report_format}", response_class=Response)
def report_file(
    request: Request, conn: Tx, version_id: str, report_format: ReportFormat
) -> Response:
    number, file = reports.report_file(conn, version_id, report_format.value)
    data = _storage(request).get(file.storage_key)
    if sha256_hex(data) != file.sha256:
        raise UserError("Файл справки повреждён", 500)
    name = f"Справка, версия {number}.{report_format.value}"
    return Response(
        data,
        media_type=_MEDIA[report_format],
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


# --- состояние -----------------------------------------------------------------------------


class ProfileInfo(BaseModel):
    name: str
    model: str
    reasoning: bool
    prompt_version: str
    default: bool


class TemplateInfo(BaseModel):
    name: str
    version: str


class SystemInfo(BaseModel):
    version: str
    ollama_url: str
    ollama_available: bool
    ollama_models: list[str]
    profiles: list[ProfileInfo]
    templates: list[TemplateInfo]
    # Доклады по состоянию обработки: queued, running, failed.
    queue: dict[str, int]


def _profiles() -> list[ProfileInfo]:
    try:
        data = ProfilesFile.model_validate(
            yaml.safe_load(settings.PROFILES_FILE.read_text(encoding="utf-8"))
        )
    except (OSError, yaml.YAMLError, ValidationError):
        return []
    return [_profile(item, item.name == data.default) for item in data.profiles]


def _profile(item: ModelProfile, default: bool) -> ProfileInfo:
    return ProfileInfo(
        name=item.name,
        model=item.model,
        reasoning=item.generation.reasoning,
        prompt_version=item.prompt_version,
        default=default,
    )


def _templates() -> list[TemplateInfo]:
    result = []
    for spec in sorted(settings.TEMPLATES_DIR.glob("*/variables.yaml")):
        try:
            template_version = yaml.safe_load(spec.read_text(encoding="utf-8")).get("version")
        except (OSError, yaml.YAMLError, AttributeError):
            continue
        if isinstance(template_version, str):
            result.append(TemplateInfo(name=spec.parent.name, version=template_version))
    return result


def _ollama_models() -> list[str] | None:
    try:
        with urllib.request.urlopen(f"{settings.OLLAMA_URL}/api/tags", timeout=2) as answer:
            return [item["name"] for item in json.load(answer).get("models", [])]
    except (OSError, ValueError, KeyError):
        return None


@api.get("/system")
def system(request: Request) -> SystemInfo:
    # Запрос к Ollama идёт вне транзакции: он не должен задерживать обработку.
    with _database(request).tx() as conn:
        queue = {
            row["job"]: row["count"]
            for row in conn.execute(
                "SELECT job, count(*) AS count FROM talks "
                "WHERE job IN ('queued', 'running', 'failed') GROUP BY job"
            )
        }
    models = _ollama_models()
    return SystemInfo(
        version=request.app.version,
        ollama_url=settings.OLLAMA_URL,
        ollama_available=models is not None,
        ollama_models=models or [],
        profiles=_profiles(),
        templates=_templates(),
        queue=queue,
    )
