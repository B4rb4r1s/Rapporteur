"""Тела запросов API: идентификаторы, владелец, автор и версии задаются сервером."""

import datetime as dt
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from rapporteur_contracts.base import NonEmptyStr, Probability
from rapporteur_contracts.entities import TalkParticipant
from rapporteur_contracts.enums import TalkStatus, TalkType
from rapporteur_contracts.reports import ReportSelection


class WriteModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConferenceWrite(WriteModel):
    title: NonEmptyStr | None = None
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    place: NonEmptyStr | None = None
    organizer: NonEmptyStr | None = None

    @model_validator(mode="after")
    def dates(self) -> Self:
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("Дата окончания раньше начала")
        return self


class SectionWrite(WriteModel):
    title: NonEmptyStr
    date: dt.date | None = None
    hall: NonEmptyStr | None = None
    order: PositiveInt = 1


class PersonWrite(WriteModel):
    full_name: NonEmptyStr
    affiliation: NonEmptyStr | None = None


class TalkWrite(WriteModel):
    order: PositiveInt = 1
    title: NonEmptyStr | None = None
    date: dt.date | None = None
    talk_type: TalkType | None = None
    status: TalkStatus = TalkStatus.DRAFT
    match_confidence: Probability | None = None
    participants: list[TalkParticipant] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.status is TalkStatus.INSUFFICIENT_DATA and self.title is None:
            raise ValueError("Докладу без материалов требуется название")
        keys = {(person.person_id, person.role) for person in self.participants}
        positions = {(person.role, person.order) for person in self.participants}
        if len(keys) != len(self.participants) or len(positions) != len(self.participants):
            raise ValueError("Участники и их позиции в каждой роли должны быть уникальны")
        return self


class ReportWrite(WriteModel):
    selection: ReportSelection
    template_version: NonEmptyStr = "starter-1"

    @model_validator(mode="after")
    def unique_selection(self) -> Self:
        if (
            len(set(self.selection.days)) != len(self.selection.days)
            or len(set(self.selection.section_ids)) != len(self.selection.section_ids)
            or len(set(self.selection.talk_ids)) != len(self.selection.talk_ids)
        ):
            raise ValueError("Выборка справки содержит повтор")
        return self


class PriorityWrite(WriteModel):
    priority: int
