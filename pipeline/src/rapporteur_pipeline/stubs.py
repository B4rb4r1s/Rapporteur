"""Заглушки этапов для профиля dev и тестов без LibreOffice и без модели (ADR-007).

Артефакт заглушки помечен meta.stub = True и содержит вымышленный текст: заглушка не выдаёт
себя за разбор настоящего файла (spec §17).
"""

import io
from typing import ClassVar

from PIL import Image
from pydantic import BaseModel

from rapporteur_contracts import (
    FactsArtifact,
    FactsDraft,
    FragmentDraft,
    InventoryArtifact,
    NotesArtifact,
    SlidesArtifact,
    StageInputs,
    StageOutput,
    SummaryArtifact,
    SummaryLine,
    TalkFragmentPack,
    TranscriptArtifact,
    VerifiedClaimsArtifact,
)
from rapporteur_contracts.artifacts import SlideRecord, TitleCandidates, TranscriptSegment
from rapporteur_contracts.claims import EvidenceDraft, FactDraft
from rapporteur_contracts.enums import FragmentKind, SummaryStatus, TalkType, TextOrigin
from rapporteur_contracts.fragments import LinesLocator, PageLocator, TimeLocator
from rapporteur_pipeline.ingest.limits import NotesConfig, PresentationConfig
from rapporteur_pipeline.stage import (
    StageContext,
    make_meta,
    publish,
    read_input,
    single_source,
)
from rapporteur_pipeline.storage import derived_key
from rapporteur_pipeline.summarize import facts as facts_step
from rapporteur_pipeline.summarize import summary as summary_step
from rapporteur_pipeline.summarize.checks import usable_claims

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


_STUB_PROFILE = "stub"
_STUB_SUMMARY = "Заглушка: текст сводки не генерировался."
_QUOTE_WORDS = 12


class StubLlmConfig(BaseModel):
    """У заглушек этапов с LLM настроек нет: профиль модели им не нужен."""


class StubFactsStage:
    """Факт-заглушка с цитатой из первого фрагмента пакета: дальше цепочка (verify, summary,
    справка) работает как настоящая."""

    name: ClassVar[str] = "facts"
    version: ClassVar[str] = "1.0.0-stub"
    config_model: ClassVar[type[BaseModel]] = StubLlmConfig

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageOutput:
        pack = TalkFragmentPack.model_validate_json(read_input(ctx, inputs, facts_step.PACK_INPUT))
        meta = make_meta(self, inputs, stub=True)
        source = next((f for f in pack.fragments if len(f.text.strip()) >= 10), None)
        problem = None
        if source is not None:
            quote = " ".join(source.text.split()[:_QUOTE_WORDS])
            problem = FactDraft(
                text=f"Заглушка: тема по фрагменту {source.label}",
                evidence=[EvidenceDraft(label=source.label, quote=quote)],
            )
        facts = FactsDraft(
            title=None,
            speakers=[],
            affiliations=[],
            talk_type=TalkType.OTHER,
            problem=problem,
            methods=[],
            results=[],
            conclusions=[],
            conclusions_absent_reason="заглушка: выводы не извлекались",
            listener_remarks=[],
            uncertain=[],
        )
        artifact = FactsArtifact(
            meta=meta,
            talk_id=pack.talk_id,
            profile=_STUB_PROFILE,
            prompt_version=_STUB_PROFILE,
            facts=facts,
        )
        return publish(ctx, meta, artifact, [_STUB_WARNING])


class StubSummaryStage:
    name: ClassVar[str] = "summary"
    version: ClassVar[str] = "1.0.0-stub"
    config_model: ClassVar[type[BaseModel]] = StubLlmConfig

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageOutput:
        facts = FactsArtifact.model_validate_json(read_input(ctx, inputs, summary_step.FACTS_INPUT))
        verified = VerifiedClaimsArtifact.model_validate_json(
            read_input(ctx, inputs, summary_step.VERIFY_INPUT)
        )
        pack = TalkFragmentPack.model_validate_json(
            read_input(ctx, inputs, summary_step.PACK_INPUT)
        )
        meta = make_meta(self, inputs, stub=True)
        usable = usable_claims(verified.claims)
        sentences = [SummaryLine(text=_STUB_SUMMARY, claim_ids=[usable[0].key])] if usable else []
        status = SummaryStatus.READY if usable else SummaryStatus.NEEDS_ATTENTION
        summary = summary_step.make_summary(
            meta.cache_key,
            pack,
            facts,
            verified.claims,
            summary_step.Written(status, sentences, [], [], 1),
            profile=_STUB_PROFILE,
            prompt_version=_STUB_PROFILE,
        )
        return publish(ctx, meta, SummaryArtifact(meta=meta, summary=summary), [_STUB_WARNING])


class TranscriptStubConfig(BaseModel):
    """У фиктивного распознавания нет параметров модели."""


class StubInventoryStage:
    """Пустая фиктивная инвентаризация для проверки графа в dev."""

    name: ClassVar[str] = "inventory"
    version: ClassVar[str] = "1.0.0-stub"
    config_model: ClassVar[type[BaseModel]] = TranscriptStubConfig

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageOutput:
        meta = make_meta(self, inputs, stub=True)
        artifact = InventoryArtifact(meta=meta, files=[], failures=[])
        return publish(ctx, meta, artifact, [_STUB_WARNING])


class StubTranscriptStage:
    """Фиктивная ASR для проверки worker-gpu без GPU. Звук не читается."""

    name: ClassVar[str] = "asr"
    version: ClassVar[str] = "1.0.0-stub"
    config_model: ClassVar[type[BaseModel]] = TranscriptStubConfig

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageOutput:
        source = single_source(inputs)
        meta = make_meta(self, inputs, stub=True)
        text = "ЗАГЛУШКА: это вымышленная расшифровка для проверки dev-профиля."
        artifact = TranscriptArtifact(
            meta=meta,
            source_sha256=source.sha256,
            engine="stub",
            segments=[
                TranscriptSegment(
                    start_ms=0,
                    end_ms=1000,
                    text=text,
                    confidence=None,
                    speaker=None,
                    low_quality=True,
                    words=[],
                )
            ],
            fragments=[
                FragmentDraft(
                    kind=FragmentKind.TRANSCRIPT,
                    source_sha256=source.sha256,
                    locator=TimeLocator(kind="time", start_ms=0, end_ms=1000),
                    raw_text=text,
                    normalized_text=text,
                    origin=TextOrigin.ASR,
                    engine_confidence=None,
                )
            ],
        )
        return publish(ctx, meta, artifact, [_STUB_WARNING])
