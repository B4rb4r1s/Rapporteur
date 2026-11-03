"""Этап slides: разбор одной презентации pptx или pdf (spec §6.2, ADR-008)."""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import ClassVar

import pypdfium2 as pdfium
from pydantic import BaseModel

from rapporteur_contracts import FragmentDraft, SlidesArtifact, StageInputs, StageOutput
from rapporteur_contracts.artifacts import SlideRecord, TitleCandidates
from rapporteur_contracts.enums import FragmentKind, TextOrigin
from rapporteur_contracts.fragments import PageLocator
from rapporteur_pipeline.ingest.detect import PresentationFormat, detect_presentation
from rapporteur_pipeline.ingest.dictionary import build_dictionary
from rapporteur_pipeline.ingest.limits import PresentationConfig, check_size, check_zip
from rapporteur_pipeline.ingest.pdf_reader import open_pdf, read_page
from rapporteur_pipeline.ingest.pptx_reader import ExtractedSlide, read_pptx
from rapporteur_pipeline.ingest.render import pptx_to_pdf, render_page
from rapporteur_pipeline.ingest.titles import candidates_from_lines, is_conclusion, pdf_heading
from rapporteur_pipeline.stage import (
    FailureCode,
    StageContext,
    StageError,
    make_meta,
    publish,
    read_source,
)
from rapporteur_pipeline.storage import derived_key
from rapporteur_pipeline.text import normalize_text

# Длинный текст в плейсхолдере заголовка — обычный текст слайда, а не заголовок.
_MAX_PLACEHOLDER_TITLE = 100


@dataclass(frozen=True)
class _Parsed:
    slides: list[ExtractedSlide]
    title: TitleCandidates
    pdf: pdfium.PdfDocument


class SlidesStage:
    name: ClassVar[str] = "slides"
    version: ClassVar[str] = "1.0.0"
    config_model: ClassVar[type[BaseModel]] = PresentationConfig

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageOutput:
        config = ctx.config
        assert isinstance(config, PresentationConfig)
        source, data = read_source(ctx, inputs)
        check_size(len(data), config.max_file_bytes)
        meta = make_meta(self, inputs, stub=False)

        if detect_presentation(data) is PresentationFormat.PPTX:
            parsed = _parse_pptx(data, ctx.tmp_dir, config)
        else:
            parsed = _parse_pdf(data, ctx.tmp_dir, config)

        records: list[SlideRecord] = []
        fragments: list[FragmentDraft] = []
        hidden: list[int] = []
        try:
            for slide in parsed.slides:
                if slide.hidden:
                    hidden.append(slide.slide_no)
                    continue
                image_key = derived_key(meta.cache_key, f"slide-{slide.slide_no:03}.png")
                ctx.storage.put(image_key, render_page(parsed.pdf, slide.slide_no, config))
                records.append(_record(slide, image_key))
                fragment = _fragment(slide, source.sha256)
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
        artifact = SlidesArtifact(
            meta=meta,
            source_sha256=source.sha256,
            slides=records,
            title_candidates=parsed.title,
            dictionary=build_dictionary(texts, parsed.title.speakers),
            fragments=fragments,
        )
        return publish(ctx, meta, artifact, warnings)


def _parse_pptx(data: bytes, work: Path, config: PresentationConfig) -> _Parsed:
    path = work / "source.pptx"
    path.write_bytes(data)
    check_zip(path, config)
    slides = read_pptx(path)
    _check_pages(len(slides), config)
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
    title = _pptx_title(slides[0], headings[0])
    slides = [
        _with_heading(slide, heading) for slide, heading in zip(slides, headings, strict=True)
    ]
    return _Parsed(slides=slides, title=title, pdf=pdf)


def _with_heading(slide: ExtractedSlide, heading: str | None) -> ExtractedSlide:
    """Плейсхолдер заголовка бывает пуст или занят обычным текстом. Тогда заголовком считается
    самый крупный текст на странице, которую нарисовал LibreOffice: шрифты там уже вычислены."""
    if slide.title is not None and len(slide.title) <= _MAX_PLACEHOLDER_TITLE:
        return slide
    return replace(slide, title=heading)


def _pptx_title(first: ExtractedSlide, heading: str | None) -> TitleCandidates:
    """Название с титульного слайда по фигурам: плейсхолдер, иначе верхний блок. Строки PDF
    здесь хуже: соседние надписи PDFium склеивает в одну строку с названием."""
    blocks = [" ".join(block.split()) for block in map(normalize_text, first.blocks) if block]
    if first.title is not None and len(first.title) <= _MAX_PLACEHOLDER_TITLE:
        title: str | None = " ".join(normalize_text(first.title).split())
    elif first.title is None and blocks:
        title = blocks[0]
    else:
        title = heading
    others = [
        line
        for block in map(normalize_text, first.blocks)
        if block and not (title and " ".join(block.split()) in title)
        for line in block.split("\n")
    ]
    return candidates_from_lines(title, others)


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

    first_lines = pages[0].lines
    title, used = pdf_heading(first_lines)
    others = [line.text for index, line in enumerate(first_lines) if index not in used]
    return _Parsed(slides=slides, title=candidates_from_lines(title, others), pdf=pdf)


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
