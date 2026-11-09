"""Модель для сводок и режим рассуждения, выбранные на странице «Управление» (spec §9).

Профиль по умолчанию задаёт profiles.yaml. Выбор в интерфейсе хранится в базе и заменяет
в профиле модель, ревизию весов, квантование и режим рассуждения; лимиты генерации и версия
промптов остаются из профиля. Профиль целиком входит в ключ кэша шагов сводки, поэтому
после смены модели доклады обрабатываются ею, а не берутся из кэша прежней.
"""

import json
import sqlite3
import urllib.request

from pydantic import BaseModel, ConfigDict

from rapporteur import db
from rapporteur.contracts import ModelProfile
from rapporteur.contracts.base import NonEmptyStr
from rapporteur.errors import UserError
from rapporteur.llm.profiles import load_profile

_KEY = "model"
# Возможность модели в ответе Ollama /api/tags: у неё есть режим рассуждения (think).
THINKING = "thinking"


class OllamaModel(BaseModel):
    name: str
    parameter_size: str | None
    quantization: str | None
    size_bytes: int
    digest: str
    thinking: bool


class ModelChoice(BaseModel):
    """Выбор человека вместе со сведениями о весах на момент выбора."""

    model: str
    reasoning: bool
    weights_revision: str
    quantization: str


class ModelChoiceWrite(BaseModel):
    """model = null возвращает профиль по умолчанию из profiles.yaml."""

    model_config = ConfigDict(extra="forbid")

    model: NonEmptyStr | None
    reasoning: bool = False


class ActiveModel(BaseModel):
    profile: str
    model: str
    reasoning: bool
    # Выбрано на странице «Управление», а не взято из profiles.yaml.
    chosen: bool


class ModelState(BaseModel):
    current: ActiveModel
    default: ActiveModel
    ollama_available: bool
    models: list[OllamaModel]


def installed_models(ollama_url: str, timeout: float = 3) -> list[OllamaModel] | None:
    """Модели Ollama или None, если Ollama недоступна."""
    try:
        with urllib.request.urlopen(f"{ollama_url}/api/tags", timeout=timeout) as answer:
            items = json.load(answer).get("models", [])
        return [
            OllamaModel(
                name=item["name"],
                parameter_size=item.get("details", {}).get("parameter_size"),
                quantization=item.get("details", {}).get("quantization_level"),
                size_bytes=int(item.get("size", 0)),
                digest=item.get("digest", ""),
                thinking=THINKING in (item.get("capabilities") or []),
            )
            for item in items
        ]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def stored(conn: sqlite3.Connection) -> ModelChoice | None:
    row = db.one(conn, "SELECT value FROM settings WHERE key = ?", _KEY)
    return ModelChoice.model_validate_json(row["value"]) if row else None


def active_profile(conn: sqlite3.Connection) -> ModelProfile:
    base = load_profile()
    choice = stored(conn)
    if choice is None:
        return base
    return base.model_copy(
        update={
            "name": choice.model,
            "model": choice.model,
            "weights_revision": choice.weights_revision,
            "quantization": choice.quantization,
            "generation": base.generation.model_copy(update={"reasoning": choice.reasoning}),
        }
    )


def save(
    conn: sqlite3.Connection, data: ModelChoiceWrite, models: list[OllamaModel] | None
) -> None:
    if data.model is None:
        conn.execute("DELETE FROM settings WHERE key = ?", (_KEY,))
        return
    if models is None:
        raise UserError("Ollama недоступна: выбранную модель нельзя проверить", 503)
    found = next((item for item in models if item.name == data.model), None)
    if found is None:
        raise UserError(f"Модели {data.model} нет в Ollama")
    if data.reasoning and not found.thinking:
        raise UserError(f"У модели {data.model} нет режима рассуждения")
    choice = ModelChoice(
        model=found.name,
        reasoning=data.reasoning,
        weights_revision=f"sha256:{found.digest}" if found.digest else "неизвестна",
        quantization=found.quantization or "неизвестно",
    )
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (_KEY, choice.model_dump_json()),
    )


def state(conn: sqlite3.Connection, models: list[OllamaModel] | None) -> ModelState:
    default = load_profile()
    current = active_profile(conn)
    return ModelState(
        current=_active(current, chosen=stored(conn) is not None),
        default=_active(default, chosen=False),
        ollama_available=models is not None,
        models=models or [],
    )


def _active(profile: ModelProfile, *, chosen: bool) -> ActiveModel:
    return ActiveModel(
        profile=profile.name,
        model=profile.model,
        reasoning=profile.generation.reasoning,
        chosen=chosen,
    )
