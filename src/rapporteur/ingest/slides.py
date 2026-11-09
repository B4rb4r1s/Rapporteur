"""Разбор одной презентации pptx или pdf (spec §6.2, ADR-008)."""

from dataclasses import dataclass, replace
from pathlib import Path

import pypdfium2 as pdfium

from rapporteur.contracts import FragmentDraft, SlidesArtifact
from rapporteur.contracts.artifacts import SlideRecord, TitleCandidates
from rapporteur.contracts.enums import FragmentKind, TextOrigin
from rapporteur.contracts.fragments import PageLocator
from rapporteur.errors import FailureCode, StageError
from rapporteur.ingest.detect import PresentationFormat, detect_presentation
from rapporteur.ingest.dictionary import build_dictionary
from rapporteur.ingest.limits import PresentationConfig, check_size, check_zip
from rapporteur.ingest.pdf_reader import open_pdf, read_page
from rapporteur.ingest.pptx_reader import ExtractedSlide, read_pptx
from rapporteur.ingest.render import pptx_to_pdf, render_page
from rapporteur.ingest.titles import (
    MAX_PLACEHOLDER_TITLE,
    is_conclusion,
    pdf_heading,
    pdf_title,
    pptx_title,
)
from rapporteur.storage import Storage
from rapporteur.text import normalize_text

# Версия разбора входит в ключ кэша: после правки разбора презентации разбираются заново.
VERSION = "1.0.0"


@dataclass(frozen=True)
class _Parsed:
    slides: list[ExtractedSlide]
    title: TitleCandidates
    pdf: pdfium.PdfDocument


def parse_slides(
    data: bytes,
    source_sha256: str,
    storage: Storage,
    images_key: str,
    work: Path,
    config: PresentationConfig | None = None,
) -> SlidesArtifact:
    """Текст, таблицы и заметки по слайдам; PNG каждого слайда кладётся в images_key/."""
    config = config or PresentationConfig()
    check_size(len(data), config.max_file_bytes)
    if detect_presentation(data) is PresentationFormat.PPTX:
        parsed = _parse_pptx(data, work, config)
    else:
        parsed = _parse_pdf(data, work, config)

    records: list[SlideRecord] = []
    fragments: list[FragmentDraft] = []
    hidden: list[int] = []
    try:
        for slide in parsed.slides:
            if slide.hidden:
                hidden.append(slide.slide_no)
                continue
            image_key = f"{images_key}/slide-{slide.slide_no:03}.png"
            storage.put(image_key, render_page(parsed.pdf, slide.slide_no, config))
            records.append(_record(slide, image_key))
            fragment = _fragment(slide, source_sha256)
            if fragment is not None:
                fragments.append(fragment)
    finally:
        parsed.pdf.close()

    warnings: list[str] = []
    if hidden:
        warnings.append(f"Скрытые слайды пропущены: {_ranges(hidden)}")
    without_text = [record.slide_no for record in records if not record.has_text]
    if without_text:
        warnings.append(f"Слайды без текста: {_ranges(without_text)}")

    texts = [record.text for record in records] + [r.notes for r in records if r.notes]
    return SlidesArtifact(
        source_sha256=source_sha256,
        slides=records,
        title_candidates=parsed.title,
        dictionary=build_dictionary(texts, parsed.title.speakers),
        fragments=fragments,
        warnings=warnings,
    )


def _parse_pptx(data: bytes, work: Path, config: PresentationConfig) -> _Parsed:
    path = work / "source.pptx"
    path.write_bytes(data)
    check_zip(path, config)
    slides = read_pptx(path, max_pages=config.max_pages)
    if not slides:
        raise StageError(FailureCode.BROKEN_FILE, "В презентации нет слайдов")

    pdf = open_pdf(pptx_to_pdf(path, work, config))
    try:
        if len(pdf) != len(slides):
            raise StageError(
                FailureCode.RENDER_FAILED,
                f"LibreOffice выдал {len(pdf)} страниц вместо {len(slides)} слайдов",
            )
        headings = [pdf_heading(read_page(pdf, slide.slide_no).lines)[0] for slide in slides]
    except BaseException:
        pdf.close()
        raise
    title = pptx_title(slides[0], headings[0])
    slides = [
        _with_heading(slide, heading) for slide, heading in zip(slides, headings, strict=True)
    ]
    return _Parsed(slides=slides, title=title, pdf=pdf)


