import codecs

import pytest

from rapporteur.contracts import NotesArtifact
from rapporteur.contracts.enums import FragmentKind
from rapporteur.contracts.fragments import LinesLocator
from rapporteur.errors import FailureCode, StageError
from rapporteur.ingest.limits import NotesConfig
from rapporteur.ingest.notes import decode_notes, parse_notes, split_blocks
from rapporteur.storage import sha256_hex

NOTES = (
    "Докладчик сравнил\tдва подхода.\nВторой быстрее.\n\n\nИз зала спросили про холодный старт.\n"
)


def _notes(data: bytes, config: NotesConfig | None = None) -> NotesArtifact:
    return parse_notes(data, sha256_hex(data), config)


def test_blocks_keep_line_numbers_and_normalize_text() -> None:
    artifact = _notes(NOTES.encode())

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


def test_cp1251_is_detected_with_confidence() -> None:
    artifact = _notes(NOTES.encode("cp1251"))

    assert (artifact.encoding, artifact.encoding_confident) == ("cp1251", True)
    assert artifact.fragments[1].normalized_text == "Из зала спросили про холодный старт."


def test_doubtful_encoding_is_left_to_the_user() -> None:
    artifact = _notes(NOTES.encode("koi8-r"))

    assert (artifact.encoding, artifact.encoding_confident) == ("cp1251", False)
    assert any("неуверенно" in warning for warning in artifact.warnings)


def test_user_choice_overrides_detection() -> None:
    artifact = _notes(NOTES.encode("cp1251"), NotesConfig(encoding="cp1251"))

    assert (artifact.encoding, artifact.encoding_confident) == ("cp1251", True)


def test_bom_and_crlf() -> None:
    decoded = decode_notes(codecs.BOM_UTF8 + "Первая\r\nвторая\r\n\r\nтретья".encode())

    assert decoded.encoding == "utf-8"
    assert [(b.line_start, b.line_end) for b in split_blocks(decoded.text)] == [(1, 2), (4, 4)]


def test_markdown_headings_are_blocks() -> None:
    artifact = _notes("# Секция 2\n\n- вопрос про данные\n- ответ: открытые\n".encode())

    assert [f.normalized_text for f in artifact.fragments] == [
        "# Секция 2",
        "- вопрос про данные\n- ответ: открытые",
    ]


def test_empty_file_gives_warning() -> None:
    artifact = _notes(b"  \n\n")

    assert artifact.fragments == []
    assert artifact.warnings == ["В файле заметок нет текста"]


@pytest.mark.parametrize(
    ("data", "config", "code"),
    [
        (b"\x89PNG\r\n\x1a\n\x00\x00\x00", None, FailureCode.UNSUPPORTED_FORMAT),
        (b"too long", NotesConfig(max_file_bytes=4), FailureCode.FILE_TOO_LARGE),
        ("текст".encode("cp1251"), NotesConfig(encoding="utf-8"), FailureCode.BROKEN_FILE),
    ],
    ids=["binary", "too-large", "wrong-user-choice"],
)
def test_bad_notes(data: bytes, config: NotesConfig | None, code: FailureCode) -> None:
    with pytest.raises(StageError) as error:
        _notes(data, config)
    assert error.value.code is code
