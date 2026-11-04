"""Выдача справки: DOCX по бланку, Markdown и PDF из того же DOCX (spec §6.9).

DOCX — единственный источник текста: Markdown строится из его абзацев, PDF — конвертацией
через LibreOffice, поэтому три формата не расходятся по содержанию.
"""

import io
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from docx import Document

from rapporteur_contracts.enums import ReportFormat
from rapporteur_contracts.reports import ReportFile
from rapporteur_pipeline.office import ConversionError, convert_to_pdf
from rapporteur_pipeline.report.template import Template, render_docx
from rapporteur_pipeline.storage import Storage, sha256_hex

PDF_TIMEOUT_S = 120
_FILE_NAMES = {
    ReportFormat.DOCX: "spravka.docx",
    ReportFormat.MARKDOWN: "spravka.md",
    ReportFormat.PDF: "spravka.pdf",
}
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]])")
_LINE_START_SPECIAL = re.compile(r"^(#|>|\+|-(?=\s))")


class ExportError(Exception):
    pass


def render_formats(
    template: Template, context: Mapping[str, Any], formats: Iterable[ReportFormat], work: Path
) -> dict[ReportFormat, bytes]:
    docx = render_docx(template, context)
    rendered: dict[ReportFormat, bytes] = {}
    for report_format in formats:
        match report_format:
            case ReportFormat.DOCX:
                rendered[report_format] = docx
            case ReportFormat.MARKDOWN:
                rendered[report_format] = docx_to_markdown(docx).encode()
            case ReportFormat.PDF:
                rendered[report_format] = docx_to_pdf(docx, work)
    return rendered


def export_files(
    storage: Storage, version_id: str, rendered: Mapping[ReportFormat, bytes]
) -> list[ReportFile]:
    """Файлы версии справки в exports/<версия>/ (ADR-005) для ReportVersion.files."""
    files: list[ReportFile] = []
    for report_format, data in rendered.items():
        key = f"exports/{version_id}/{_FILE_NAMES[report_format]}"
        storage.put(key, data)
        files.append(
            ReportFile(
                format=report_format, storage_key=key, sha256=sha256_hex(data), size_bytes=len(data)
            )
        )
    return files


def docx_to_markdown(docx: bytes) -> str:
    """Абзац DOCX — абзац Markdown; абзац целиком полужирный — **полужирный**."""
    blocks: list[str] = []
    for paragraph in Document(io.BytesIO(docx)).paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        escaped = _LINE_START_SPECIAL.sub(r"\\\1", _MARKDOWN_SPECIAL.sub(r"\\\1", text))
        runs = [run for run in paragraph.runs if run.text.strip()]
        if runs and all(run.bold for run in runs):
            escaped = f"**{escaped}**"
        blocks.append(escaped)
    return "\n\n".join(blocks) + "\n"


def docx_to_pdf(docx: bytes, work: Path) -> bytes:
    source = work / "spravka.docx"
    source.write_bytes(docx)
    try:
        return convert_to_pdf(source, work, "pdf:writer_pdf_Export", PDF_TIMEOUT_S).read_bytes()
    except ConversionError as error:
        raise ExportError(f"PDF не собран: {error}") from error
