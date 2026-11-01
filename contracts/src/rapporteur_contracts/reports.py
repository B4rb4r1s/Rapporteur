"""Справка: выборка, вход сборки и выдача (spec §6.9, ADR-006, ADR-008)."""

import datetime as dt
from typing import Self
from uuid import UUID

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator
from pydantic_core import PydanticCustomError

from rapporteur_contracts.base import ContractModel, NonEmptyStr, Sha256, UtcDatetime
from rapporteur_contracts.enums import (
    REPORTABLE_TALK_STATUSES,
    ReportFormat,
    TalkStatus,
)


class ReportSelection(ContractModel):
    """talk_ids — итоговый список докладов; days и section_ids хранят выбор пользователя."""

    days: list[dt.date]
    section_ids: list[UUID]
    talk_ids: list[UUID] = Field(min_length=1)


class Report(ContractModel):
    id: UUID
    author_id: UUID
    conference_id: UUID
    selection: ReportSelection
    template_version: NonEmptyStr
    version: PositiveInt


class ReportTalk(ContractModel):
    """Доклад в справке. Название и докладчики — из реестра, текст — из сводки."""

    talk_id: UUID
    section_title: NonEmptyStr
    title: NonEmptyStr
    speakers: list[NonEmptyStr]
    affiliations: list[NonEmptyStr]
    status: TalkStatus
    summary: NonEmptyStr
    conclusions: list[NonEmptyStr]

    @model_validator(mode="after")
    def _reportable(self) -> Self:
        if self.status not in REPORTABLE_TALK_STATUSES:
            raise PydanticCustomError(
                "talk_not_reportable",
                "доклад со статусом {status} не входит в справку",
                {"status": self.status.value},
            )
        return self


class SkippedTalk(ContractModel):
    """Выбранный доклад, который не вошёл в справку. Показывается пользователю."""

    talk_id: UUID
    title: NonEmptyStr | None
    status: TalkStatus


class ReportInput(ContractModel):
    """Снимок входных данных сборки. Хранится в версии справки."""

    conference_title: NonEmptyStr | None
    period_start: dt.date | None
    period_end: dt.date | None
    place: NonEmptyStr | None
    organizer: NonEmptyStr | None
    selection: ReportSelection
    talks: list[ReportTalk]
    skipped: list[SkippedTalk]
    template_version: NonEmptyStr
    unreviewed_confirmed: bool

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        covered = [talk.talk_id for talk in self.talks] + [talk.talk_id for talk in self.skipped]
        if sorted(covered) != sorted(self.selection.talk_ids):
            raise PydanticCustomError(
                "selection_mismatch", "выборка не совпадает с докладами справки"
            )
        unreviewed = any(talk.status is not TalkStatus.REVIEWED for talk in self.talks)
        if unreviewed and not self.unreviewed_confirmed:
            raise PydanticCustomError(
                "unreviewed_not_confirmed",
                "сборка с непроверенными докладами требует явного подтверждения",
            )
        return self


class ReportVariables(ContractModel):
    """Значения, которые считает код, а не модель."""

    talks_count: NonNegativeInt
    speakers_count: NonNegativeInt


class ReportFile(ContractModel):
    format: ReportFormat
    storage_key: NonEmptyStr
    sha256: Sha256
    size_bytes: NonNegativeInt


class ReportVersion(ContractModel):
    id: UUID
    report_id: UUID
    number: PositiveInt
    input: ReportInput
    variables: ReportVariables
    files: list[ReportFile]
    created_at: UtcDatetime
