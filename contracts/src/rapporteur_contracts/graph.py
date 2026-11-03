"""Декларативный граф этапов (ADR-009)."""

from graphlib import CycleError, TopologicalSorter
from typing import Self

from pydantic import Field, PositiveInt, model_validator
from pydantic_core import PydanticCustomError

from rapporteur_contracts.base import ContractModel, NonEmptyStr, StageName
from rapporteur_contracts.enums import FileKind, HumanGate, ResourceKind, StageScope


class StageSpec(ContractModel):
    """Этап конвейера. Этап с областью talk разворачивается в задачу на каждый доклад.

    file_kinds — входные файлы этапа. Этап с областью file запускается на каждый файл этих
    видов. Этап конференции с непустым списком нужен, только если такие файлы есть: без записей
    разбиение не запускается.
    """

    name: StageName
    scope: StageScope
    resource: ResourceKind
    needs: list[StageName]
    gates: list[HumanGate]
    uses_llm: bool
    output: NonEmptyStr
    file_kinds: list[FileKind] = Field(default_factory=list)

    @model_validator(mode="after")
    def _file_kinds_match_scope(self) -> Self:
        missing = self.scope is StageScope.FILE and not self.file_kinds
        forbidden = self.scope is StageScope.TALK and bool(self.file_kinds)
        if missing or forbidden:
            raise PydanticCustomError(
                "file_kinds_mismatch",
                "виды файлов нужны этапу с областью file и запрещены этапу доклада: {name}",
                {"name": self.name},
            )
        return self


class PipelineGraph(ContractModel):
    version: PositiveInt
    stages: list[StageSpec]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        stages = {stage.name: stage for stage in self.stages}
        if len(stages) != len(self.stages):
            raise PydanticCustomError("duplicate_stage", "имена этапов уникальны")

        for stage in self.stages:
            for need in stage.needs:
                if need not in stages:
                    raise PydanticCustomError(
                        "unknown_stage", "неизвестный этап {name}", {"name": need}
                    )
                if stage.scope is not StageScope.TALK and stages[need].scope is StageScope.TALK:
                    raise PydanticCustomError(
                        "talk_scope_fan_in",
                        "этап {name} шире доклада и не может зависеть от этапа доклада",
                        {"name": stage.name},
                    )

        try:
            TopologicalSorter({stage.name: stage.needs for stage in self.stages}).prepare()
        except CycleError as error:
            raise PydanticCustomError("stage_cycle", "в графе этапов есть цикл") from error
        return self
