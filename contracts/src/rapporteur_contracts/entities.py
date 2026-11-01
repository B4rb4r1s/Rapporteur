"""Сущности предметной области (spec §7.1, ADR-006, ADR-008)."""

import datetime as dt
from typing import Self
from uuid import UUID

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator
from pydantic_core import PydanticCustomError

from rapporteur_contracts.base import (
    ContractModel,
    Milliseconds,
    NonEmptyStr,
    Probability,
    Sha256,
    UtcDatetime,
    require_interval,
)
from rapporteur_contracts.enums import (
    FileKind,
    InferenceEngine,
    JobStatus,
    PersonRole,
    ResourceKind,
    SegmentKind,
    TalkStatus,
    TalkType,
)


class Conference(ContractModel):
    """Конференция. Неизвестные сведения остаются пустыми (spec §4.2)."""

    id: UUID
    owner_id: UUID
    title: NonEmptyStr | None
    start_date: dt.date | None
    end_date: dt.date | None
    place: NonEmptyStr | None
    organizer: NonEmptyStr | None
    version: PositiveInt

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
    hall: NonEmptyStr | None
    order: PositiveInt


class Person(ContractModel):
    id: UUID
    full_name: NonEmptyStr
    affiliation: NonEmptyStr | None


class TalkParticipant(ContractModel):
    person_id: UUID
    role: PersonRole
    order: PositiveInt


class Talk(ContractModel):
    """Доклад реестра. Название и докладчики в справку идут отсюда, а не из ответа модели."""

    id: UUID
    section_id: UUID
    order: PositiveInt
    title: NonEmptyStr | None
    date: dt.date | None
    talk_type: TalkType | None
    status: TalkStatus
    match_confidence: Probability | None
    participants: list[TalkParticipant]
    version: PositiveInt

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


class FileBinding(ContractModel):
    """Привязка файла. talk_id пуст, если файл относится к конференции целиком."""

    id: UUID
    source_file_id: UUID
    talk_id: UUID | None
    confirmed: bool
    confidence: Probability | None


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


class Job(ContractModel):
    """Задача очереди (ADR-004). Ожидание проверки человеком статусом не является (ADR-009)."""

    id: UUID
    type: NonEmptyStr
    stage: NonEmptyStr | None
    conference_id: UUID | None
    talk_id: UUID | None
    resource: ResourceKind
    status: JobStatus
    priority: int
    attempt: NonNegativeInt
    lease_until: UtcDatetime | None
    heartbeat_at: UtcDatetime | None
    progress: Probability | None
    current_step: NonEmptyStr | None
    error_code: NonEmptyStr | None
    created_at: UtcDatetime
