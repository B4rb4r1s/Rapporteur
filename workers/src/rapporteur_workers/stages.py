"""Реестр реализованных обработчиков. Семантика графа здесь не дублируется."""

from rapporteur_contracts.enums import ResourceKind
from rapporteur_pipeline.stage import Stage
from rapporteur_pipeline.stubs import (
    StubInventoryStage,
    StubNotesStage,
    StubSlidesStage,
    StubTranscriptStage,
)
from rapporteur_workers.queue import Mode

RESOURCES = {
    "inventory": ResourceKind.CPU,
    "slides": ResourceKind.CPU,
    "notes": ResourceKind.CPU,
    "asr": ResourceKind.GPU,
}
FILE_STAGES = frozenset({"slides", "notes", "asr"})


def get_stage(name: str, mode: Mode) -> Stage:
    if mode == "dev":
        stubs: dict[str, Stage] = {
            "inventory": StubInventoryStage(),
            "slides": StubSlidesStage(),
            "notes": StubNotesStage(),
            "asr": StubTranscriptStage(),
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
    raise ValueError(f"Нет реализации этапа {name!r} для режима {mode!r}")
