"""Загрузка потоком и API проверки привязок."""

import hashlib
import tempfile
from pathlib import Path, PurePosixPath
from typing import Annotated
from uuid import UUID

import anyio
import sqlalchemy as sa
import yaml
from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from rapporteur_backend import materials, store
from rapporteur_backend import tables as t
from rapporteur_backend.errors import API_ERRORS, ApiRequestError
from rapporteur_backend.jobs import require_queue
from rapporteur_backend.materials import BindingReview, Materials
from rapporteur_backend.routes import Version, engine, etag
from rapporteur_contracts import ApiError, SourceFile
from rapporteur_contracts.enums import ApiErrorCode
from rapporteur_pipeline.ingest.inventory import (
    HEADER_BYTES,
    MAX_UPLOAD_BYTES,
    inspect_file,
    upload_limit,
)
from rapporteur_pipeline.ingest.limits import check_size
from rapporteur_pipeline.stage import FailureCode, StageError
from rapporteur_pipeline.storage import DirectoryStorage

router = APIRouter(prefix="/api/v1/conferences/{conference_id}", responses=API_ERRORS)
_UPLOAD_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}


class ProcessingAccepted(BaseModel):
    status_url: str


@router.post("/processing", status_code=202, response_model=ProcessingAccepted)
def start_processing(request: Request, conference_id: UUID) -> ProcessingAccepted:
    with engine(request).begin() as conn:
        conference = store.require(conn, t.conferences, conference_id, lock=True)
        require_queue(conn)
        if not conference["processing_started"]:
            if not conn.scalar(
                sa.select(sa.exists().where(t.source_files.c.conference_id == conference_id))
            ):
                raise ApiRequestError(ApiErrorCode.VALIDATION_ERROR, "Сначала загрузите материалы")
            conn.execute(
                sa.update(t.conferences)
                .where(t.conferences.c.id == conference_id)
                .values(processing_started=True)
            )
    return ProcessingAccepted(status_url=f"/api/v1/jobs?conference_id={conference_id}")


@router.post(
    "/materials",
    status_code=201,
    response_model=SourceFile,
    responses={200: {"model": SourceFile}, 413: {"model": ApiError}, 415: {"model": ApiError}},
    openapi_extra=_UPLOAD_SCHEMA,
)
async def upload_material(
    request: Request,
    response: Response,
    conference_id: UUID,
    path: Annotated[str, Query(min_length=1, max_length=1024)],
    program: bool = False,
) -> SourceFile:
    path = materials.safe_path(path)
    if request.headers.get("content-type") != "application/octet-stream":
        raise ApiRequestError(
            ApiErrorCode.VALIDATION_ERROR,
            "Передайте содержимое файла как application/octet-stream",
            status=415,
        )
    database = engine(request)

    def require_conference() -> None:
        with database.begin() as conn:
            store.require(conn, t.conferences, conference_id)

    await run_in_threadpool(require_conference)
    root: Path = request.app.state.storage_dir
    work = root / "tmp"
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
                marked_program = program or PurePosixPath(name).stem.casefold() in {
                    "программа",
                    "program",
                }
                inspection = inspect_file(staged, program=marked_program)
                sha = digest.hexdigest()
                item = inspection.item(staged, name, sha)
                with database.begin() as conn:
                    result = materials.add_file(conn, conference_id, path, item, inspection)
                    DirectoryStorage(root).add_blob(sha, staged)
                return result

            source, created = await run_in_threadpool(register)
    except StageError as error:
        raise ApiRequestError(
            ApiErrorCode.VALIDATION_ERROR,
            error.message,
            status=413 if error.code is FailureCode.FILE_TOO_LARGE else 422,
            details={"file_code": error.code},
        ) from error
    if not created:
        response.status_code = 200
    return source


@router.get("/materials", response_model=Materials)
def review_materials(request: Request, response: Response, conference_id: UUID) -> Materials:
    with engine(request).begin() as conn:
        conference = store.require(conn, t.conferences, conference_id, lock=True)
        result = materials.review(conn, conference_id)
    etag(response, conference["version"])
    return result


@router.put("/materials/bindings", response_model=Materials)
def confirm_materials(
    request: Request,
    response: Response,
    conference_id: UUID,
    data: BindingReview,
    version: Version,
) -> Materials:
    with engine(request).begin() as conn:
        updated = materials.confirm(conn, conference_id, version, data)
        result = materials.review(conn, conference_id)
    etag(response, updated)
    return result


@router.get(
    "/conference.yaml",
    response_class=Response,
    responses={200: {"content": {"application/yaml": {"schema": {"type": "string"}}}}},
)
def export_registry(request: Request, conference_id: UUID) -> Response:
    with engine(request).begin() as conn:
        conference = store.require(conn, t.conferences, conference_id, lock=True)
        registry = materials.registry(conn, conference)
    return Response(
        yaml.safe_dump(registry.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
        media_type="application/yaml",
        headers={"Content-Disposition": 'attachment; filename="conference.yaml"'},
    )
