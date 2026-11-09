"""Фрагменты источников в двух слоях и пакет фрагментов доклада (ADR-003, ADR-008)."""

import re
from collections import defaultdict
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, PositiveInt, StringConstraints, model_validator
from pydantic_core import PydanticCustomError

from rapporteur.contracts.base import (
    ContractModel,
    Milliseconds,
    Probability,
    Sha256,
    UtcDatetime,
    require_interval,
)
from rapporteur.contracts.enums import FragmentKind, PackBlock, TextOrigin

_LABEL_PATTERN = (
    r"^(?:[SV](?:[1-9][0-9]*\.)?[1-9][0-9]*"
    r"|T(?:[1-9][0-9]*\.)?[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"|N[1-9][0-9]*)$"
)
_LABEL_PARTS = re.compile(r"^(?P<prefix>[STNV])(?:(?P<source>\d+)\.)?(?P<place>.+)$")

# Метка: S12, S2.12 — слайд; T00:12:34, T2.00:12:34 — стенограмма; N3 — заметка; V12 — описание VLM.
Label = Annotated[str, StringConstraints(pattern=_LABEL_PATTERN)]

_PREFIX_BY_KIND = {
    FragmentKind.SLIDE: "S",
    FragmentKind.SLIDE_DESCRIPTION: "V",
    FragmentKind.TRANSCRIPT: "T",
    FragmentKind.NOTE: "N",
}


class PageLocator(ContractModel):
    """Слайд презентации или страница документа."""

    kind: Literal["page"]
    page_no: PositiveInt


class TimeLocator(ContractModel):
    """Отрезок записи, таймкоды от начала файла."""

    kind: Literal["time"]
    start_ms: Milliseconds
    end_ms: Milliseconds

    @model_validator(mode="after")
    def _interval(self) -> Self:
        require_interval(self.start_ms, self.end_ms)
        return self


class LinesLocator(ContractModel):
    """Строки текстового файла, нумерация с единицы, конец включён."""

    kind: Literal["lines"]
    line_start: PositiveInt
    line_end: PositiveInt

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.line_start > self.line_end:
            raise PydanticCustomError("empty_interval", "начало должно быть не позже конца")
        return self


Locator = Annotated[PageLocator | TimeLocator | LinesLocator, Field(discriminator="kind")]

_LOCATOR_BY_KIND: dict[FragmentKind, tuple[type[ContractModel], ...]] = {
    FragmentKind.SLIDE: (PageLocator,),
    FragmentKind.SLIDE_DESCRIPTION: (PageLocator,),
    FragmentKind.TRANSCRIPT: (TimeLocator,),
    FragmentKind.NOTE: (LinesLocator,),
    FragmentKind.PROGRAM: (PageLocator, LinesLocator),
}


def _require_locator(kind: FragmentKind, locator: ContractModel) -> None:
    if not isinstance(locator, _LOCATOR_BY_KIND[kind]):
        raise PydanticCustomError(
            "locator_mismatch",
            "локатор не подходит к виду фрагмента {kind}",
            {"kind": kind.value},
        )


def _require_vlm_origin(kind: FragmentKind, origin: TextOrigin) -> None:
    if (kind is FragmentKind.SLIDE_DESCRIPTION) != (origin is TextOrigin.VLM):
        raise PydanticCustomError(
            "origin_mismatch", "текст VLM хранится только во фрагменте slide_description"
        )


class FragmentDraft(ContractModel):
    """Кэшируемый фрагмент: только файл и локатор, без UUID и меток, поэтому артефакт
    переиспользуется между конференциями. Текст слайда включает заметки докладчика."""

    kind: FragmentKind
    source_sha256: Sha256
    locator: Locator
    raw_text: str
    normalized_text: str
    origin: TextOrigin
    engine_confidence: Probability | None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _require_locator(self.kind, self.locator)
        _require_vlm_origin(self.kind, self.origin)
        return self


