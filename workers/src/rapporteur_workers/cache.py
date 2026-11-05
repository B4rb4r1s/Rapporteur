"""Переиспользование опубликованного артефакта без перезаписи его produced_at и SHA."""

from pydantic import BaseModel, ValidationError

from rapporteur_contracts import (
    ArtifactMeta,
    ArtifactRef,
    InventoryArtifact,
    NotesArtifact,
    SlidesArtifact,
    StageInputs,
    StageOutput,
    TranscriptArtifact,
)
from rapporteur_pipeline.stage import CONTRACT_VERSION, Stage, cache_key
from rapporteur_pipeline.storage import Storage, artifact_key, sha256_hex
from rapporteur_workers.queue import Mode

OUTPUTS: dict[str, type[BaseModel]] = {
    "inventory": InventoryArtifact,
    "notes": NotesArtifact,
    "slides": SlidesArtifact,
    "asr": TranscriptArtifact,
}


def valid_meta(meta: ArtifactMeta, stage: Stage, inputs: StageInputs, mode: Mode) -> bool:
    hashes = [ref.sha256 for ref in inputs.artifacts]
    expected = cache_key(stage.name, stage.version, hashes, inputs.config_hash)
    return (
        meta.stage == stage.name
        and meta.stage_version == stage.version
        and meta.contract_version == CONTRACT_VERSION
        and meta.cache_key == expected
        and meta.config_hash == inputs.config_hash
        and sorted(meta.input_hashes) == sorted(hashes)
        and not (mode == "gpu" and meta.stub)
    )


def cached_output(
    stage: Stage, inputs: StageInputs, mode: Mode, storage: Storage
) -> StageOutput | None:
    key = artifact_key(
        stage.name,
        cache_key(
            stage.name, stage.version, [ref.sha256 for ref in inputs.artifacts], inputs.config_hash
        ),
    )
    if stage.name not in OUTPUTS or not storage.exists(key):
        return None
    data = storage.get(key)
    try:
        artifact = OUTPUTS[stage.name].model_validate_json(data)
        meta = ArtifactMeta.model_validate(artifact.model_dump()["meta"])
    except (ValidationError, KeyError):
        return None
    if not valid_meta(meta, stage, inputs, mode):
        return None
    return StageOutput(
        artifact=ArtifactRef(name=stage.name, storage_key=key, sha256=sha256_hex(data)),
        meta=meta,
        warnings=["Результат из кэша заглушки" if meta.stub else "Результат из кэша"],
    )
