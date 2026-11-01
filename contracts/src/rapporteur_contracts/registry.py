"""Реестр в файле conference.yaml (spec §7.4). Идентификаторы здесь локальные: s1, t01."""

import datetime as dt
from typing import Annotated, Self

from pydantic import Field, PositiveInt, StringConstraints, model_validator
from pydantic_core import PydanticCustomError

from rapporteur_contracts.base import ContractModel, NonEmptyStr, Probability
from rapporteur_contracts.enums import TalkStatus

Timecode = Annotated[str, StringConstraints(pattern=r"^[0-9]{2}:[0-9]{2}:[0-9]{2}$")]


class RegistryConference(ContractModel):
    title: NonEmptyStr | None = None
    dates: list[dt.date] = Field(default_factory=list, max_length=2)
    place: NonEmptyStr | None = None


class RegistrySection(ContractModel):
    id: NonEmptyStr
    title: NonEmptyStr
    date: dt.date | None = None


class RegistryAudio(ContractModel):
    file: NonEmptyStr
    start: Timecode
    end: Timecode


class RegistrySources(ContractModel):
    slides: list[NonEmptyStr] = Field(default_factory=list)
    audio: list[RegistryAudio] = Field(default_factory=list)
    notes: list[NonEmptyStr] = Field(default_factory=list)


class RegistryTalk(ContractModel):
    id: NonEmptyStr
    section: NonEmptyStr
    order: PositiveInt
    date: dt.date | None = None
    title: NonEmptyStr | None = None
    speakers: list[NonEmptyStr] = Field(default_factory=list)
    coauthors: list[NonEmptyStr] = Field(default_factory=list)
    affiliations: list[NonEmptyStr] = Field(default_factory=list)
    sources: RegistrySources
    status: TalkStatus
    confidence: Probability | None = None


class ConferenceRegistry(ContractModel):
    conference: RegistryConference
    sections: list[RegistrySection]
    talks: list[RegistryTalk]

    @model_validator(mode="after")
    def _references(self) -> Self:
        sections = {section.id for section in self.sections}
        talks = {talk.id for talk in self.talks}
        if len(sections) != len(self.sections) or len(talks) != len(self.talks):
            raise PydanticCustomError("duplicate_registry_id", "идентификаторы реестра уникальны")
        for talk in self.talks:
            if talk.section not in sections:
                raise PydanticCustomError(
                    "unknown_section", "неизвестная секция {section}", {"section": talk.section}
                )
        return self