class SourceFragment(ContractModel):
    """Фрагмент конференции. Метки здесь нет: её назначает сборка пакета."""

    id: UUID
    conference_id: UUID
    source_file_id: UUID
    kind: FragmentKind
    locator: Locator

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _require_locator(self.kind, self.locator)
        return self


class FragmentRevision(ContractModel):
    """Неизменяемая версия текста. normalized_text — отображаемый текст: регистр, пунктуация,
    числа и ё сохранены; span считается по нему."""

    id: UUID
    fragment_id: UUID
    revision: PositiveInt
    raw_text: str
    normalized_text: str
    origin: TextOrigin
    edited_by: UUID | None
    created_at: UtcDatetime

    @model_validator(mode="after")
    def _human_edit_has_author(self) -> Self:
        if (self.origin is TextOrigin.HUMAN) != (self.edited_by is not None):
            raise PydanticCustomError(
                "edit_author_mismatch", "автор указан тогда и только тогда, когда правил человек"
            )
        return self


def _timecode(ms: int) -> str:
    seconds = ms // 1000
    return f"{seconds // 3600:02}:{seconds % 3600 // 60:02}:{seconds % 60:02}"


class PackedFragment(ContractModel):
    """Фрагмент в пакете доклада: метка и текст конкретной ревизии."""

    label: Label
    kind: FragmentKind
    fragment_id: UUID
    revision_id: UUID
    source_file_id: UUID
    locator: Locator
    origin: TextOrigin
    text: str
    block: PackBlock
    low_quality: bool

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.kind is FragmentKind.PROGRAM:
            raise PydanticCustomError("program_in_pack", "программа не служит источником фактов")
        _require_locator(self.kind, self.locator)
        _require_vlm_origin(self.kind, self.origin)

        parts = _LABEL_PARTS.match(self.label)
        assert parts is not None  # метка уже прошла шаблон
        expected_place: str | None = None
        if isinstance(self.locator, PageLocator):
            expected_place = str(self.locator.page_no)
        elif isinstance(self.locator, TimeLocator):
            expected_place = _timecode(self.locator.start_ms)
        if parts["prefix"] != _PREFIX_BY_KIND[self.kind] or expected_place not in (
            None,
            parts["place"],
        ):
            raise PydanticCustomError(
                "label_mismatch",
                "метка {label} не соответствует фрагменту",
                {"label": self.label},
            )

        if self.block is PackBlock.QA and self.kind is not FragmentKind.TRANSCRIPT:
            raise PydanticCustomError(
                "qa_block_mismatch", "в блоке вопросов бывает только стенограмма"
            )
        return self

    @property
    def source_index(self) -> int | None:
        parts = _LABEL_PARTS.match(self.label)
        assert parts is not None
        return int(parts["source"]) if parts["source"] else None


class TalkFragmentPack(ContractModel):
    """Все фрагменты ровно одного доклада: вход этапов сводки и единственное пространство
    поиска цитат. Собирает обработка доклада; цитата из другого доклада здесь не находится."""

    conference_id: UUID
    talk_id: UUID
    fragments: list[PackedFragment]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        labels = [fragment.label for fragment in self.fragments]
        if len(labels) != len(set(labels)):
            raise PydanticCustomError("duplicate_label", "метки внутри пакета уникальны")

        files: dict[tuple[FragmentKind, int | None], set[UUID]] = defaultdict(set)
        for fragment in self.fragments:
            if fragment.kind is not FragmentKind.NOTE:
                files[fragment.kind, fragment.source_index].add(fragment.source_file_id)

        kinds = {kind for kind, _ in files}
        for kind in kinds:
            indexes = {index for fragment_kind, index in files if fragment_kind is kind}
            if len(indexes) > 1 and None in indexes:
                raise PydanticCustomError(
                    "ambiguous_source", "при нескольких файлах номер источника обязателен"
                )
        if any(len(ids) > 1 for ids in files.values()):
            raise PydanticCustomError(
                "ambiguous_source", "один номер источника указывает на разные файлы"
            )
        return self

    def by_label(self) -> dict[str, PackedFragment]:
        return {fragment.label: fragment for fragment in self.fragments}
