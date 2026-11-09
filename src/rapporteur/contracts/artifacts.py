"""Результаты разбора файлов и шагов сводки, которые кэшируются на томе (spec §6, ADR-014)."""

from uuid import UUID

from pydantic import NonNegativeInt, PositiveInt

from rapporteur.contracts.base import (
    ContractModel,
    Milliseconds,
    NonEmptyStr,
    Probability,
    Sha256,
)
from rapporteur.contracts.claims import FactsDraft
from rapporteur.contracts.entities import Segment
from rapporteur.contracts.enums import FileKind
from rapporteur.contracts.fragments import FragmentDraft


class InventoryItem(ContractModel):
    """Метаданные принятого файла (spec §6.1)."""

    sha256: Sha256
    original_name: NonEmptyStr
    kind: FileKind
    media_type: NonEmptyStr
    size_bytes: NonNegativeInt
    duration_ms: Milliseconds | None
    encoding: NonEmptyStr | None


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
    """Разбор одной презентации (spec §6.2)."""

    source_sha256: Sha256
    slides: list[SlideRecord]
    title_candidates: TitleCandidates
    dictionary: list[NonEmptyStr]
    fragments: list[FragmentDraft]
    warnings: list[NonEmptyStr]


class NotesArtifact(ContractModel):
    """Один файл заметок. Сомнение в кодировке решает пользователь (spec §5.2)."""

    source_sha256: Sha256
    encoding: NonEmptyStr
    encoding_confident: bool
    fragments: list[FragmentDraft]
    warnings: list[NonEmptyStr]


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
    """Стенограмма записи, Ф2 (spec §6.4). Таймкоды абсолютные, от начала исходного файла."""

    source_sha256: Sha256
    engine: NonEmptyStr
    segments: list[TranscriptSegment]
    fragments: list[FragmentDraft]


class SegmentsArtifact(ContractModel):
    """Разбиение записей на доклады, Ф3 (spec §6.5). unmatched_talk_ids — кандидаты
    в «не состоялся»."""

    conference_id: UUID
    segments: list[Segment]
    unmatched_talk_ids: list[UUID]


class FactsArtifact(ContractModel):
    """Шаг А. profile и prompt_version нужны сводке для Summary.prompt_versions."""

    talk_id: UUID
    profile: NonEmptyStr
    prompt_version: NonEmptyStr
    facts: FactsDraft
    attempts: PositiveInt
