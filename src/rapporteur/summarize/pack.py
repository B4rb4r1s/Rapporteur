"""Пакет фрагментов одного доклада с метками (spec §6.7, ADR-003, ADR-013).

Пакет строится из разбора презентаций и заметок доклада. Идентификаторы выводятся из данных:
файл — из конференции и SHA-256 (source_file_id), фрагмент — из файла и локатора, ревизия —
из фрагмента и текста. Поэтому повторная сборка даёт тот же пакет и тот же ключ кэша шагов
сводки, а id совпадают с записями БД.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from rapporteur.contracts import FragmentDraft, PackedFragment, TalkFragmentPack
from rapporteur.contracts.enums import FragmentKind, PackBlock
from rapporteur.contracts.fragments import PageLocator

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
