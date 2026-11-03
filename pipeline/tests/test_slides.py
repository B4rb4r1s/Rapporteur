import io
import zipfile

import pytest
from PIL import Image

from rapporteur_contracts import SlidesArtifact
from rapporteur_contracts.enums import FragmentKind, TextOrigin
from rapporteur_contracts.fragments import PageLocator
from rapporteur_pipeline.ingest.limits import MIB, PresentationConfig
from rapporteur_pipeline.ingest.slides import SlidesStage
from rapporteur_pipeline.stage import FailureCode, StageError, cache_key
from rapporteur_pipeline.storage import artifact_key, sha256_hex

from .conftest import CONTENT_SLIDE, TITLE_SLIDE, RunStage, SlideSpec, make_pptx

DECK = [
    TITLE_SLIDE,
    SlideSpec(
        title="Методы",
        body=["Используется LSTM-автоэнкодер на ИИ-модулях", "Точность 94,5 %"],
        table=[["Метод", "Точность"], ["LSTM", "94,5 %"]],
        group=["Блок А", "Блок Б"],
        notes="Сказать про размер выборки",
    ),
    SlideSpec(picture=True),
    SlideSpec(title="Запасной слайд", body=["Не показывался"], hidden=True),
    SlideSpec(title="Выводы", body=["Подход применим к телеметрии"]),
]


@pytest.fixture
def deck(run_stage: RunStage) -> tuple[SlidesArtifact, list[str], RunStage]:
    run = run_stage(SlidesStage(), make_pptx(DECK))
    artifact = SlidesArtifact.model_validate_json(run.artifact_json())
    return artifact, run.output.warnings, run_stage


def test_pptx_slides_keep_deck_numbers_and_skip_hidden(
    deck: tuple[SlidesArtifact, list[str], RunStage],
) -> None:
    artifact, warnings, _ = deck

    assert [slide.slide_no for slide in artifact.slides] == [1, 2, 3, 5]
    assert "Скрытые слайды пропущены: 4" in warnings
    assert "Слайды без текста: 3" in warnings
    assert not artifact.meta.stub


def test_pptx_text_in_reading_order_with_table_group_and_notes(
    deck: tuple[SlidesArtifact, list[str], RunStage],
) -> None:
    artifact, _, _ = deck
    methods = artifact.slides[1]

    assert methods.title == "Методы"
    assert methods.text.split("\n") == [
        "Методы",
        "Используется LSTM-автоэнкодер на ИИ-модулях",
        "Точность 94,5 %",
        "Метод | Точность",
        "LSTM | 94,5 %",
        "Блок А",
        "Блок Б",
    ]
    assert methods.tables == [[["Метод", "Точность"], ["LSTM", "94,5 %"]]]
    assert methods.notes == "Сказать про размер выборки"
    assert methods.has_text


def test_fragments_include_notes_and_skip_empty_slides(
    deck: tuple[SlidesArtifact, list[str], RunStage],
) -> None:
    artifact, _, _ = deck

    pages = [fragment.locator for fragment in artifact.fragments]
    assert pages == [PageLocator(kind="page", page_no=n) for n in (1, 2, 5)]
    methods = artifact.fragments[1]
    assert methods.kind is FragmentKind.SLIDE
    assert methods.origin is TextOrigin.EXTRACTED
    assert methods.normalized_text.endswith("Блок Б\n\nСказать про размер выборки")
    assert all(f.source_sha256 == artifact.source_sha256 for f in artifact.fragments)


def test_every_slide_is_rendered_to_png(deck: tuple[SlidesArtifact, list[str], RunStage]) -> None:
    artifact, _, run_stage = deck
    run = run_stage(SlidesStage(), make_pptx(DECK))  # из того же хранилища

    for slide in artifact.slides:
        image = Image.open(io.BytesIO(run.storage.get(slide.image_path)))
        assert image.format == "PNG"
        assert max(image.size) == 1600


def test_conclusions_title_and_dictionary(deck: tuple[SlidesArtifact, list[str], RunStage]) -> None:
    artifact, _, _ = deck

    assert [slide.is_conclusion for slide in artifact.slides] == [False, False, False, True]
    assert artifact.title_candidates.title == "Кэширование запросов в распределённых базах данных"
    assert artifact.title_candidates.speakers == ["Иванов И. И."]
    for term in ("LSTM", "ИИ", "ИУ6-31М", "Иванов И. И."):
        assert term in artifact.dictionary


