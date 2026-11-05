"""Публикует метаданные приёма как артефакт первого этапа графа."""

from collections import Counter
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from rapporteur_contracts import InventoryArtifact, StageInputs, StageOutput
from rapporteur_contracts.artifacts import InventoryItem
from rapporteur_pipeline.stage import SOURCE_INPUT, StageContext, make_meta, publish
from rapporteur_pipeline.storage import blob_key


class InventoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    files: list[InventoryItem]


class InventoryStage:
    name: ClassVar[str] = "inventory"
    version: ClassVar[str] = "1.0.0"
    config_model: ClassVar[type[BaseModel]] = InventoryConfig

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageOutput:
        config = ctx.config
        assert isinstance(config, InventoryConfig)
        if Counter(file.sha256 for file in config.files) != Counter(
            ref.sha256 for ref in inputs.artifacts
        ) or any(
            ref.name != SOURCE_INPUT or ref.storage_key != blob_key(ref.sha256)
            for ref in inputs.artifacts
        ):
            raise ValueError("Метаданные инвентаризации не соответствуют оригиналам")
        meta = make_meta(self, inputs, stub=False)
        return publish(ctx, meta, InventoryArtifact(meta=meta, files=config.files, failures=[]), [])
