"""Сущности предметной области (spec §7.1, ADR-008, ADR-014)."""

import datetime as dt
from typing import Self
from uuid import UUID

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator
from pydantic_core import PydanticCustomError

from rapporteur.contracts.base import (
    ContractModel,
    Milliseconds,
    NonEmptyStr,
    Probability,
    Sha256,
    require_interval,
)
from rapporteur.contracts.enums import (
    FileKind,
    InferenceEngine,
    PersonRole,
    SegmentKind,
    TalkStatus,
)


class Conference(ContractModel):
    """Конференция. Неизвестные сведения остаются пустыми (spec §4.2)."""

    id: UUID
    title: NonEmptyStr | None
    start_date: dt.date | None
    end_date: dt.date | None
    place: NonEmptyStr | None
    organizer: NonEmptyStr | None

    @model_validator(mode="after")
    def _dates_in_order(self) -> Self:
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise PydanticCustomError("dates_out_of_order", "конец раньше начала")
        return self


class Section(ContractModel):
    """Секция. Дата — значение по умолчанию для её докладов."""

    id: UUID
    conference_id: UUID
    title: NonEmptyStr
    date: dt.date | None
    order: PositiveInt


class Speaker(ContractModel):
    """Докладчик или соавтор. Порядок — порядок в списке доклада."""

    name: NonEmptyStr
    affiliation: NonEmptyStr | None = None
    role: PersonRole = PersonRole.SPEAKER


class Talk(ContractModel):
    """Доклад реестра. Название и докладчики в справку идут отсюда, а не из ответа модели."""

    id: UUID
    section_id: UUID
    order: PositiveInt
    title: NonEmptyStr | None
    date: dt.date | None
    status: TalkStatus
    speakers: list[Speaker]

    @model_validator(mode="after")
    def _insufficient_needs_title(self) -> Self:
        if self.status is TalkStatus.INSUFFICIENT_DATA and self.title is None:
            raise PydanticCustomError(
                "insufficient_without_title",
                "доклад без материалов и без названия не заводится (ADR-008)",
            )
        return self


class SourceFile(ContractModel):
    """Файл конференции. Оригинал на томе общий по SHA-256, эта запись — у каждой своя."""

    id: UUID
    conference_id: UUID
    sha256: Sha256
    original_name: NonEmptyStr
    kind: FileKind
    media_type: NonEmptyStr
    size_bytes: NonNegativeInt
    duration_ms: Milliseconds | None
    encoding: NonEmptyStr | None


class Segment(ContractModel):
    """Отрезок записи. Таймкоды абсолютные, от начала файла."""

    id: UUID
    source_file_id: UUID
    start_ms: Milliseconds
    end_ms: Milliseconds
    talk_id: UUID | None
    kind: SegmentKind
    confidence: Probability | None
    confirmed: bool

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        require_interval(self.start_ms, self.end_ms)
        if self.kind is SegmentKind.SERVICE and self.talk_id is not None:
            raise PydanticCustomError(
                "service_segment_with_talk", "служебный отрезок не относится к докладу"
            )
        if self.kind is not SegmentKind.SERVICE and self.talk_id is None:
            raise PydanticCustomError("segment_without_talk", "отрезку доклада нужен talk_id")
        return self


class GenerationParams(ContractModel):
    temperature: float = Field(ge=0)
    reasoning: bool
    max_tokens: PositiveInt
    context_tokens: PositiveInt


class ModelProfile(ContractModel):
    """Профиль модели. Конвейер видит только профиль; движок — забота клиента (ADR-010)."""

    name: NonEmptyStr
    engine: InferenceEngine
    model: NonEmptyStr
    weights_revision: NonEmptyStr
    quantization: NonEmptyStr
    generation: GenerationParams
    prompt_version: NonEmptyStr
    stages: list[NonEmptyStr]
