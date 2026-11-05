import json
import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from rapporteur_contracts import ArtifactRef, PipelineGraph, StageInputs, StageOutput
from rapporteur_contracts.enums import FileKind, HumanGate, JobStatus, ResourceKind, TalkStatus
from rapporteur_pipeline.graph import GraphError, load_graph
from rapporteur_pipeline.stage import Stage, StageContext, make_meta
from rapporteur_pipeline.storage import DirectoryStorage, artifact_key, blob_key, sha256_hex
from rapporteur_pipeline.stubs import (
    StubInventoryStage,
    StubNotesStage,
    StubSlidesStage,
    StubTranscriptStage,
    TranscriptStubConfig,
)
from rapporteur_workers import runtime
from rapporteur_workers.cli import main
from rapporteur_workers.queue import Queue, QueueJob
from rapporteur_workers.runtime import execute
from rapporteur_workers.scheduler import (
    ConferenceState,
    FileState,
    PreparedJob,
    Scheduler,
    TalkState,
    plan,
)


class FactsHandler:
    """Только описание обработчика для тестов планирования; LLM не вызывается."""

    name: ClassVar[str] = "facts"
    version: ClassVar[str] = "test-1"
    config_model: ClassVar[type[BaseModel]] = TranscriptStubConfig

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageOutput:
        raise NotImplementedError


class VerifyHandler(FactsHandler):
    name: ClassVar[str] = "verify"


class SummaryHandler(FactsHandler):
    name: ClassVar[str] = "summary"


class SegmentHandler(FactsHandler):
    name: ClassVar[str] = "segment"


@pytest.fixture
def handlers() -> dict[str, Stage]:
    stages: list[Stage] = [
        StubInventoryStage(),
        StubNotesStage(),
        StubSlidesStage(),
        StubTranscriptStage(),
        FactsHandler(),
        VerifyHandler(),
        SummaryHandler(),
        SegmentHandler(),
    ]
    return {stage.name: stage for stage in stages}


def source(kind: FileKind = FileKind.NOTES, data: bytes = b"synthetic") -> FileState:
    sha = sha256_hex(data)
    return FileState(
        id=uuid4(),
        kind=kind,
        source=ArtifactRef(name="source", storage_key=blob_key(sha), sha256=sha),
    )


@pytest.fixture
def state() -> ConferenceState:
    files = [source(data=b"one"), source(data=b"two")]
    return ConferenceState(
        conference_id=uuid4(),
        files=files,
        talks=[
            TalkState(id=uuid4(), status=TalkStatus.MATCHED, file_ids=[file.id]) for file in files
        ],
    )


def completed(
    prepared: PreparedJob, handlers: Mapping[str, Stage], *, stub: bool = True
) -> QueueJob:
    meta = make_meta(handlers[prepared.stage], prepared.inputs, stub=stub)
    return QueueJob(
        id=uuid4(),
        dedup_key=prepared.key,
        stage=prepared.stage,
        resource=prepared.resource,
        mode=prepared.mode,
        inputs=prepared.inputs,
        config=prepared.config,
        status=JobStatus.SUCCEEDED,
        priority=0,
        attempt=1,
        max_attempts=4,
        lease_token=None,
        lease_until=None,
        heartbeat_at=None,
        output=StageOutput(
            artifact=ArtifactRef(
                name=prepared.stage,
                storage_key=artifact_key(prepared.stage, meta.cache_key),
                sha256=sha256_hex(meta.cache_key.encode()),
            ),
            meta=meta,
            warnings=[],
        ),
        error_code=None,
        created_at=datetime.now(UTC),
    )


def complete_stages(
    state: ConferenceState,
    handlers: Mapping[str, Stage],
    names: set[str],
    graph: PipelineGraph | None = None,
) -> list[QueueJob]:
    graph = graph or load_graph()
    jobs: list[QueueJob] = []
    for _ in range(len(graph.stages)):
        ready = [
            job for job in plan(graph, state, jobs, "dev", handlers).ready if job.stage in names
        ]
        if not ready:
            break
        jobs.extend(completed(job, handlers) for job in ready)
    return jobs


def test_only_inventory_is_ready_initially(
    state: ConferenceState, handlers: dict[str, Stage]
) -> None:
    result = plan(load_graph(), state, [], "dev", handlers)
    assert [job.stage for job in result.ready] == ["inventory"]
    assert not state.gates  # Автоматическое подтверждение не меняет переданный снимок.