def test_heading_falls_back_to_largest_text_on_rendered_slide(run_stage: RunStage) -> None:
    """Без плейсхолдера или с обычным текстом в нём заголовок берётся по размеру шрифта."""
    misused = "Подход работает при разных темах оформления и не требует ручных масок. " * 2
    deck = [
        SlideSpec(heading="Заключение", body=["Метод проверен на трёх наборах данных"]),
        SlideSpec(title=misused, title_size=14, heading="Выводы"),
    ]
    artifact = SlidesArtifact.model_validate_json(
        run_stage(SlidesStage(), make_pptx(deck)).artifact_json()
    )

    assert [slide.title for slide in artifact.slides] == ["Заключение", "Выводы"]
    assert [slide.is_conclusion for slide in artifact.slides] == [True, True]


def test_artifact_is_stored_under_cache_key(run_stage: RunStage) -> None:
    data = make_pptx([CONTENT_SLIDE])
    run = run_stage(SlidesStage(), data)
    meta = run.output.meta

    assert meta.input_hashes == [sha256_hex(data)]
    assert meta.cache_key == cache_key("slides", "1.0.0", [sha256_hex(data)], meta.config_hash)
    assert run.output.artifact.storage_key == artifact_key("slides", meta.cache_key)
    assert run.output.artifact.sha256 == sha256_hex(run.artifact_json())


def test_pdf_text_layer_and_title_by_font_size(run_stage: RunStage, text_pdf: bytes) -> None:
    artifact = SlidesArtifact.model_validate_json(
        run_stage(SlidesStage(), text_pdf).artifact_json()
    )

    assert [slide.slide_no for slide in artifact.slides] == [1, 2]
    assert artifact.slides[1].title == "Методы"
    assert "Точность 94,5 %" in artifact.slides[1].text
    assert artifact.title_candidates.title == "Кэширование запросов в распределённых базах данных"
    assert artifact.title_candidates.speakers == ["Иванов И. И."]


def test_pdf_without_text_layer_is_marked(run_stage: RunStage, picture_pdf: bytes) -> None:
    run = run_stage(SlidesStage(), picture_pdf)
    artifact = SlidesArtifact.model_validate_json(run.artifact_json())

    assert [slide.has_text for slide in artifact.slides] == [False, False]
    assert artifact.fragments == []
    assert run.output.warnings == ["Слайды без текста: 1–2"]


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"not a presentation at all", FailureCode.UNSUPPORTED_FORMAT),
        (make_pptx([CONTENT_SLIDE])[:3000], FailureCode.BROKEN_FILE),
        (_zip({"word/document.xml": b"<w/>"}), FailureCode.UNSUPPORTED_FORMAT),
        (
            _zip({"ppt/presentation.xml": b"<p/>", "ppt/media/bomb.bin": bytes(64 * MIB)}),
            FailureCode.ARCHIVE_LIMITS,
        ),
        (OLE + b"\x00" * 64 + "EncryptionInfo".encode("utf-16-le"), FailureCode.ENCRYPTED_FILE),
        (OLE + b"\x00" * 64, FailureCode.UNSUPPORTED_FORMAT),
        (b"%PDF-1.7\n" + "обрыв".encode(), FailureCode.BROKEN_FILE),
    ],
    ids=["garbage", "truncated-pptx", "not-pptx-zip", "zip-bomb", "encrypted-pptx", "ppt", "pdf"],
)
def test_bad_files_fail_with_stable_codes(
    run_stage: RunStage, data: bytes, code: FailureCode
) -> None:
    with pytest.raises(StageError) as error:
        run_stage(SlidesStage(), data)
    assert error.value.code is code


def test_encrypted_pdf(run_stage: RunStage, encrypted_pdf: bytes) -> None:
    with pytest.raises(StageError) as error:
        run_stage(SlidesStage(), encrypted_pdf)
    assert error.value.code is FailureCode.ENCRYPTED_FILE


@pytest.mark.parametrize(
    ("config", "code"),
    [
        (PresentationConfig(max_pages=1), FailureCode.TOO_MANY_PAGES),
        (PresentationConfig(max_file_bytes=10), FailureCode.FILE_TOO_LARGE),
    ],
)
def test_limits_from_config(
    run_stage: RunStage, config: PresentationConfig, code: FailureCode
) -> None:
    with pytest.raises(StageError) as error:
        run_stage(SlidesStage(), make_pptx([TITLE_SLIDE, CONTENT_SLIDE]), config)
    assert error.value.code is code
