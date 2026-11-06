"""Приложение FastAPI и статика браузерного интерфейса (spec §11)."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import URL, text

from rapporteur_backend.db import configured_url, make_engine
from rapporteur_backend.errors import ApiRequestError, install_errors
from rapporteur_backend.jobs import router as job_router
from rapporteur_backend.material_routes import router as material_router
from rapporteur_backend.migrate import MIGRATION_HEAD
from rapporteur_backend.report_routes import router as report_router
from rapporteur_backend.routes import engine, router
from rapporteur_backend.summary_routes import router as summary_router
from rapporteur_backend.system_routes import router as system_router
from rapporteur_contracts.enums import ApiErrorCode


class Health(BaseModel):
    status: Literal["ok"]
    version: str


def create_app(
    static_dir: Path | None = None,
    database_url: str | URL | None = None,
    storage_dir: Path | None = None,
    mode: str | None = None,
) -> FastAPI:
    url = database_url if database_url is not None else configured_url()
    database = make_engine(url) if url is not None else None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if database is not None:
                database.dispose()

    app = FastAPI(
        title="Репортёр",
        version=version("rapporteur-backend"),
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.database = database
    app.state.storage_dir = storage_dir or Path(os.environ.get("RAPPORTEUR_DATA_DIR", "/data"))
    # Режим воркеров: в нём ставится сборка справки и читается ход обработки.
    app.state.mode = mode or os.environ.get("RAPPORTEUR_MODE", "dev")
    if app.state.mode not in {"dev", "gpu"}:
        raise RuntimeError("RAPPORTEUR_MODE должен быть dev или gpu")
    install_errors(app)
    app.include_router(router)
    app.include_router(job_router)
    app.include_router(material_router)
    app.include_router(summary_router)
    app.include_router(report_router)
    app.include_router(system_router)

    @app.get("/health")
    def health() -> Health:
        return Health(status="ok", version=app.version)

    @app.get("/ready", response_model=Health)
    def ready(request: Request) -> Health:
        with engine(request).begin() as conn:
            if conn.scalar(text("SELECT version_num FROM alembic_version")) != MIGRATION_HEAD:
                raise ApiRequestError(
                    ApiErrorCode.INTERNAL_ERROR, "Нужна совместимая миграция backend"
                )
        return Health(status="ok", version=app.version)

    # В образе web фронтенд собран заранее. При работе только с backend статика не нужна.
    # Монтируем после API: /health и /api/v1 имеют приоритет над статикой.
    if static_dir is None and (configured_dir := os.environ.get("RAPPORTEUR_STATIC_DIR")):
        static_dir = Path(configured_dir)
    if static_dir is not None:
        if not (static_dir / "index.html").is_file():
            raise RuntimeError(f"Не найдена сборка фронтенда: {static_dir / 'index.html'}")
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")

    return app


app = create_app()