def test_human_binding_gate_blocks_facts(
    state: ConferenceState, handlers: dict[str, Stage]
) -> None:
    jobs = complete_stages(state, handlers, {"inventory", "notes"})
    result = plan(load_graph(), state, jobs, "dev", handlers)
    assert not result.ready
    assert all(
        item.reason == "human_gates_not_confirmed"
        for item in result.decisions
        if item.stage == "facts"
    )
    state.gates.add(HumanGate.BINDING_CONFIRMED)
    assert len(plan(load_graph(), state, jobs, "dev", handlers).ready) == 2


def test_talk_waits_only_for_its_files(state: ConferenceState, handlers: dict[str, Stage]) -> None:
    state.gates.add(HumanGate.BINDING_CONFIRMED)
    jobs = complete_stages(state, handlers, {"inventory"})
    pending = plan(load_graph(), state, jobs, "dev", handlers).ready
    first = next(job for job in pending if job.inputs.artifacts == [state.files[0].source])
    jobs.append(completed(first, handlers))
    result = plan(load_graph(), state, jobs, "dev", handlers)
    facts = [job for job in result.ready if job.stage == "facts"]
    assert len(facts) == 1 and facts[0].inputs.talk_id == state.talks[0].id
    assert jobs[-1].output is not None
    assert facts[0].inputs.artifacts == [jobs[-1].output.artifact]


def test_conference_waits_for_all_recordings(
    state: ConferenceState, handlers: dict[str, Stage]
) -> None:
    state.gates.add(HumanGate.BINDING_CONFIRMED)
    state.files.extend([source(FileKind.AUDIO, b"a"), source(FileKind.VIDEO, b"b")])
    jobs = complete_stages(state, handlers, {"inventory", "notes"})
    pending = plan(load_graph(), state, jobs, "dev", handlers).ready
    jobs.append(completed(pending[0], handlers))
    assert not any(
        job.stage == "segment" for job in plan(load_graph(), state, jobs, "dev", handlers).ready
    )
    jobs.append(completed(pending[1], handlers))
    assert [job.stage for job in plan(load_graph(), state, jobs, "dev", handlers).ready] == [
        "segment"
    ]


def test_boundaries_are_required_when_recordings_exist(
    state: ConferenceState, handlers: dict[str, Stage]
) -> None:
    state.gates.add(HumanGate.BINDING_CONFIRMED)
    state.files.append(source(FileKind.AUDIO))
    state.talks[0].file_ids.append(state.files[-1].id)
    jobs = complete_stages(state, handlers, {"inventory", "notes", "asr", "segment"})
    assert not plan(load_graph(), state, jobs, "dev", handlers).ready
    state.gates.add(HumanGate.BOUNDARIES_CONFIRMED)
    assert all(
        job.stage == "facts" for job in plan(load_graph(), state, jobs, "dev", handlers).ready
    )


@pytest.mark.parametrize(
    "error,unblocks",
    [
        ("broken_file", True),
        ("handler_failure", False),
        ("llm_unavailable", False),
    ],
)
def test_only_file_failures_complete_file_dependencies(
    state: ConferenceState, handlers: dict[str, Stage], error: str, unblocks: bool
) -> None:
    state.gates.add(HumanGate.BINDING_CONFIRMED)
    jobs = complete_stages(state, handlers, {"inventory", "notes"})
    notes = [job for job in jobs if job.stage == "notes"]
    failed = notes[0].model_copy(
        update={"status": JobStatus.FAILED, "error_code": error, "output": None}
    )
    jobs = [failed if job.id == failed.id else job for job in jobs]
    result = plan(load_graph(), state, jobs, "dev", handlers)
    assert len([job for job in result.ready if job.stage == "facts"]) == (2 if unblocks else 1)
    assert any(item.state == "file_failure" for item in result.decisions) is unblocks


def test_no_inputs_skip_missing_asr_and_segment(
    state: ConferenceState, handlers: dict[str, Stage]
) -> None:
    state.gates.add(HumanGate.BINDING_CONFIRMED)
    handlers.pop("asr")
    handlers.pop("segment")
    jobs = complete_stages(state, handlers, {"inventory", "notes"})
    assert len(plan(load_graph(), state, jobs, "dev", handlers).ready) == 2


def test_present_unimplemented_stage_blocks_dependents(
    state: ConferenceState, handlers: dict[str, Stage]
) -> None:
    state.gates.update(HumanGate)
    state.files.append(source(FileKind.AUDIO))
    state.talks[0].file_ids.append(state.files[-1].id)
    handlers.pop("asr")
    jobs = complete_stages(state, handlers, {"inventory", "notes"})
    result = plan(load_graph(), state, jobs, "dev", handlers)
    assert not result.ready
    assert any(
        item.stage == "asr" and item.reason == "implementation_missing" for item in result.decisions
    )


