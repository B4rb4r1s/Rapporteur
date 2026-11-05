"""Артефакты этапов и входы-выходы интерфейса Stage (spec §6, ADR-005, ADR-007, ADR-008)."""

from uuid import UUID

from pydantic import NonNegativeInt, PositiveInt

from rapporteur_contracts.base import (
    ContractModel,
    Milliseconds,
    NonEmptyStr,
    Probability,
    Sha256,
    StageName,
    UtcDatetime,
)
from rapporteur_contracts.claims import Claim, FactsDraft, Summary
from rapporteur_contracts.entities import Segment
from rapporteur_contracts.enums import FileKind
from rapporteur_contracts.fragments import FragmentDraft, TalkFragmentPack
from rapporteur_contracts.reports import ReportFile, ReportInput, ReportVariables


class ArtifactMeta(ContractModel):
    """Служебная часть каждого артефакта. stub явно помечает результат заглушки (spec §17)."""

    stage: StageName
    stage_version: NonEmptyStr
    contract_version: NonEmptyStr
    cache_key: Sha256
    input_hashes: list[Sha256]
    config_hash: Sha256
    produced_at: UtcDatetime
    stub: bool


class ArtifactRef(ContractModel):
    name: StageName
    storage_key: NonEmptyStr
    sha256: Sha256


class StageInputs(ContractModel):
    """Вход Stage.run. Артефакты этап читает сам через ctx.storage."""

    conference_id: UUID | None
    talk_id: UUID | None
    artifacts: list[ArtifactRef]
    config_hash: Sha256


class StageOutput(ContractModel):
    artifact: ArtifactRef
    meta: ArtifactMeta
    warnings: list[NonEmptyStr]


class InventoryItem(ContractModel):
    sha256: Sha256
    original_name: NonEmptyStr
    kind: FileKind
    media_type: NonEmptyStr
    size_bytes: NonNegativeInt
    duration_ms: Milliseconds | None
    encoding: NonEmptyStr | None


class FileFailure(ContractModel):
    """Ошибка одного файла не скрывает результаты остальных (spec §6)."""

    original_name: NonEmptyStr
    code: NonEmptyStr
    message: NonEmptyStr


class InventoryArtifact(ContractModel):
    """inventory.json, этап 6.1."""

    meta: ArtifactMeta
    files: list[InventoryItem]
    failures: list[FileFailure]


class SlideRecord(ContractModel):
    slide_no: PositiveInt
    title: str | None
    text: str
    tables: list[list[list[str]]]
    notes: str | None
    image_path: NonEmptyStr
    vlm_description: str | None
    is_conclusion: bool
    has_text: bool


class TitleCandidates(ContractModel):
    """Кандидаты с титульного слайда. Пустое поле означает «не найдено»."""

    title: NonEmptyStr | None
    speakers: list[NonEmptyStr]
    affiliations: list[NonEmptyStr]


class SlidesArtifact(ContractModel):
    """slides.json, этап 6.2, один файл презентации."""

    meta: ArtifactMeta
    source_sha256: Sha256
    slides: list[SlideRecord]
    title_candidates: TitleCandidates
    dictionary: list[NonEmptyStr]
    fragments: list[FragmentDraft]


class NotesArtifact(ContractModel):
    """notes.json, один файл заметок. Сомнение в кодировке решает пользователь (spec §5.2)."""

    meta: ArtifactMeta
    source_sha256: Sha256
    encoding: NonEmptyStr
    encoding_confident: bool
    fragments: list[FragmentDraft]


class TranscriptWord(ContractModel):
    start_ms: Milliseconds
    end_ms: Milliseconds
    text: NonEmptyStr
    confidence: Probability | None


class TranscriptSegment(ContractModel):
    start_ms: Milliseconds
    end_ms: Milliseconds
    text: str
    confidence: Probability | None
    speaker: NonEmptyStr | None
    low_quality: bool
    words: list[TranscriptWord]


class TranscriptArtifact(ContractModel):
    """transcript.json, этап 6.4. Таймкоды абсолютные, от начала исходного файла."""

    meta: ArtifactMeta
    source_sha256: Sha256
    engine: NonEmptyStr
    segments: list[TranscriptSegment]
    fragments: list[FragmentDraft]


class SegmentsArtifact(ContractModel):
    """segments.json, этап 6.5. unmatched_talk_ids — кандидаты в «не состоялся»."""

    meta: ArtifactMeta
    conference_id: UUID
    segments: list[Segment]
    unmatched_talk_ids: list[UUID]


class PackArtifact(ContractModel):
    """pack.json: пакет фрагментов одного доклада, общий вход шагов сводки (ADR-013)."""

    meta: ArtifactMeta
    pack: TalkFragmentPack


class FactsArtifact(ContractModel):
    """facts.json, шаг А. profile и prompt_version нужны сводке для Summary.prompt_versions."""

    meta: ArtifactMeta
    talk_id: UUID
    profile: NonEmptyStr
    prompt_version: NonEmptyStr
    facts: FactsDraft


class VerifiedClaimsArtifact(ContractModel):
    """Шаг Б: утверждения после сверки цитат, в том числе отклонённые с причиной."""

    meta: ArtifactMeta
    talk_id: UUID
    claims: list[Claim]


class SummaryArtifact(ContractModel):
    """summary.json, шаги В и Г."""

    meta: ArtifactMeta
    summary: Summary


class ReportArtifact(ContractModel):
    """Выдача задачи report. input, variables и files переходят в ReportVersion (ADR-013)."""

    meta: ArtifactMeta
    report_id: UUID
    input: ReportInput
    variables: ReportVariables
    files: list[ReportFile]
