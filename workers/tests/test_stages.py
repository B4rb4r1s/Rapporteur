"""Реестр этапов воркера: шаги сводки в dev и gpu, клиент модели (ADR-013)."""

import pytest

from rapporteur_contracts.enums import ResourceKind
from rapporteur_pipeline.graph import load_graph
from rapporteur_pipeline.llm.client import OllamaClient
from rapporteur_pipeline.llm.profiles import load_profile
from rapporteur_pipeline.report.stage import ReportStage
from rapporteur_pipeline.stubs import StubFactsStage, StubLlmConfig, StubSummaryStage
from rapporteur_pipeline.summarize.facts import FactsConfig, FactsStage
from rapporteur_pipeline.summarize.pack import PackConfig, PackStage
from rapporteur_pipeline.summarize.summary import SummaryConfig, SummaryStage
from rapporteur_pipeline.summarize.verify import VerifyStage
from rapporteur_workers.queue import Mode
from rapporteur_workers.stages import RESOURCES, get_stage, llm_client


def test_registered_resources_match_the_graph() -> None:
    specs = {stage.name: stage for stage in load_graph().stages}
    for name, resource in RESOURCES.items():
        if name != "report":
            assert specs[name].resource is resource, name
    assert {"pack", "facts", "verify", "summary"} <= RESOURCES.keys()
    # Справку собирает отдельная задача, а не узел графа.
    assert "report" not in specs and RESOURCES["report"] is ResourceKind.CPU


@pytest.mark.parametrize("mode", ["dev", "gpu"])
def test_stages_without_a_model_are_real_in_both_modes(mode: Mode) -> None:
    assert isinstance(get_stage("pack", mode), PackStage)
    assert isinstance(get_stage("verify", mode), VerifyStage)
    assert isinstance(get_stage("report", mode), ReportStage)


def test_dev_uses_llm_stubs() -> None:
    assert isinstance(get_stage("facts", "dev"), StubFactsStage)
    assert isinstance(get_stage("summary", "dev"), StubSummaryStage)


def test_gpu_uses_real_llm_stages() -> None:
    assert isinstance(get_stage("facts", "gpu"), FactsStage)
    assert isinstance(get_stage("summary", "gpu"), SummaryStage)


def test_llm_config_without_profile_takes_the_default() -> None:
    """Backend не передаёт профиль: планировщик и воркер берут профиль по умолчанию."""
    default = load_profile()

    assert FactsConfig.model_validate({}).profile == default
    assert SummaryConfig.model_validate({}).profile == default


def test_only_profiled_stages_get_a_model_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_URL", "http://ollama-test:11434/")

    client = llm_client(FactsConfig())

    assert isinstance(client, OllamaClient) and client.base_url == "http://ollama-test:11434"
    assert isinstance(llm_client(SummaryConfig()), OllamaClient)
    assert llm_client(StubLlmConfig()) is None
    assert llm_client(PackConfig()) is None
