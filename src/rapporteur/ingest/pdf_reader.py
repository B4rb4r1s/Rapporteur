"""Текст PDF по страницам и строки с размером шрифта для поиска заголовка."""

from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

from rapporteur.errors import FailureCode, StageError
from rapporteur.text import normalize_text


@dataclass(frozen=True)
class PdfLine:
    # Текст строки как его отдаёт PDFium, без нормализации: перенос слова виден только
    # на стыке строк, поэтому строки нормализуются вместе (titles.pdf_heading).
    text: str
    font_size: float


@dataclass(frozen=True)
class PdfPage:
    page_no: int
    text: str
    lines: list[PdfLine]


def open_pdf(path: Path) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as error:
        if getattr(error, "err_code", None) == pdfium_raw.FPDF_ERR_PASSWORD:
            raise StageError(FailureCode.ENCRYPTED_FILE, "PDF защищён паролем") from error
        raise StageError(FailureCode.BROKEN_FILE, "Файл повреждён: PDF не читается") from error


def read_page(pdf: pdfium.PdfDocument, page_no: int) -> PdfPage:
    textpage = pdf[page_no - 1].get_textpage()
    try:
        text = textpage.get_text_range()
        lines = _lines_with_font_size(textpage)
    finally:
        textpage.close()
    return PdfPage(page_no=page_no, text=text, lines=lines)


def _lines_with_font_size(textpage: pdfium.PdfTextPage) -> list[PdfLine]:
    """PDFium сам вставляет \\r\\n между строками; размер строки — наибольший размер её символов."""
    lines: list[PdfLine] = []
    chars: list[str] = []
    size = 0.0
    for index in range(textpage.count_chars()):
        char = chr(pdfium_raw.FPDFText_GetUnicode(textpage.raw, index))
        if char == "\n":
            lines.append(PdfLine("".join(chars), size))
            chars, size = [], 0.0
        elif char != "\r":
            chars.append(char)
            if not char.isspace():
                size = max(size, pdfium_raw.FPDFText_GetFontSize(textpage.raw, index))
    lines.append(PdfLine("".join(chars), size))
    return [line for line in lines if normalize_text(line.text)]