def _with_heading(slide: ExtractedSlide, heading: str | None) -> ExtractedSlide:
    """Плейсхолдер заголовка бывает пуст или занят обычным текстом. Тогда заголовком считается
    самый крупный текст на странице, которую нарисовал LibreOffice: шрифты там уже вычислены."""
    if slide.title is not None and len(slide.title) <= MAX_PLACEHOLDER_TITLE:
        return slide
    return replace(slide, title=heading)


def _parse_pdf(data: bytes, work: Path, config: PresentationConfig) -> _Parsed:
    path = work / "source.pdf"
    path.write_bytes(data)
    pdf = open_pdf(path)
    try:
        _check_pages(len(pdf), config)
        if len(pdf) == 0:
            raise StageError(FailureCode.BROKEN_FILE, "В PDF нет страниц")
        pages = [read_page(pdf, page_no) for page_no in range(1, len(pdf) + 1)]
    except BaseException:
        pdf.close()
        raise

    slides: list[ExtractedSlide] = []
    for page in pages:
        heading, _ = pdf_heading(page.lines)
        slides.append(
            ExtractedSlide(
                slide_no=page.page_no,
                title=heading,
                blocks=[page.text] if page.text.strip() else [],
                tables=[],
                notes=None,
                hidden=False,
            )
        )

    return _Parsed(slides=slides, title=pdf_title(pages[0]), pdf=pdf)


def _check_pages(count: int, config: PresentationConfig) -> None:
    if count > config.max_pages:
        raise StageError(
            FailureCode.TOO_MANY_PAGES,
            f"В презентации {count} слайдов при лимите {config.max_pages}",
        )


def _record(slide: ExtractedSlide, image_key: str) -> SlideRecord:
    text = normalize_text(slide.text)
    title = " ".join(normalize_text(slide.title).split()) if slide.title else None
    notes = normalize_text(slide.notes) if slide.notes else ""
    return SlideRecord(
        slide_no=slide.slide_no,
        title=title or None,
        text=text,
        tables=[
            [[normalize_text(cell) for cell in row] for row in table] for table in slide.tables
        ],
        notes=notes or None,
        image_path=image_key,
        vlm_description=None,
        is_conclusion=is_conclusion(title),
        has_text=bool(text),
    )


def _fragment(slide: ExtractedSlide, source_sha256: str) -> FragmentDraft | None:
    """Текст слайда вместе с заметками докладчика (FragmentDraft). Пустой слайд фрагмента
    не даёт: его содержимое опишет VLM в Ф4."""
    raw = "\n\n".join(part for part in (slide.text, slide.notes) if part and part.strip())
    normalized = normalize_text(raw)
    if not normalized:
        return None
    return FragmentDraft(
        kind=FragmentKind.SLIDE,
        source_sha256=source_sha256,
        locator=PageLocator(kind="page", page_no=slide.slide_no),
        raw_text=raw,
        normalized_text=normalized,
        origin=TextOrigin.EXTRACTED,
        engine_confidence=None,
    )


def _ranges(numbers: list[int]) -> str:
    """[1, 2, 3, 5] → «1–3, 5»."""
    parts: list[str] = []
    start = previous = numbers[0]
    for number in [*numbers[1:], None]:
        if number is not None and number == previous + 1:
            previous = number
            continue
        parts.append(str(start) if start == previous else f"{start}–{previous}")
        if number is not None:
            start = previous = number
    return ", ".join(parts)
