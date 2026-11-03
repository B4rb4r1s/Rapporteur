"""Заглушки этапов для профиля dev и тестов без LibreOffice (ADR-007).

Артефакт заглушки помечен meta.stub = True и содержит вымышленный текст: заглушка не выдаёт
себя за разбор настоящего файла (spec §17).
"""

import io
from typing import ClassVar

from PIL import Image
from pydantic import BaseModel

from rapporteur_contracts import (
    FragmentDraft,
    NotesArtifact,
    SlidesArtifact,
    StageInputs,
    StageOutput,
)
from rapporteur_contracts.artifacts import SlideRecord, TitleCandidates
from rapporteur_contracts.enums import FragmentKind, TextOrigin
from rapporteur_contracts.fragments import LinesLocator, PageLocator
from rapporteur_pipeline.ingest.limits import NotesConfig, PresentationConfig
from rapporteur_pipeline.stage import StageContext, make_meta, publish, single_source
from rapporteur_pipeline.storage import derived_key

_STUB_WARNING = "Результат заглушки: файл не разбирался"
_SLIDE_TEXT = (
    "Результаты: при нагрузке 1000 запросов в секунду политика LFU снизила задержку на 23 %."
)
_NOTE_TEXT = "Из зала спросили про холодный старт. Докладчик ответил, что он не исследовался."


def _placeholder_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (160, 90), "lightgray").save(buffer, format="PNG")
    return buffer.getvalue()


class StubSlidesStage:
    name: ClassVar[str] = "slides"
    version: ClassVar[str] = "1.0.0-stub"
    config_model: ClassVar[type[BaseModel]] = PresentationConfig

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageOutput:
        source = single_source(inputs)
        meta = make_meta(self, inputs, stub=True)
        image_key = derived_key(meta.cache_key, "slide-001.png")
        ctx.storage.put(image_key, _placeholder_png())
        artifact = SlidesArtifact(
            meta=meta,
            source_sha256=source.sha256,
            slides=[
                SlideRecord(
                    slide_no=1,
                    title="Результаты",
                    text=_SLIDE_TEXT,
                    tables=[],
                    notes=None,
                    image_path=image_key,
                    vlm_description=None,
                    is_conclusion=False,
                    has_text=True,
                )
            ],
            title_candidates=TitleCandidates(title=None, speakers=[], affiliations=[]),
            dictionary=["LFU"],
            fragments=[
                FragmentDraft(
                    kind=FragmentKind.SLIDE,
                    source_sha256=source.sha256,
                    locator=PageLocator(kind="page", page_no=1),
                    raw_text=_SLIDE_TEXT,
                    normalized_text=_SLIDE_TEXT,
                    origin=TextOrigin.EXTRACTED,
                    engine_confidence=None,
                )
            ],
        )
        return publish(ctx, meta, artifact, [_STUB_WARNING])


class StubNotesStage:
    name: ClassVar[str] = "notes"
    version: ClassVar[str] = "1.0.0-stub"
    config_model: ClassVar[type[BaseModel]] = NotesConfig

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageOutput:
        source = single_source(inputs)
        meta = make_meta(self, inputs, stub=True)
        artifact = NotesArtifact(
            meta=meta,
            source_sha256=source.sha256,
            encoding="utf-8",
            encoding_confident=True,
            fragments=[
                FragmentDraft(
                    kind=FragmentKind.NOTE,
                    source_sha256=source.sha256,
                    locator=LinesLocator(kind="lines", line_start=1, line_end=1),
                    raw_text=_NOTE_TEXT,
                    normalized_text=_NOTE_TEXT,
                    origin=TextOrigin.EXTRACTED,
                    engine_confidence=None,
                )
            ],
        )
        return publish(ctx, meta, artifact, [_STUB_WARNING])
