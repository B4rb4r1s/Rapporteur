"""Справка: выборка, вход сборки и выдача (spec §6.9, ADR-006, ADR-008)."""

import datetime as dt
from typing import Self
from uuid import UUID

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator
from pydantic_core import PydanticCustomError

from rapporteur_contracts.base import ContractModel, NonEmptyStr, Sha256, UtcDatetime
from rapporteur_contracts.claims import Summary
from rapporteur_contracts.entities import Conference, Person, Section, Talk
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


class ReportRequest(ContractModel):
    """Вход задачи report (ADR-013): снимок данных конференции на момент запроса сборки,
    включая текущие сводки докладов, в том числе исправленные человеком."""

    report: Report
    conference: Conference
    sections: list[Section]
    talks: list[Talk]
    people: list[Person]
    summaries: list[Summary]
    unreviewed_confirmed: bool

    @model_validator(mode="after")
    def _one_conference(self) -> Self:
        if self.report.conference_id != self.conference.id:
            raise PydanticCustomError(
                "report_conference_mismatch", "справка относится к другой конференции"
            )
        section_ids = {section.id for section in self.sections}
        if any(section.conference_id != self.conference.id for section in self.sections):
            raise PydanticCustomError(
                "section_conference_mismatch", "секция относится к другой конференции"
            )
        if any(talk.section_id not in section_ids for talk in self.talks):
            raise PydanticCustomError("talk_section_unknown", "доклад из неизвестной секции")
        talk_ids = {talk.id for talk in self.talks}
        if not set(self.report.selection.talk_ids) <= talk_ids:
            raise PydanticCustomError(
                "selection_talk_unknown", "в выборке есть доклад, которого нет в запросе"
            )
        summarized = [summary.talk_id for summary in self.summaries]
        if len(set(summarized)) != len(summarized) or not set(summarized) <= talk_ids:
            raise PydanticCustomError(
                "summary_talk_mismatch",
                "у доклада запроса одна текущая сводка, сводок чужих докладов нет",
            )
        return self
