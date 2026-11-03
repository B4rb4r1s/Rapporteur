"""Приложение FastAPI. Маршруты `/api/v1` появятся в P1-04 (spec §11)."""

from importlib.metadata import version
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel


class Health(BaseModel):
    status: Literal["ok"]
    version: str


def create_app() -> FastAPI:
    app = FastAPI(title="Репортёр", version=version("rapporteur-backend"))

    @app.get("/health")
    def health() -> Health:
        return Health(status="ok", version=app.version)

    return app


app = create_app()
