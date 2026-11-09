"""Пакет фрагментов доклада из разбора презентаций и заметок (ADR-013)."""

import uuid

from rapporteur.contracts.enums import FragmentKind
from rapporteur.summarize.pack import PackSource, build_pack

from .fictional_talk import CONFERENCE, DECK, NOTES, TALK, note_draft, slide_draft

OTHER_TALK = uuid.UUID("00000000-0000-4000-8000-000000000011")
OTHER_DECK = uuid.UUID("00000000-0000-4000-8000-000000000102")


def _deck(file_id: uuid.UUID = DECK) -> PackSource:
    return PackSource(file_id, [slide_draft(1, "Задача"), slide_draft(3, "Выводы")])


def test_fragments_point_to_the_file_and_get_labels() -> None:
    pack = build_pack(CONFERENCE, TALK, [_deck()], [PackSource(NOTES, [note_draft(2, "Вопрос")])])

    assert (pack.conference_id, pack.talk_id) == (CONFERENCE, TALK)
    files = {fragment.kind: fragment.source_file_id for fragment in pack.fragments}
    assert files == {FragmentKind.SLIDE: DECK, FragmentKind.NOTE: NOTES}
    assert [fragment.label for fragment in pack.fragments] == ["S1", "S3", "N1"]


def test_rebinding_a_file_keeps_fragment_ids() -> None:
    first = build_pack(CONFERENCE, TALK, [_deck()])
    second = build_pack(CONFERENCE, OTHER_TALK, [_deck()])

    assert second.talk_id == OTHER_TALK
    assert [f.fragment_id for f in first.fragments] == [f.fragment_id for f in second.fragments]
    assert [f.revision_id for f in first.fragments] == [f.revision_id for f in second.fragments]


def test_two_decks_are_numbered_in_input_order() -> None:
    pack = build_pack(CONFERENCE, TALK, [_deck(), _deck(OTHER_DECK)])

    assert [f.label for f in pack.fragments] == ["S1.1", "S1.3", "S2.1", "S2.3"]
    assert pack.fragments[0].source_file_id == DECK
    assert pack.fragments[-1].source_file_id == OTHER_DECK


def test_empty_fragments_are_skipped() -> None:
    pack = build_pack(CONFERENCE, TALK, [PackSource(DECK, [slide_draft(1, "  ")])])

    assert pack.fragments == []
