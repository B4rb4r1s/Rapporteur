"""Рендер слайдов в PNG: pptx через LibreOffice в PDF, PDF через PDFium (spec §6.2)."""

import io
import json
from pathlib import Path

import pypdfium2 as pdfium

from rapporteur.errors import FailureCode, StageError
from rapporteur.ingest.limits import PresentationConfig
from rapporteur.office import ConversionError, convert_to_pdf

# Скрытые слайды тоже выгружаются, чтобы страница PDF совпадала с номером слайда.
_PDF_FILTER = "pdf:impress_pdf_Export:" + json.dumps(
    {"ExportHiddenSlides": {"type": "boolean", "value": "true"}}
)


def pptx_to_pdf(pptx: Path, work_dir: Path, limits: PresentationConfig) -> Path:
    try:
        return convert_to_pdf(pptx, work_dir, _PDF_FILTER, limits.render_timeout_s)
    except ConversionError as error:
        raise StageError(FailureCode.RENDER_FAILED, f"Слайды не отрисованы: {error}") from error


def render_page(pdf: pdfium.PdfDocument, page_no: int, limits: PresentationConfig) -> bytes:
    page = pdf[page_no - 1]
    width, height = page.get_size()
    scale = limits.render_long_side_px / max(width, height, 1.0)
    if width * scale * height * scale > limits.max_render_pixels:
        scale = (limits.max_render_pixels / (width * height)) ** 0.5
    image = page.render(scale=scale).to_pil()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
