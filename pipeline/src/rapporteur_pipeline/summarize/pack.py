"""Этап pack: пакет фрагментов одного доклада с метками (spec §6.7, ADR-003, ADR-013).

Пакет строится из выходов slides и notes файлов доклада. Идентификаторы выводятся из данных:
файл — из конференции и SHA-256 (общая с backend функция source_file_id), фрагмент — из файла
и локатора, ревизия — из фрагмента и текста. Поэтому повторная сборка даёт тот же пакет и тот же
ключ кэша, а id совпадают с записями БД без обращения к ней.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from rapporteur_contracts import (
    FragmentDraft,
    NotesArtifact,
    PackArtifact,
    PackedFragment,
    SlidesArtifact,
    StageInputs,
    StageOutput,
    TalkFragmentPack,
    source_file_id,
)
from rapporteur_contracts.enums import FragmentKind, PackBlock
from rapporteur_contracts.fragments import PageLocator
from rapporteur_pipeline.stage import (
    FailureCode,
    StageContext,
    StageError,
    make_meta,
    publish,
    read_input,
    read_ref,
)

PACK_INPUT = "pack"
SLIDES_INPUT = "slides"
NOTES_INPUT = "notes"
_SLIDE_PREFIX = {FragmentKind.SLIDE: "S", FragmentKind.SLIDE_DESCRIPTION: "V"}


@dataclass(frozen=True)
class PackSource:
    """Фрагменты одного файла доклада и id этого файла в конференции."""

    source_file_id: uuid.UUID
    fragments: Sequence[FragmentDraft]


def build_pack(
    conference_id: uuid.UUID,
    talk_id: uuid.UUID,
    presentations: Sequence[PackSource],
    notes: Sequence[PackSource] = (),
) -> TalkFragmentPack:
    """S12 — слайд 12; при нескольких презентациях S2.12. N — заметки подряд по всем файлам.
    Фрагменты без текста в пакет не входят: на них нельзя сослаться."""
    packed: list[PackedFragment] = []
    numbered = len(presentations) > 1
    for index, source in enumerate(presentations, start=1):
        for draft in source.fragments:
            if draft.kind not in _SLIDE_PREFIX or not draft.normalized_text.strip():
                continue
            assert isinstance(draft.locator, PageLocator)
            prefix = _SLIDE_PREFIX[draft.kind]
            place = f"{index}.{draft.locator.page_no}" if numbered else str(draft.locator.page_no)
            packed.append(_packed(talk_id, source, draft, f"{prefix}{place}"))
    note_no = 0
    for source in notes:
        for draft in source.fragments:
            if draft.kind is not FragmentKind.NOTE or not draft.normalized_text.strip():
                continue
            note_no += 1
            packed.append(_packed(talk_id, source, draft, f"N{note_no}"))
    return TalkFragmentPack(conference_id=conference_id, talk_id=talk_id, fragments=packed)


def _packed(
    talk_id: uuid.UUID, source: PackSource, draft: FragmentDraft, label: str
) -> PackedFragment:
    # Фрагмент принадлежит файлу, а не докладу: перепривязка файла его id не меняет.
    fragment_id = uuid.uuid5(
        source.source_file_id, f"{draft.kind.value}:{draft.locator.model_dump_json()}"
    )
    return PackedFragment(
        label=label,
        kind=draft.kind,
        fragment_id=fragment_id,
        revision_id=uuid.uuid5(fragment_id, draft.normalized_text),
        source_file_id=source.source_file_id,
        locator=draft.locator,
        origin=draft.origin,
        text=draft.normalized_text,
        block=PackBlock.MAIN,
        low_quality=False,
    )


class PackConfig(BaseModel):
    """У сборки пакета нет параметров: метки и id определяются входами."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PackStage:
    """Пакет доклада из выходов slides и notes его файлов. Порядок презентаций — порядок входов:
    его задаёт планировщик, а CLI — реестр. Стенограммы войдут в пакет в Ф2."""

    name: ClassVar[str] = "pack"
    version: ClassVar[str] = "1.0.0"
    config_model: ClassVar[type[BaseModel]] = PackConfig

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageOutput:
        if inputs.conference_id is None or inputs.talk_id is None:
            raise ValueError("этапу pack нужны конференция и доклад")
        presentations: list[PackSource] = []
        notes: list[PackSource] = []
        for ref in inputs.artifacts:
            data = read_ref(ctx, ref)
            if ref.name == SLIDES_INPUT:
                slides = SlidesArtifact.model_validate_json(data)
                presentations.append(
                    PackSource(
                        source_file_id(inputs.conference_id, slides.source_sha256),
                        slides.fragments,
                    )
                )
            elif ref.name == NOTES_INPUT:
                parsed = NotesArtifact.model_validate_json(data)
                notes.append(
                    PackSource(
                        source_file_id(inputs.conference_id, parsed.source_sha256),
                        parsed.fragments,
                    )
                )
            else:
                raise ValueError(f"этап pack не принимает вход {ref.name}: стенограммы — в Ф2")
        pack = build_pack(inputs.conference_id, inputs.talk_id, presentations, notes)
        if not pack.fragments:
            raise StageError(FailureCode.NO_SOURCES, "В материалах доклада нет текста")
        meta = make_meta(self, inputs, stub=False)
        return publish(ctx, meta, PackArtifact(meta=meta, pack=pack), [])


def read_pack(ctx: StageContext, inputs: StageInputs) -> TalkFragmentPack:
    """Пакет из входа pack; доклад пакета должен совпадать с докладом задачи."""
    pack = PackArtifact.model_validate_json(read_input(ctx, inputs, PACK_INPUT)).pack
    if inputs.talk_id is not None and inputs.talk_id != pack.talk_id:
        raise ValueError("пакет фрагментов относится к другому докладу")
    return pack