@pytest.mark.parametrize(
    "status", [TalkStatus.DRAFT, TalkStatus.NOT_HELD, TalkStatus.INSUFFICIENT_DATA]
)
def test_inactive_talks_are_not_scheduled(
    state: ConferenceState, handlers: dict[str, Stage], status: TalkStatus
) -> None:
    state.gates.add(HumanGate.BINDING_CONFIRMED)
    state.talks[0].status = status
    jobs = complete_stages(state, handlers, {"inventory", "notes"})
    assert [
        job.inputs.talk_id for job in plan(load_graph(), state, jobs, "dev", handlers).ready
    ] == [state.talks[1].id]


def test_snapshot_rejects_unknown_file(state: ConferenceState) -> None:
    data = state.model_dump()
    data["talks"][0]["file_ids"] = [uuid4()]
    with pytest.raises(ValidationError, match="неизвестный файл"):
        ConferenceState.model_validate(data)


def test_file_to_file_edge_is_rejected(state: ConferenceState, handlers: dict[str, Stage]) -> None:
    graph = load_graph()
    graph = graph.model_copy(
        update={
            "stages": [
                stage.model_copy(update={"needs": ["slides"]}) if stage.name == "notes" else stage
                for stage in graph.stages
            ]
        }
    )
    with pytest.raises(GraphError, match="только от этапов конференции"):
        plan(graph, state, [], "dev", handlers)


def test_handler_version_change_invalidates_job(
    state: ConferenceState, handlers: dict[str, Stage]
) -> None:
    class NewNotes(StubNotesStage):
        version: ClassVar[str] = "2.0.0-stub"

    jobs = complete_stages(state, handlers, {"inventory", "notes"})
    handlers["notes"] = NewNotes()
    assert [job.stage for job in plan(load_graph(), state, jobs, "dev", handlers).ready] == [
        "notes",
        "notes",
    ]


def test_input_order_does_not_change_keys(
    state: ConferenceState, handlers: dict[str, Stage]
) -> None:
    original = plan(load_graph(), state, [], "dev", handlers).ready
    state.files.reverse()
    state.talks.reverse()
    assert plan(load_graph(), state, [], "dev", handlers).ready == original


def test_scheduler_rejects_wrong_conference_from_adapter(state: ConferenceState) -> None:
    scheduler = Scheduler(Queue("unused"), lambda _id: state, "dev")
    with pytest.raises(ValueError, match="другой конференции"):
        scheduler.preview(uuid4())


def test_advance_is_idempotent_under_concurrency(queue: Queue, state: ConferenceState) -> None:
    scheduler = Scheduler(queue, lambda _id: state, "dev")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: scheduler.advance(state.conference_id), range(2)))
    assert len(queue.for_conference(state.conference_id, "dev")) == 1
    assert len({job for result in results for job in result.job_ids}) == 1
    assert scheduler.advance(state.conference_id).job_ids == []


def test_default_graph_runs_inventory_then_notes(
    queue: Queue, state: ConferenceState, tmp_path: Path
) -> None:
    storage = DirectoryStorage(tmp_path / "data")
    scheduler = Scheduler(queue, lambda _id: state, "dev")
    first = scheduler.advance(state.conference_id)
    assert len(first.job_ids) == 1
    job = queue.claim(ResourceKind.CPU, "dev")
    assert job is not None and job.stage == "inventory"
    assert execute(queue, job, storage, tmp_path / "health")
    second = scheduler.advance(state.conference_id)
    assert len(second.job_ids) == 2
    for _ in range(2):
        job = queue.claim(ResourceKind.CPU, "dev")
        assert job is not None and job.stage == "notes"
        assert execute(queue, job, storage, tmp_path / "health")
    result = scheduler.advance(state.conference_id)
    assert not result.job_ids
    assert any(item.reason == "human_gates_not_confirmed" for item in result.plan.decisions)


