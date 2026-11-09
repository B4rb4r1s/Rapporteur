"""Инвентаризация оригинала без рендера и без загрузки записи в память."""

import codecs
import json
import math
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from rapporteur.contracts.artifacts import InventoryItem, TitleCandidates
from rapporteur.contracts.enums import FileKind
from rapporteur.errors import FailureCode, StageError
from rapporteur.ingest.limits import (
    MIB,
    NotesConfig,
    PresentationConfig,
    check_size,
    check_zip,
)
from rapporteur.ingest.notes import decode_notes
from rapporteur.ingest.pdf_reader import open_pdf, read_page
from rapporteur.ingest.pptx_reader import read_pptx
from rapporteur.ingest.titles import pdf_title, pptx_title

MAX_UPLOAD_BYTES = 10 * 1024 * MIB
MAX_DURATION_MS = 10 * 60 * 60 * 1000
HEADER_BYTES = 1024
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_PDF_LOCK = Lock()  # PDFium не допускает параллельных вызовов из разных потоков.
_PROGRAMS = {
    "word/document.xml": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xl/workbook.xml": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
_AUDIO = {
    "aac": "audio/aac",
    "flac": "audio/flac",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "wav": "audio/wav",
}


@dataclass(frozen=True)
class Inspection:
    kind: FileKind
    media_type: str
    title: TitleCandidates | None = None
    duration_ms: int | None = None
    encoding: str | None = None
    warning: str | None = None

    def item(self, path: Path, original_name: str, sha256: str) -> InventoryItem:
        return InventoryItem(
            kind=self.kind,
            media_type=self.media_type,
            size_bytes=path.stat().st_size,
            original_name=original_name,
            sha256=sha256,
            duration_ms=self.duration_ms,
            encoding=self.encoding,
        )


def upload_limit(head: bytes) -> int:
    if b"%PDF-" in head or head.startswith(b"PK\x03\x04"):
        return PresentationConfig().max_file_bytes
    if not _looks_like_recording(head) and _looks_like_text(head):
        return NotesConfig().max_file_bytes
    return MAX_UPLOAD_BYTES


def inspect_file(path: Path, *, program: bool = False) -> Inspection:
    size = path.stat().st_size
    check_size(size, MAX_UPLOAD_BYTES)
    with path.open("rb") as stream:
        head = stream.read(HEADER_BYTES)
    presentation = PresentationConfig()
    if b"%PDF-" in head:
        check_size(size, presentation.max_file_bytes)
        with _PDF_LOCK, open_pdf(path) as pdf:
            count = len(pdf)
            if not count:
                raise StageError(FailureCode.BROKEN_FILE, "В PDF нет страниц")
            if count > presentation.max_pages:
                raise StageError(
                    FailureCode.TOO_MANY_PAGES,
                    f"В PDF {count} страниц при лимите {presentation.max_pages}",
                )
            title = None if program else pdf_title(read_page(pdf, 1))
        kind = FileKind.PROGRAM if program else FileKind.PRESENTATION
        return Inspection(kind, "application/pdf", title=title)
    if head.startswith(b"PK\x03\x04"):
        check_size(size, presentation.max_file_bytes)
        return _office(path, presentation)
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise StageError(
            FailureCode.UNSUPPORTED_FORMAT,
            "Старые и защищённые форматы Office: сохраните файл как pptx или pdf",
        )
    if _looks_like_recording(head):
        return _recording(path)
    if _looks_like_text(head):
        check_size(size, NotesConfig().max_file_bytes)
        decoded = decode_notes(path.read_bytes())
        if _looks_like_text(decoded.text.encode("utf-8")) and decoded.text.strip():
            return Inspection(
                FileKind.PROGRAM if program else FileKind.NOTES,
                "text/plain",
                encoding=decoded.encoding,
                warning=None if decoded.confident else "Кодировка определена неуверенно",
            )
    raise StageError(FailureCode.UNSUPPORTED_FORMAT, "Формат файла не поддерживается")


def _office(path: Path, config: PresentationConfig) -> Inspection:
    check_zip(path, config)
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
    if "ppt/presentation.xml" in names:
        slides = read_pptx(path, max_pages=config.max_pages, first_only=True)
        if not slides:
            raise StageError(FailureCode.BROKEN_FILE, "В презентации нет слайдов")
        return Inspection(FileKind.PRESENTATION, PPTX, title=pptx_title(slides[0]))
    for entry, media_type in _PROGRAMS.items():
        if entry in names:
            return Inspection(FileKind.PROGRAM, media_type)
    raise StageError(FailureCode.UNSUPPORTED_FORMAT, "Формат архива не поддерживается")


def _looks_like_text(data: bytes) -> bool:
    return data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) or all(
        (byte >= 32 and byte != 127) or byte in (9, 10, 13) for byte in data
    )


def _looks_like_recording(head: bytes) -> bool:
    return (
        head.startswith((b"ID3", b"OggS", b"fLaC"))
        or (head.startswith((b"RIFF", b"RF64")) and head[8:12] == b"WAVE")
        or head[4:8] in {b"ftyp", b"moov", b"mdat", b"wide", b"free"}
        or (len(head) >= 2 and head[0] == 255 and head[1] & 224 == 224)
    )


def _recording(path: Path) -> Inspection:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file",
                "-format_whitelist",
                "aac,flac,mov,mp3,ogg,wav",
                "-enable_drefs",
                "0",
                "-show_entries",
                "format=format_name,duration:stream=codec_type:stream_disposition=attached_pic",
                "-of",
                "json",
                "-i",
                str(path.resolve()),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        report = json.loads(result.stdout)
        fmt = report["format"]["format_name"].split(",")[0]
        duration = float(report["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError
        kinds = {
            stream["codec_type"]
            for stream in report["streams"]
            if not stream.get("disposition", {}).get("attached_pic")
        }
        if (fmt != "mov" and "audio" not in kinds) or not kinds & {"audio", "video"}:
            raise ValueError
    except (subprocess.SubprocessError, KeyError, ValueError) as error:
        raise StageError(
            FailureCode.BROKEN_FILE, "Не удалось прочитать запись и её длительность"
        ) from error
    if duration * 1000 > MAX_DURATION_MS:
        raise StageError(FailureCode.FILE_TOO_LARGE, "Длительность записи превышает 10 часов")
    if "video" in kinds:
        return Inspection(FileKind.VIDEO, "video/mp4", duration_ms=max(1, round(duration * 1000)))
    return Inspection(
        FileKind.AUDIO, _AUDIO.get(fmt, "audio/mp4"), duration_ms=max(1, round(duration * 1000))
    )
