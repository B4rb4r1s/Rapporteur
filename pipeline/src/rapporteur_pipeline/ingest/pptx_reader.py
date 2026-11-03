"""Текст pptx по слайдам: заголовки, текст в порядке чтения, таблицы, заметки докладчика."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.exc import PackageNotFoundError

from rapporteur_pipeline.stage import FailureCode, StageError

Table = list[list[str]]


@dataclass(frozen=True)
class ExtractedSlide:
    slide_no: int
    title: str | None
    # Текстовые блоки в порядке чтения: заголовок, фигуры с текстом, строки таблиц через « | ».
    blocks: list[str]
    tables: list[Table]
    notes: str | None
    hidden: bool

    @property
    def text(self) -> str:
        return "\n".join(self.blocks)


def read_pptx(path: Path) -> list[ExtractedSlide]:
    try:
        presentation = Presentation(str(path))
    except (PackageNotFoundError, KeyError, ValueError) as error:
        raise StageError(FailureCode.BROKEN_FILE, "Файл повреждён: pptx не читается") from error
    return [_read_slide(number, slide) for number, slide in enumerate(presentation.slides, 1)]


def _read_slide(slide_no: int, slide: Any) -> ExtractedSlide:
    blocks: list[str] = []
    tables: list[Table] = []
    for shape in _in_reading_order(slide.shapes):
        if shape.has_text_frame and shape.text_frame.text.strip():
            blocks.append(shape.text_frame.text)
        elif getattr(shape, "has_table", False) and shape.has_table:
            table = _read_table(shape.table)
            tables.append(table)
            blocks.extend(" | ".join(row) for row in table)

    title_shape = slide.shapes.title
    title = title_shape.text_frame.text.strip() if title_shape is not None else ""
    return ExtractedSlide(
        slide_no=slide_no,
        title=title or None,
        blocks=blocks,
        tables=tables,
        notes=_read_notes(slide),
        hidden=slide._element.get("show") == "0",
    )


def _in_reading_order(shapes: Iterable[Any]) -> Iterator[Any]:
    """Сверху вниз, слева направо; группы раскрываются на месте. Фигуры без позиции — в конце."""

    def position(shape: Any) -> tuple[bool, int, int]:
        top, left = shape.top, shape.left
        return (top is None, top or 0, left or 0)

    for shape in sorted(shapes, key=position):
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _in_reading_order(shape.shapes)
        else:
            yield shape


def _read_table(table: Any) -> Table:
    rows: Table = []
    for row in table.rows:
        rows.append(["" if cell.is_spanned else cell.text_frame.text.strip() for cell in row.cells])
    return rows


def _read_notes(slide: Any) -> str | None:
    if not slide.has_notes_slide:
        return None
    frame = slide.notes_slide.notes_text_frame
    if frame is None:
        return None
    return frame.text.strip() or None
