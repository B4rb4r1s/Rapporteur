"""Реестр реализованных обработчиков. Семантика графа здесь не дублируется."""

import os
from collections.abc import Mapping

from rapporteur_contracts.enums import ResourceKind
from rapporteur_pipeline.ingest.inventory_stage import InventoryStage
from rapporteur_pipeline.llm.client import DEFAULT_OLLAMA_URL, LlmClient, make_client
from rapporteur_pipeline.report.stage import ReportStage
from rapporteur_pipeline.stage import Stage
from rapporteur_pipeline.stubs import (
    StubFactsStage,
    StubInventoryStage,
    StubNotesStage,
    StubSlidesStage,
    StubSummaryStage,
    StubTranscriptStage,
)
from rapporteur_pipeline.summarize.facts import FactsConfig, FactsStage
from rapporteur_pipeline.summarize.pack import PackStage
from rapporteur_pipeline.summarize.summary import SummaryConfig, SummaryStage
from rapporteur_pipeline.summarize.verify import VerifyStage
from rapporteur_workers.queue import Mode

RESOURCES = {
    "inventory": ResourceKind.CPU,
    "slides": ResourceKind.CPU,
    "notes": ResourceKind.CPU,
    "asr": ResourceKind.GPU,
    "pack": ResourceKind.CPU,
    "facts": ResourceKind.GPU,
    "verify": ResourceKind.CPU,
    "summary": ResourceKind.GPU,
    # Не узел графа: задачу ставит backend по запросу сборки справки (ADR-013).
    "report": ResourceKind.CPU,
}
FILE_STAGES = frozenset({"slides", "notes", "asr"})


def get_stage(name: str, mode: Mode, config: Mapping[str, object] | None = None) -> Stage:
    if name == "inventory" and config is not None and "files" in config:
        return InventoryStage()
    # Пакет, сверка цитат и справка не обращаются к модели: в dev они настоящие (ADR-013).
    if name == "pack":
        return PackStage()
    if name == "verify":
        return VerifyStage()
    if name == "report":
        return ReportStage()
    if mode == "dev":
        stubs: dict[str, Stage] = {
            "inventory": StubInventoryStage(),
            "slides": StubSlidesStage(),
            "notes": StubNotesStage(),
            "asr": StubTranscriptStage(),
            "facts": StubFactsStage(),
            "summary": StubSummaryStage(),
        }
        if name in stubs:
            return stubs[name]
    elif mode == "gpu":
        if name == "notes":
            from rapporteur_pipeline.ingest.notes import NotesStage

            return NotesStage()
        if name == "slides":
            # Linux-зависимый рендер не импортируется при запуске dev и утилит очереди.
            from rapporteur_pipeline.ingest.slides import SlidesStage

            return SlidesStage()
        if name == "facts":
            return FactsStage()
        if name == "summary":
            return SummaryStage()
    raise ValueError(f"Нет реализации этапа {name!r} для режима {mode!r}")


def llm_client(config: object) -> LlmClient | None:
    """Клиент модели для этапа, в конфигурации которого есть профиль. Заглушкам он не нужен."""
    if isinstance(config, FactsConfig | SummaryConfig):
        return make_client(config.profile, os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL))
    return None
