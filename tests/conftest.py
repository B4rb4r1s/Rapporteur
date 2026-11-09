"""Фикстуры собираются в тестах из вымышленного текста: материалы конференций в репозиторий
не копируются (spec §16)."""

import io
import json
import subprocess
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.util import Inches, Pt

from rapporteur.contracts import SlidesArtifact
from rapporteur.ingest.limits import PresentationConfig
from rapporteur.ingest.render import pptx_to_pdf
from rapporteur.ingest.slides import parse_slides
from rapporteur.storage import Storage, sha256_hex

_LAYOUT_TITLE_ONLY = 5
_LAYOUT_BLANK = 6


@dataclass
class SlideSpec:
    title: str | None = None
    title_size: int | None = None
    # Заголовок отдельной надписью крупным шрифтом, как в презентациях без плейсхолдера.
    heading: str | None = None
    body: list[str] = field(default_factory=list)
    table: list[list[str]] | None = None
    group: list[str] | None = None
    notes: str | None = None
    picture: bool = False
    hidden: bool = False


def picture_png() -> bytes:
    """Картинка без текста: слайд-схема, которую в Ф1 нечем прочитать."""
    image = Image.new("RGB", (400, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 180, 140), fill="steelblue")
    draw.ellipse((220, 120, 360, 260), fill="orange")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def make_pptx(specs: list[SlideSpec]) -> bytes:
    presentation = Presentation()
    for spec in specs:
        layout = _LAYOUT_TITLE_ONLY if spec.title is not None else _LAYOUT_BLANK
        slide = presentation.slides.add_slide(presentation.slide_layouts[layout])
        if spec.title is not None:
            slide.shapes.title.text = spec.title
            if spec.title_size is not None:
                slide.shapes.title.text_frame.paragraphs[0].runs[0].font.size = Pt(spec.title_size)
        if spec.heading is not None:
            box = slide.shapes.add_textbox(Inches(0.6), Inches(0.3), Inches(8.5), Inches(1))
            box.text_frame.text = spec.heading
            box.text_frame.paragraphs[0].runs[0].font.size = Pt(40)
        top: int = Inches(1.8)
        for text in spec.body:
            box = slide.shapes.add_textbox(Inches(0.6), top, Inches(8.5), Inches(0.6))
            box.text_frame.text = text
            box.text_frame.paragraphs[0].runs[0].font.size = Pt(18)
            top += Inches(0.7)
        if spec.table is not None:
            rows, cols = len(spec.table), len(spec.table[0])
            table = slide.shapes.add_table(
                rows, cols, Inches(0.6), top, Inches(6), Inches(0.4 * rows)
            ).table
            for r, row in enumerate(spec.table):
                for c, value in enumerate(row):
                    table.cell(r, c).text = value
            top += Inches(0.5 * rows)
        if spec.group is not None:
            group = slide.shapes.add_group_shape()
            for index, text in enumerate(spec.group):
                box = group.shapes.add_textbox(
                    Inches(0.6 + 3 * index), top, Inches(2.8), Inches(0.6)
                )
                box.text_frame.text = text
        if spec.picture:
            slide.shapes.add_picture(io.BytesIO(picture_png()), Inches(1), Inches(1), Inches(6))
        if spec.notes is not None:
            slide.notes_slide.notes_text_frame.text = spec.notes
        if spec.hidden:
            slide._element.set("show", "0")
    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def soffice_to_pdf(pptx: bytes, work: Path, export_options: Mapping[str, object]) -> bytes:
    """Прямой вызов LibreOffice для фикстур, которых не делает этап: например, PDF с паролем."""
    source = work / "fixture.pptx"
    source.write_bytes(pptx)
    target = "pdf:impress_pdf_Export:" + json.dumps(export_options)
    subprocess.run(
        [
            "soffice",
            f"-env:UserInstallation={(work / 'profile').as_uri()}",
            "--headless",
            "--convert-to",
            target,
            "--outdir",
            str(work),
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )
    return (work / "fixture.pdf").read_bytes()


TITLE_SLIDE = SlideSpec(
    title="Кэширование запросов в распределённых базах данных",
    body=["Подготовил: Иванов И. И.", "Группа: ИУ6-31М"],
)
CONTENT_SLIDE = SlideSpec(
    title="Методы",
    body=["Используется LSTM-автоэнкодер на ИИ-модулях", "Точность 94,5 %"],
)


ParseSlides = Callable[..., SlidesArtifact]


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "data")


@pytest.fixture
def slides(storage: Storage, tmp_path: Path) -> ParseSlides:
    """Разбор презентации; картинки слайдов — в хранилище фикстуры storage."""

    def run(data: bytes, config: PresentationConfig | None = None) -> SlidesArtifact:
        sha = sha256_hex(data)
        work = tmp_path / "work" / sha[:12]
        work.mkdir(parents=True, exist_ok=True)
        return parse_slides(data, sha, storage, f"slides/{sha[:12]}", work, config)

    return run


@pytest.fixture(scope="session")
def lo_work(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    yield tmp_path_factory.mktemp("libreoffice")


@pytest.fixture(scope="session")
def text_pdf(lo_work: Path) -> bytes:
    """PDF, каким его выгружает LibreOffice из pptx: с текстовым слоем."""
    pptx = lo_work / "text.pptx"
    pptx.write_bytes(make_pptx([TITLE_SLIDE, CONTENT_SLIDE]))
    work = lo_work / "text"
    work.mkdir()
    return pptx_to_pdf(pptx, work, PresentationConfig()).read_bytes()


@pytest.fixture(scope="session")
def picture_pdf(lo_work: Path) -> bytes:
    """PDF без текстового слоя: только картинки."""
    pptx = lo_work / "pictures.pptx"
    pptx.write_bytes(make_pptx([SlideSpec(picture=True), SlideSpec(picture=True)]))
    work = lo_work / "pictures"
    work.mkdir()
    return pptx_to_pdf(pptx, work, PresentationConfig()).read_bytes()


@pytest.fixture(scope="session")
def encrypted_pdf(lo_work: Path) -> bytes:
    work = lo_work / "encrypted"
    work.mkdir()
    options = {
        "EncryptFile": {"type": "boolean", "value": "true"},
        "DocumentOpenPassword": {"type": "string", "value": "секрет"},
    }
    return soffice_to_pdf(make_pptx([TITLE_SLIDE]), work, options)
