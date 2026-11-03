import codecs

import pytest

from rapporteur_contracts import NotesArtifact
from rapporteur_contracts.enums import FragmentKind
from rapporteur_contracts.fragments import LinesLocator
from rapporteur_pipeline.ingest.limits import NotesConfig
from rapporteur_pipeline.ingest.notes import NotesStage, decode_notes, split_blocks
from rapporteur_pipeline.stage import FailureCode, StageError

from .conftest import RunStage

NOTES = (
    "Докладчик сравнил\tдва подхода.\nВторой быстрее.\n\n\nИз зала спросили про холодный старт.\n"
)


def _notes(run_stage: RunStage, data: bytes, config: NotesConfig | None = None) -> NotesArtifact:
    return NotesArtifact.model_validate_json(run_stage(NotesStage(), data, config).artifact_json())


def test_blocks_keep_line_numbers_and_normalize_text(run_stage: RunStage) -> None:
    artifact = _notes(run_stage, NOTES.encode())

    assert artifact.encoding == "utf-8"
    assert artifact.encoding_confident
    assert [f.locator for f in artifact.fragments] == [
        LinesLocator(kind="lines", line_start=1, line_end=2),
        LinesLocator(kind="lines", line_start=5, line_end=5),
    ]
    first = artifact.fragments[0]
    assert first.kind is FragmentKind.NOTE
    assert first.raw_text == "Докладчик сравнил\tдва подхода.\nВторой быстрее."
    assert first.normalized_text == "Докладчик сравнил два подхода.\nВторой быстрее."


def test_cp1251_is_detected_with_confidence(run_stage: RunStage) -> None:
    artifact = _notes(run_stage, NOTES.encode("cp1251"))

    assert (artifact.encoding, artifact.encoding_confident) == ("cp1251", True)
    assert artifact.fragments[1].normalized_text == "Из зала спросили про холодный старт."


def test_doubtful_encoding_is_left_to_the_user(run_stage: RunStage) -> None:
    koi8 = NOTES.encode("koi8-r")
    run = run_stage(NotesStage(), koi8)
    artifact = NotesArtifact.model_validate_json(run.artifact_json())

    assert (artifact.encoding, artifact.encoding_confident) == ("cp1251", False)
    assert any("неуверенно" in warning for warning in run.output.warnings)


def test_user_choice_overrides_detection(run_stage: RunStage) -> None:
    artifact = _notes(run_stage, NOTES.encode("cp1251"), NotesConfig(encoding="cp1251"))

    assert (artifact.encoding, artifact.encoding_confident) == ("cp1251", True)


def test_bom_and_crlf() -> None:
    decoded = decode_notes(codecs.BOM_UTF8 + "Первая\r\nвторая\r\n\r\nтретья".encode())

    assert decoded.encoding == "utf-8"
    assert [(b.line_start, b.line_end) for b in split_blocks(decoded.text)] == [(1, 2), (4, 4)]


def test_markdown_headings_are_blocks(run_stage: RunStage) -> None:
    artifact = _notes(run_stage, "# Секция 2\n\n- вопрос про данные\n- ответ: открытые\n".encode())

    assert [f.normalized_text for f in artifact.fragments] == [
        "# Секция 2",
        "- вопрос про данные\n- ответ: открытые",
    ]


def test_empty_file_gives_warning(run_stage: RunStage) -> None:
    run = run_stage(NotesStage(), b"  \n\n")

    assert NotesArtifact.model_validate_json(run.artifact_json()).fragments == []
    assert run.output.warnings == ["В файле заметок нет текста"]


@pytest.mark.parametrize(
    ("data", "config", "code"),
    [
        (b"\x89PNG\r\n\x1a\n\x00\x00\x00", None, FailureCode.UNSUPPORTED_FORMAT),
        (b"too long", NotesConfig(max_file_bytes=4), FailureCode.FILE_TOO_LARGE),
        ("текст".encode("cp1251"), NotesConfig(encoding="utf-8"), FailureCode.BROKEN_FILE),
    ],
    ids=["binary", "too-large", "wrong-user-choice"],
)
def test_bad_notes(
    run_stage: RunStage, data: bytes, config: NotesConfig | None, code: FailureCode
) -> None:
    with pytest.raises(StageError) as error:
        run_stage(NotesStage(), data, config)
    assert error.value.code is code
