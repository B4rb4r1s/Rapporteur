"""Граф этапов pipeline/graph.yaml (P0-08, ADR-009)."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from rapporteur_contracts import PipelineGraph
from rapporteur_contracts.enums import FileKind, HumanGate, ResourceKind, StageScope
from rapporteur_pipeline.cli import main
from rapporteur_pipeline.graph import (
    GRAPH_FILE,
    SUMMARY_OUTPUTS,
    GraphError,
    execution_order,
    load_graph,
)


@pytest.fixture(scope="module")
def graph() -> PipelineGraph:
    return load_graph()


def _stages(graph: PipelineGraph) -> dict[str, Any]:
    return {stage.name: stage for stage in graph.stages}


def test_phase1_stages_and_phase2_3_placeholders(graph: PipelineGraph) -> None:
    assert [stage.name for stage in graph.stages] == [
        "inventory",
        "slides",
        "notes",
        "asr",
        "segment",
        "facts",
        "verify",
        "summary",
    ]


def test_summary_step_is_isolated_per_talk(graph: PipelineGraph) -> None:
    summary_step = [stage for stage in graph.stages if stage.output in SUMMARY_OUTPUTS]

    assert [stage.name for stage in summary_step] == ["facts", "verify", "summary"]
    assert all(stage.scope is StageScope.TALK for stage in summary_step)


def test_only_segment_uses_llm_outside_a_talk(graph: PipelineGraph) -> None:
    """Исключение из принципа 1: поиск объявлений по окнам стенограммы сессии."""
    wide = [s.name for s in graph.stages if s.uses_llm and s.scope is not StageScope.TALK]

    assert wide == ["segment"]
    assert all(s.resource is ResourceKind.GPU for s in graph.stages if s.uses_llm)


def test_file_stages_know_their_inputs(graph: PipelineGraph) -> None:
    stages = _stages(graph)

    assert stages["slides"].file_kinds == [FileKind.PRESENTATION]
    assert stages["notes"].file_kinds == [FileKind.NOTES]
    assert stages["asr"].file_kinds == [FileKind.AUDIO, FileKind.VIDEO]
    # Без записей разбиение не нужно, и проверка границ проходит сама.
    assert stages["segment"].file_kinds == [FileKind.AUDIO, FileKind.VIDEO]


def test_facts_wait_for_both_human_checks(graph: PipelineGraph) -> None:
    facts = _stages(graph)["facts"]

    assert facts.gates == [HumanGate.BINDING_CONFIRMED, HumanGate.BOUNDARIES_CONFIRMED]
    assert set(facts.needs) == {"slides", "notes", "asr", "segment"}


def test_execution_order_respects_needs(graph: PipelineGraph) -> None:
    order = execution_order(graph)
    position = {name: index for index, name in enumerate(order)}

    assert order[0] == "inventory" and order[-1] == "summary"
    for stage in graph.stages:
        assert all(position[need] < position[stage.name] for need in stage.needs)


def _broken_graph(tmp_path: Path, change: dict[str, Any]) -> Path:
    document = yaml.safe_load(GRAPH_FILE.read_text(encoding="utf-8"))
    stages = {stage["name"]: stage for stage in document["stages"]}
    for name, fields in change.items():
        stages[name].update(fields)
    path = tmp_path / "graph.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"inventory": {"needs": ["summary"]}}, "talk_scope_fan_in"),
        ({"facts": {"needs": ["slides", "summary"]}}, "stage_cycle"),
        ({"verify": {"needs": ["transcribe"]}}, "unknown_stage"),
        ({"notes": {"file_kinds": []}}, "file_kinds_mismatch"),
        ({"summary": {"output": "SummaryDraft"}}, "не артефакт"),
        ({"summary": {"output": "Отчёт"}}, "не артефакт"),
        ({"facts": {"resource": "cpu"}}, "ресурс должен быть gpu"),
        ({"facts": {"scope": "conference"}}, "должен иметь область talk"),
    ],
    ids=[
        "conference-waits-for-talk",
        "cycle",
        "unknown-need",
        "file-stage-without-kinds",
        "output-not-artifact",
        "unknown-output",
        "llm-on-cpu",
        "summary-step-not-per-talk",
    ],
)
def test_broken_graph_is_rejected(tmp_path: Path, change: dict[str, Any], message: str) -> None:
    with pytest.raises(GraphError, match=message):
        load_graph(_broken_graph(tmp_path, change))


def test_cli_prints_order(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["graph"]) == 0
    output = capsys.readouterr().out

    assert "1. inventory → InventoryArtifact" in output
    assert "ждёт: binding_confirmed, boundaries_confirmed" in output


def test_cli_reports_broken_graph(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _broken_graph(tmp_path, {"facts": {"resource": "cpu"}})

    assert main(["graph", "--file", str(path)]) == 1
    assert "ресурс должен быть gpu" in capsys.readouterr().err
