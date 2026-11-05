"""Планирование графа по согласованному снимку конференции (ADR-009).

Снимок поставляет адаптер предметной БД. Здесь нет таблиц конференций, распознавания
файлов или обхода проверок человека: только вычисление готовых узлов и постановка задач.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from rapporteur_contracts import ArtifactRef, PipelineGraph, StageInputs
from rapporteur_contracts.enums import (
    FileKind,
    HumanGate,
    JobStatus,
    ResourceKind,
    StageScope,
    TalkStatus,
)
from rapporteur_pipeline.graph import check_graph, execution_order, load_graph
from rapporteur_pipeline.stage import SOURCE_INPUT, FailureCode, Stage, cache_key, config_hash
from rapporteur_workers.cache import valid_meta
from rapporteur_workers.queue import Mode, Queue, QueueJob
from rapporteur_workers.stages import RESOURCES, get_stage

_TALK_STATUSES = {TalkStatus.MATCHED, TalkStatus.PROCESSED, TalkStatus.REVIEWED, TalkStatus.STALE}
_RECORDINGS = {FileKind.AUDIO, FileKind.VIDEO}
_FILE_ERRORS = {
    code.value
    for code in (
        FailureCode.UNSUPPORTED_FORMAT,
        FailureCode.BROKEN_FILE,
        FailureCode.ENCRYPTED_FILE,
        FailureCode.FILE_TOO_LARGE,
        FailureCode.TOO_MANY_PAGES,
        FailureCode.ARCHIVE_LIMITS,
        FailureCode.RENDER_FAILED,
    )
}


class SnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileState(SnapshotModel):
    id: UUID
    kind: FileKind
    source: ArtifactRef

    @model_validator(mode="after")
    def source_name(self) -> Self:
        if self.source.name != SOURCE_INPUT:
            raise ValueError("Исходный файл должен иметь имя source")
        return self


class TalkState(SnapshotModel):
    id: UUID
    status: TalkStatus
    file_ids: list[UUID]


class ConferenceState(SnapshotModel):
    conference_id: UUID
    files: list[FileState]
    talks: list[TalkState]
    gates: set[HumanGate] = Field(default_factory=set)
    configs: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def references(self) -> Self:
        file_ids = {file.id for file in self.files}
        if len(file_ids) != len(self.files) or len({talk.id for talk in self.talks}) != len(
            self.talks
        ):
            raise ValueError("Идентификаторы файлов и докладов должны быть уникальны")
        for talk in self.talks:
            if len(set(talk.file_ids)) != len(talk.file_ids) or not set(talk.file_ids) <= file_ids:
                raise ValueError("Привязки доклада содержат повтор или неизвестный файл")
        return self


class Decision(SnapshotModel):
    stage: str
    scope: StageScope
    target_id: UUID | None
    state: Literal["ready", "existing", "complete", "file_failure", "skipped", "blocked"]
    reason: str | None = None
    job_id: UUID | None = None


class PreparedJob(SnapshotModel):
    key: str
    stage: str
    resource: ResourceKind
    mode: Mode
    inputs: StageInputs
    config: dict[str, JsonValue]


class Plan(SnapshotModel):
    ready: list[PreparedJob] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)


class AdvanceResult(SnapshotModel):
    plan: Plan
    job_ids: list[UUID]


@dataclass(frozen=True)
class Target:
    id: UUID
    source: ArtifactRef | None = None
    files: frozenset[UUID] = frozenset()


@dataclass(frozen=True)
class Outcome:
    done: bool
    artifact: ArtifactRef | None = None


def plan(
    graph: PipelineGraph,
    state: ConferenceState,
    jobs: Sequence[QueueJob],
    mode: Mode,
    handlers: Mapping[str, Stage] | None = None,
) -> Plan:
    """Чистое вычисление: не меняет снимок, очередь или хранилище."""
    check_graph(graph)
    specs = {stage.name: stage for stage in graph.stages}
    if not state.configs.keys() <= specs.keys():
        raise ValueError("Конфигурация содержит неизвестный этап графа")
    gates = set(state.gates)
    if not any(file.kind in _RECORDINGS for file in state.files):
        gates.add(HumanGate.BOUNDARIES_CONFIRMED)

    targets: dict[str, list[Target]] = {}
    for spec in graph.stages:
        files = [
            file for file in state.files if not spec.file_kinds or file.kind in spec.file_kinds
        ]
        if spec.scope is StageScope.FILE:
            targets[spec.name] = [Target(file.id, file.source) for file in files]
        elif spec.scope is StageScope.TALK:
            targets[spec.name] = [
                Target(talk.id, files=frozenset(talk.file_ids))
                for talk in state.talks
                if talk.status in _TALK_STATUSES
            ]
        else:
            targets[spec.name] = (
                [Target(state.conference_id)] if files or not spec.file_kinds else []
            )
        targets[spec.name].sort(key=lambda target: str(target.id))

    existing = {
        job.dedup_key: job
        for job in jobs
        if job.inputs.conference_id == state.conference_id and job.mode == mode
    }
    outcomes: dict[tuple[str, UUID], Outcome] = {}
    result = Plan()
    for name in execution_order(graph):
        spec = specs[name]
        if not targets[name]:
            result.decisions.append(
                Decision(
                    stage=name,
                    scope=spec.scope,
                    target_id=None,
                    state="skipped",
                    reason="no_inputs",
                )
            )
        for target in targets[name]:
            decision = Decision(stage=name, scope=spec.scope, target_id=target.id, state="blocked")
            result.decisions.append(decision)
            outcomes[name, target.id] = Outcome(False)
            dependencies: list[Outcome] = []
            for need in spec.needs:
                selected = targets[need]
                if spec.scope is StageScope.TALK:
                    if specs[need].scope is StageScope.FILE:
                        selected = [item for item in selected if item.id in target.files]
                    elif specs[need].scope is StageScope.TALK:
                        selected = [item for item in selected if item.id == target.id]
                dependencies.extend(outcomes[need, item.id] for item in selected)
            if any(not dependency.done for dependency in dependencies):
                decision.reason = "dependencies_not_ready"
                continue
            if not set(spec.gates) <= gates:
                decision.reason = "human_gates_not_confirmed"
                continue
            try:
                handler = get_stage(name, mode) if handlers is None else handlers.get(name)
            except ValueError:
                handler = None
            if handler is None:
                decision.reason = "implementation_missing"
                continue
            if handler.name != name or (handlers is None and RESOURCES[name] != spec.resource):
                raise ValueError("Обработчик не соответствует имени или ресурсу этапа")
            config = handler.config_model.model_validate(state.configs.get(name, {}))
            cfg_hash = config_hash(config)
            if spec.scope is StageScope.FILE:
                assert target.source is not None
                artifacts = [target.source]
            elif not spec.needs:
                artifacts = [
                    file.source
                    for file in sorted(state.files, key=lambda file: str(file.id))
                    if (spec.scope is not StageScope.TALK or file.id in target.files)
                    and (not spec.file_kinds or file.kind in spec.file_kinds)
                ]
            else:
                artifacts = [
                    dependency.artifact
                    for dependency in dependencies
                    if dependency.artifact is not None
                ]
            inputs = StageInputs(
                conference_id=state.conference_id,
                talk_id=target.id if spec.scope is StageScope.TALK else None,
                artifacts=artifacts,
                config_hash=cfg_hash,
            )
            key = f"graph:{state.conference_id}:{spec.scope.value}:{target.id}:{mode}:" + cache_key(
                name, handler.version, [ref.sha256 for ref in artifacts], cfg_hash
            )
            job = existing.get(key)
            if job is None:
                decision.state = "ready"
                result.ready.append(
                    PreparedJob(
                        key=key,
                        stage=name,
                        resource=spec.resource,
                        mode=mode,
                        inputs=inputs,
                        config=config.model_dump(mode="json"),
                    )
                )
            else:
                decision.job_id = job.id
                if job.status is JobStatus.SUCCEEDED and job.output is not None:
                    if job.output.artifact.name != name or not valid_meta(
                        job.output.meta, handler, inputs, mode
                    ):
                        decision.reason = "invalid_output"
                        continue
                    decision.state = "complete"
                    outcomes[name, target.id] = Outcome(True, job.output.artifact)
                elif (
                    spec.scope is StageScope.FILE
                    and job.status is JobStatus.FAILED
                    and job.error_code in _FILE_ERRORS
                ):
                    decision.state, decision.reason = "file_failure", job.error_code
                    outcomes[name, target.id] = Outcome(True)
                elif job.status in {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRY_WAIT}:
                    decision.state = "existing"
                else:
                    decision.reason = f"job_{job.status.value}"
    return result


class Scheduler:
    def __init__(
        self,
        queue: Queue,
        load_state: Callable[[UUID], ConferenceState],
        mode: Mode,
        *,
        graph: PipelineGraph | None = None,
        handlers: Mapping[str, Stage] | None = None,
    ) -> None:
        self.queue, self.load_state, self.mode = queue, load_state, mode
        self.graph = graph if graph is not None else load_graph()
        self.handlers = handlers

    def preview(self, conference_id: UUID) -> Plan:
        state = self.load_state(conference_id)
        if state.conference_id != conference_id:
            raise ValueError("Адаптер вернул состояние другой конференции")
        return plan(
            self.graph,
            state,
            self.queue.for_conference(conference_id, self.mode),
            self.mode,
            self.handlers,
        )

    def advance(self, conference_id: UUID, *, priority: int = 0) -> AdvanceResult:
        prepared = self.preview(conference_id)
        ids = [
            self.queue.enqueue(
                key=job.key,
                stage=job.stage,
                resource=job.resource,
                mode=job.mode,
                inputs=job.inputs,
                config=job.config,
                priority=priority,
            )
            for job in prepared.ready
        ]
        return AdvanceResult(plan=prepared, job_ids=ids)
