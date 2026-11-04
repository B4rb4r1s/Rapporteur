"""Интерфейс этапа и служебная часть артефакта (ADR-005, ADR-007)."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Protocol

from pydantic import BaseModel

from rapporteur_contracts import ArtifactMeta, ArtifactRef, StageInputs, StageOutput
from rapporteur_pipeline.storage import Storage, artifact_key, sha256_hex

if TYPE_CHECKING:
    from rapporteur_pipeline.llm.client import LlmClient

CONTRACT_VERSION = version("rapporteur-contracts")

# Вход этапа с областью file — сам файл: ArtifactRef с этим именем указывает на blobs/.
SOURCE_INPUT = "source"


class FailureCode(StrEnum):
    """Устойчивые коды ошибок файла или доклада; попадают в FileFailure и Job.error_code."""

    UNSUPPORTED_FORMAT = "unsupported_format"
    BROKEN_FILE = "broken_file"
    ENCRYPTED_FILE = "encrypted_file"
    FILE_TOO_LARGE = "file_too_large"
    TOO_MANY_PAGES = "too_many_pages"
    ARCHIVE_LIMITS = "archive_limits"
    RENDER_FAILED = "render_failed"
    # Шаги сводки (spec §6.7).
    NO_SOURCES = "no_sources"
    CONTEXT_OVERFLOW = "context_overflow"
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_TRUNCATED = "llm_truncated"
    LLM_INVALID_OUTPUT = "llm_invalid_output"


class StageError(Exception):
    """Контролируемая ошибка входа этапа. Обработка остальных файлов и докладов продолжается
    (spec §6)."""

    def __init__(self, code: FailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class StageContext:
    storage: Storage
    config: BaseModel
    tmp_dir: Path
    # Клиент модели нужен только этапам с uses_llm в графе.
    llm: "LlmClient | None" = None


class Stage(Protocol):
    name: ClassVar[str]
    version: ClassVar[str]
    config_model: ClassVar[type[BaseModel]]

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageOutput: ...


def config_hash(config: BaseModel) -> str:
    return sha256_hex(config.model_dump_json().encode())


def cache_key(stage: str, stage_version: str, input_hashes: list[str], cfg_hash: str) -> str:
    payload = {
        "stage": stage,
        "stage_version": stage_version,
        "contract_version": CONTRACT_VERSION,
        "inputs": sorted(input_hashes),
        "config": cfg_hash,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def make_meta(stage: Stage, inputs: StageInputs, *, stub: bool) -> ArtifactMeta:
    input_hashes = [ref.sha256 for ref in inputs.artifacts]
    return ArtifactMeta(
        stage=stage.name,
        stage_version=stage.version,
        contract_version=CONTRACT_VERSION,
        cache_key=cache_key(stage.name, stage.version, input_hashes, inputs.config_hash),
        input_hashes=input_hashes,
        config_hash=inputs.config_hash,
        produced_at=datetime.now(UTC),
        stub=stub,
    )


def single_source(inputs: StageInputs) -> ArtifactRef:
    sources = [ref for ref in inputs.artifacts if ref.name == SOURCE_INPUT]
    if len(sources) != 1 or len(inputs.artifacts) != 1:
        raise ValueError("этапу с областью file нужен ровно один вход source")
    return sources[0]


def read_source(ctx: StageContext, inputs: StageInputs) -> tuple[ArtifactRef, bytes]:
    source = single_source(inputs)
    return source, _read_checked(ctx, source)


def read_input(ctx: StageContext, inputs: StageInputs, name: str) -> bytes:
    """Вход этапа по имени: артефакт другого этапа или пакет фрагментов доклада."""
    refs = [ref for ref in inputs.artifacts if ref.name == name]
    if len(refs) != 1:
        raise ValueError(f"у этапа должен быть ровно один вход {name}")
    return _read_checked(ctx, refs[0])


def _read_checked(ctx: StageContext, ref: ArtifactRef) -> bytes:
    data = ctx.storage.get(ref.storage_key)
    if sha256_hex(data) != ref.sha256:
        raise ValueError(f"хэш {ref.storage_key} не совпадает с указанным во входе")
    return data


def publish(
    ctx: StageContext, meta: ArtifactMeta, artifact: BaseModel, warnings: list[str]
) -> StageOutput:
    data = artifact.model_dump_json(indent=2).encode()
    key = artifact_key(meta.stage, meta.cache_key)
    ctx.storage.put(key, data)
    return StageOutput(
        artifact=ArtifactRef(name=meta.stage, storage_key=key, sha256=sha256_hex(data)),
        meta=meta,
        warnings=warnings,
    )
