"""Состояние установки для страницы управления: версия, режим, профили, бланки и очередь."""

from pathlib import Path

import yaml
from fastapi import APIRouter, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy import text

from rapporteur_backend.errors import API_ERRORS
from rapporteur_backend.jobs import require_queue
from rapporteur_backend.migrate import MIGRATION_HEAD
from rapporteur_backend.routes import engine
from rapporteur_backend.summary_routes import mode
from rapporteur_pipeline.llm.profiles import PROFILES_FILE, ProfilesFile
from rapporteur_pipeline.report.stage import TEMPLATES_DIR

router = APIRouter(prefix="/api/v1", responses=API_ERRORS)


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
    mode: str
    migration: str
    profiles: list[ProfileInfo]
    templates: list[TemplateInfo]
    # Задачи текущего режима: ресурс → статус → число.
    queue: dict[str, dict[str, int]]


def profiles(path: Path = PROFILES_FILE) -> list[ProfileInfo]:
    try:
        data = ProfilesFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError):
        return []
    return [
        ProfileInfo(
            name=item.name,
            model=item.model,
            reasoning=item.generation.reasoning,
            prompt_version=item.prompt_version,
            default=item.name == data.default,
        )
        for item in data.profiles
    ]


def templates(root: Path = TEMPLATES_DIR) -> list[TemplateInfo]:
    result = []
    for spec in sorted(root.glob("*/variables.yaml")):
        try:
            version = yaml.safe_load(spec.read_text(encoding="utf-8")).get("version")
        except (OSError, yaml.YAMLError, AttributeError):
            continue
        if isinstance(version, str):
            result.append(TemplateInfo(name=spec.parent.name, version=version))
    return result


@router.get("/system", response_model=SystemInfo)
def system(request: Request) -> SystemInfo:
    queue: dict[str, dict[str, int]] = {}
    with engine(request).begin() as conn:
        require_queue(conn)
        rows = conn.execute(
            text(
                "SELECT resource, status, count(*) AS count FROM rapporteur_queue.jobs "
                "WHERE mode = :mode GROUP BY resource, status"
            ),
            {"mode": mode(request)},
        ).mappings()
        for row in rows:
            queue.setdefault(row["resource"], {})[row["status"]] = row["count"]
    return SystemInfo(
        version=request.app.version,
        mode=mode(request),
        migration=MIGRATION_HEAD,
        profiles=profiles(),
        templates=templates(),
        queue=queue,
    )