def test_identical_source_in_two_conferences_keeps_cached_sha(
    queue: Queue, state: ConferenceState, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    barrier = threading.Barrier(2)

    class ConcurrentInventory(StubInventoryStage):
        def run(self, ctx: StageContext, inputs: StageInputs) -> StageOutput:
            output = super().run(ctx, inputs)
            barrier.wait(timeout=5)
            return output

    monkeypatch.setattr(runtime, "get_stage", lambda _name, _mode: ConcurrentInventory())
    storage = DirectoryStorage(tmp_path / "data")
    other = state.model_copy(deep=True, update={"conference_id": uuid4()})
    schedulers = [
        Scheduler(queue, lambda _id: state, "dev"),
        Scheduler(queue, lambda _id: other, "dev"),
    ]
    for scheduler, snapshot in zip(schedulers, [state, other], strict=True):
        scheduler.advance(snapshot.conference_id)
    jobs = [queue.claim(ResourceKind.CPU, "dev") for _ in range(2)]
    assert all(job is not None for job in jobs)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [
            pool.submit(execute, queue, job, storage, tmp_path / str(job.id))
            for job in jobs
            if job is not None
        ]
        assert all(task.result(timeout=10) for task in tasks)
    results = [queue.get(job.id) for job in jobs if job is not None]
    references = [
        result.output.artifact
        for result in results
        if result is not None and result.output is not None
    ]
    assert len(references) == 2 and references[0] == references[1]
    assert sha256_hex(storage.get(references[0].storage_key)) == references[0].sha256


def test_talk_to_talk_dependency_never_uses_another_talk(
    state: ConferenceState, handlers: dict[str, Stage]
) -> None:
    state.gates.add(HumanGate.BINDING_CONFIRMED)
    jobs = complete_stages(state, handlers, {"inventory", "notes"})
    facts = plan(load_graph(), state, jobs, "dev", handlers).ready
    jobs.append(completed(facts[0], handlers))
    result = plan(load_graph(), state, jobs, "dev", handlers)
    verify = [job for job in result.ready if job.stage == "verify"]
    assert len(verify) == 1 and verify[0].inputs.talk_id == facts[0].inputs.talk_id
    assert jobs[-1].output is not None
    assert verify[0].inputs.artifacts == [jobs[-1].output.artifact]


def test_config_change_invalidates_completed_stage(
    state: ConferenceState, handlers: dict[str, Stage]
) -> None:
    jobs = complete_stages(state, handlers, {"inventory", "notes"})
    state.configs["notes"] = {"max_file_bytes": 1024}
    assert [job.stage for job in plan(load_graph(), state, jobs, "dev", handlers).ready] == [
        "notes",
        "notes",
    ]


def test_gpu_plan_rejects_completed_stub_metadata(
    state: ConferenceState, handlers: dict[str, Stage]
) -> None:
    initial = plan(load_graph(), state, [], "gpu", handlers)
    jobs = [completed(initial.ready[0], handlers, stub=True)]
    result = plan(load_graph(), state, jobs, "gpu", handlers)
    assert not result.ready
    assert any(
        item.stage == "inventory" and item.reason == "invalid_output" for item in result.decisions
    )


def test_plan_and_advance_cli(
    queue: Queue,
    state: ConferenceState,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", queue.dsn)
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(state.model_dump_json(), encoding="utf-8")
    assert main(["--mode", "dev", "plan", str(snapshot)]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert [job["stage"] for job in preview["ready"]] == ["inventory"]
    assert queue.for_conference(state.conference_id, "dev") == []
    assert main(["--mode", "dev", "advance", str(snapshot)]) == 0
    assert len(json.loads(capsys.readouterr().out)["job_ids"]) == 1
    assert main(["--mode", "dev", "advance", str(snapshot)]) == 0
    assert json.loads(capsys.readouterr().out)["job_ids"] == []


def test_root_talk_stage_receives_only_its_sources(
    state: ConferenceState, handlers: dict[str, Stage]
) -> None:
    spec = next(stage for stage in load_graph().stages if stage.name == "facts")
    graph = PipelineGraph(version=1, stages=[spec.model_copy(update={"needs": [], "gates": []})])
    result = plan(graph, state, [], "dev", handlers)
    assert len(result.ready) == 2
    for job in result.ready:
        talk = next(talk for talk in state.talks if talk.id == job.inputs.talk_id)
        assert job.inputs.artifacts == [
            file.source for file in state.files if file.id in talk.file_ids
        ]


def test_root_conference_stage_filters_file_kinds(
    state: ConferenceState, handlers: dict[str, Stage]
) -> None:
    audio = source(FileKind.AUDIO)
    state.files.append(audio)
    spec = next(stage for stage in load_graph().stages if stage.name == "inventory")
    graph = PipelineGraph(
        version=1, stages=[spec.model_copy(update={"file_kinds": [FileKind.AUDIO]})]
    )
    result = plan(graph, state, [], "dev", handlers)
    assert result.ready[0].inputs.artifacts == [audio.source]
