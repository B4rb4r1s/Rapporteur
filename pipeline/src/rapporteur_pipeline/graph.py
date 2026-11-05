"""Граф этапов из pipeline/graph.yaml (ADR-009).

Модель PipelineGraph проверяет форму и связи: уникальные имена, известные зависимости, нет
циклов, этап шире доклада не ждёт этап доклада. Здесь — правила, которые опираются на остальные
контракты и принципы спецификации.
"""

from graphlib import TopologicalSorter
from pathlib import Path

import yaml
from pydantic import ValidationError

from rapporteur_contracts import PUBLIC_MODELS, PipelineGraph
from rapporteur_contracts.enums import ResourceKind, StageScope

GRAPH_FILE = Path(__file__).resolve().parents[2] / "graph.yaml"

# Шаг сводки работает с содержанием одного доклада: изоляция докладов (spec §3, п. 1).
SUMMARY_OUTPUTS = frozenset({"FactsArtifact", "VerifiedClaimsArtifact", "SummaryArtifact"})


class GraphError(Exception):
    pass


def load_graph(path: Path = GRAPH_FILE) -> PipelineGraph:
    try:
        graph = PipelineGraph.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise GraphError(f"Граф этапов {path.name} не прошёл проверку: {error}") from error
    check_graph(graph)
    return graph


def check_graph(graph: PipelineGraph) -> None:
    problems: list[str] = []
    for stage in graph.stages:
        if stage.scope is StageScope.FILE and any(
            need.scope is not StageScope.CONFERENCE
            for need in graph.stages
            if need.name in stage.needs
        ):
            problems.append(f"{stage.name}: файловый этап зависит только от этапов конференции")
        model = PUBLIC_MODELS.get(stage.output)
        if model is None or "meta" not in model.model_fields:
            problems.append(f"{stage.name}: выход {stage.output} не артефакт из contracts/")
        if stage.uses_llm and stage.resource is not ResourceKind.GPU:
            problems.append(f"{stage.name}: LLM вызывает только worker-gpu, ресурс должен быть gpu")
        if stage.output in SUMMARY_OUTPUTS and stage.scope is not StageScope.TALK:
            problems.append(f"{stage.name}: этап шага сводки должен иметь область talk")
    if problems:
        raise GraphError("Граф этапов отклонён:\n" + "\n".join(problems))


def execution_order(graph: PipelineGraph) -> list[str]:
    """Один из допустимых порядков этапов: каждый этап после всех своих зависимостей."""
    sorter = TopologicalSorter({stage.name: stage.needs for stage in graph.stages})
    return list(sorter.static_order())
