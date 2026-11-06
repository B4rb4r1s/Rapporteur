"""Этап pack: пакет доклада из выходов slides и notes (ADR-013)."""

import uuid
from pathlib import Path

import pytest
from pydantic import BaseModel

from rapporteur_contracts import ArtifactRef, PackArtifact, StageInputs, source_file_id
from rapporteur_contracts.enums import FragmentKind
from rapporteur_pipeline.chain import put_blob, run_cached
from rapporteur_pipeline.ingest.limits import NotesConfig, PresentationConfig
from rapporteur_pipeline.stage import FailureCode, Stage, StageContext, StageError, config_hash
from rapporteur_pipeline.storage import DirectoryStorage
from rapporteur_pipeline.stubs import (
    StubNotesStage,
    StubSlidesStage,
    StubTranscriptStage,
    TranscriptStubConfig,
)
from rapporteur_pipeline.summarize.pack import PackConfig, PackStage

from .fictional_talk import CONFERENCE, TALK

OTHER_TALK = uuid.UUID("00000000-0000-4000-8000-000000000011")


@pytest.fixture
def storage(tmp_path: Path) -> DirectoryStorage:
    return DirectoryStorage(tmp_path / "data")


def parsed(
    storage: DirectoryStorage, stage: Stage, config: BaseModel, data: bytes
) -> tuple[ArtifactRef, ArtifactRef]:
    source = put_blob(storage, data)
    return source, run_cached(stage, storage, config, [source]).ref


def run_pack(
    storage: DirectoryStorage, refs: list[ArtifactRef], talk: uuid.UUID = TALK
) -> PackArtifact:
    ran = run_cached(
        PackStage(), storage, PackConfig(), refs, talk_id=talk, conference_id=CONFERENCE
    )
    return PackArtifact.model_validate_json(storage.get(ran.ref.storage_key))


def test_fragments_point_to_the_db_file_id(storage: DirectoryStorage) -> None:
    deck_source, deck = parsed(storage, StubSlidesStage(), PresentationConfig(), b"deck")
    notes_source, notes = parsed(storage, StubNotesStage(), NotesConfig(), b"notes")

    pack = run_pack(storage, [deck, notes]).pack

    assert (pack.conference_id, pack.talk_id) == (CONFERENCE, TALK)
    files = {fragment.kind: fragment.source_file_id for fragment in pack.fragments}
    assert files[FragmentKind.SLIDE] == source_file_id(CONFERENCE, deck_source.sha256)
    assert files[FragmentKind.NOTE] == source_file_id(CONFERENCE, notes_source.sha256)
    labels = [fragment.label for fragment in pack.fragments]
    assert labels[0] == "S1" and labels[-1] == "N1"


def test_rebinding_a_file_keeps_fragment_ids(storage: DirectoryStorage) -> None:
    _, deck = parsed(storage, StubSlidesStage(), PresentationConfig(), b"deck")

    first = run_pack(storage, [deck]).pack
    second = run_pack(storage, [deck], OTHER_TALK).pack

    assert second.talk_id == OTHER_TALK
    assert [f.fragment_id for f in first.fragments] == [f.fragment_id for f in second.fragments]
    assert [f.revision_id for f in first.fragments] == [f.revision_id for f in second.fragments]


def test_two_decks_are_numbered_in_input_order(storage: DirectoryStorage) -> None:
    first_source, first = parsed(storage, StubSlidesStage(), PresentationConfig(), b"deck one")
    _, second = parsed(storage, StubSlidesStage(), PresentationConfig(), b"deck two")

    pack = run_pack(storage, [first, second]).pack

    assert pack.fragments[0].label.startswith("S1.")
    assert pack.fragments[0].source_file_id == source_file_id(CONFERENCE, first_source.sha256)
    assert pack.fragments[-1].label.startswith("S2.")


def test_same_inputs_reuse_the_cached_pack(storage: DirectoryStorage) -> None:
    _, deck = parsed(storage, StubSlidesStage(), PresentationConfig(), b"deck")
    args = (PackStage(), storage, PackConfig(), [deck])

    first = run_cached(*args, talk_id=TALK, conference_id=CONFERENCE)
    second = run_cached(*args, talk_id=TALK, conference_id=CONFERENCE)

    assert second.cached and second.ref == first.ref


def test_talk_without_text_is_no_sources(storage: DirectoryStorage) -> None:
    with pytest.raises(StageError) as error:
        run_pack(storage, [])
    assert error.value.code is FailureCode.NO_SOURCES


def test_transcripts_are_not_packed_yet(storage: DirectoryStorage) -> None:
    _, audio = parsed(storage, StubTranscriptStage(), TranscriptStubConfig(), b"audio")

    with pytest.raises(ValueError, match="asr"):
        run_pack(storage, [audio])


def test_pack_needs_conference_and_talk(storage: DirectoryStorage, tmp_path: Path) -> None:
    _, deck = parsed(storage, StubSlidesStage(), PresentationConfig(), b"deck")
    inputs = StageInputs(
        conference_id=None, talk_id=TALK, artifacts=[deck], config_hash=config_hash(PackConfig())
    )
    ctx = StageContext(storage=storage, config=PackConfig(), tmp_dir=tmp_path)

    with pytest.raises(ValueError, match="конференция"):
        PackStage().run(ctx, inputs)
