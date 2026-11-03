"""Этап notes: заметки слушателей txt и md (spec §5.2, §5.3).

Блок — подряд идущие непустые строки; локатор — номера строк файла. Если кодировка определена
неуверенно, artifact.encoding_confident = False, а пользователь выбирает кодировку сам, и этап
перезапускается с NotesConfig.encoding.
"""

import codecs
from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel

from rapporteur_contracts import FragmentDraft, NotesArtifact, StageInputs, StageOutput
from rapporteur_contracts.enums import FragmentKind, TextOrigin
from rapporteur_contracts.fragments import LinesLocator
from rapporteur_pipeline.ingest.limits import NotesConfig, check_size
from rapporteur_pipeline.stage import (
    FailureCode,
    StageContext,
    StageError,
    make_meta,
    publish,
    read_source,
)
from rapporteur_pipeline.text import normalize_text

# Доля кириллицы среди букв, ниже которой cp1251 вызывает сомнение.
_MIN_CYRILLIC_SHARE = 0.5
# Текст в KOI8-R, прочитанный как cp1251, состоит в основном из заглавных букв.
_MAX_UPPER_SHARE = 0.5


@dataclass(frozen=True)
class Decoded:
    text: str
    encoding: str
    confident: bool


@dataclass(frozen=True)
class Block:
    line_start: int
    line_end: int
    text: str


class NotesStage:
    name: ClassVar[str] = "notes"
    version: ClassVar[str] = "1.0.0"
    config_model: ClassVar[type[BaseModel]] = NotesConfig

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageOutput:
        config = ctx.config
        assert isinstance(config, NotesConfig)
        source, data = read_source(ctx, inputs)
        check_size(len(data), config.max_file_bytes)
        meta = make_meta(self, inputs, stub=False)

        decoded = decode_notes(data, config.encoding)
        fragments = [
            FragmentDraft(
                kind=FragmentKind.NOTE,
                source_sha256=source.sha256,
                locator=LinesLocator(
                    kind="lines", line_start=block.line_start, line_end=block.line_end
                ),
                raw_text=block.text,
                normalized_text=normalize_text(block.text),
                origin=TextOrigin.EXTRACTED,
                engine_confidence=None,
            )
            for block in split_blocks(decoded.text)
        ]

        warnings: list[str] = []
        if not decoded.confident:
            warnings.append(
                f"Кодировка определена неуверенно ({decoded.encoding}): проверьте текст "
                "и при необходимости выберите кодировку вручную"
            )
        if not fragments:
            warnings.append("В файле заметок нет текста")

        artifact = NotesArtifact(
            meta=meta,
            source_sha256=source.sha256,
            encoding=decoded.encoding,
            encoding_confident=decoded.confident,
            fragments=fragments,
        )
        return publish(ctx, meta, artifact, warnings)


def decode_notes(data: bytes, chosen: str | None = None) -> Decoded:
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return Decoded(_decode(data, "utf-16"), "utf-16", True)
    if b"\x00" in data:
        raise StageError(FailureCode.UNSUPPORTED_FORMAT, "Файл заметок не похож на текст")
    if chosen is not None:
        return Decoded(_decode(data, "utf-8-sig" if chosen == "utf-8" else chosen), chosen, True)
    try:
        return Decoded(data.decode("utf-8-sig"), "utf-8", True)
    except UnicodeDecodeError:
        pass
    try:
        text = data.decode("cp1251")
    except UnicodeDecodeError:
        return Decoded(data.decode("cp1251", errors="replace"), "cp1251", False)
    return Decoded(text, "cp1251", _looks_russian(text))


def _decode(data: bytes, encoding: str) -> str:
    try:
        return data.decode(encoding)
    except UnicodeDecodeError as error:
        raise StageError(
            FailureCode.BROKEN_FILE, f"Файл заметок не читается в кодировке {encoding}"
        ) from error


def _looks_russian(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    cyrillic = [char for char in letters if "а" <= char.lower() <= "я" or char in "ёЁ"]
    if not letters:
        return True
    if len(cyrillic) / len(letters) < _MIN_CYRILLIC_SHARE:
        return False
    upper = sum(char.isupper() for char in cyrillic)
    return upper / len(cyrillic) <= _MAX_UPPER_SHARE


def split_blocks(text: str) -> list[Block]:
    """Номера строк считаются по \\n, \\r\\n и \\r, как у текстового редактора."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[Block] = []
    start: int | None = None
    for number, line in enumerate([*lines, ""], 1):
        if line.strip():
            if start is None:
                start = number
        elif start is not None:
            blocks.append(Block(start, number - 1, "\n".join(lines[start - 1 : number - 1])))
            start = None
    return blocks
