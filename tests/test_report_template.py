"""Стартовый бланк: сборка на вымышленной конференции и сравнение с образцом (P0-06)."""

import datetime as dt
import io
from pathlib import Path
from typing import Any

import pytest
from docx import Document

from rapporteur.report.grammar import NBSP
from rapporteur.report.template import (
    Period,
    TemplateError,
    TemplateErrorCode,
    load_template,
    render_docx,
)

REPO = Path(__file__).resolve().parents[1]
STARTER = REPO / "templates" / "starter"
EXPECTED = Path(__file__).parent / "fixtures" / "starter_expected.txt"

CONTEXT: dict[str, Any] = {
    "title": 'Данные и знания "Осень-2026"',
    "period": Period(dt.date(2026, 10, 30), dt.date(2026, 11, 1)),
    "place": "г. Казань",
    "organizer": None,
    "talks_count": 3,
    "speakers_count": 4,
    "talks": [
        {
            "title": "Кэширование запросов в распределённых базах данных",
            "speakers": ["Иванов И. И."],
            "summary": (
                "Доклад посвящён кэшированию запросов в распределённых базах данных. "
                "Эксперименты проводились на стенде из 4 узлов с 32 ГБ памяти. "
                "При нагрузке 1000 запросов в секунду политика LFU снизила задержку на 23 %."
            ),
            "conclusions": [
                "LFU эффективнее LRU при неравномерном распределении запросов.",
                "Выигрыш растёт с размером кэша до 8 ГБ.",
            ],
        },
        {
            "title": "Журналирование в колоночных хранилищах",
            "speakers": ["Петрова А. А.", "Сидоров П. К."],
            "summary": (
                "Сравниваются журнал упреждающей записи и снимки состояния. "
                "Запись в журнал занимает <1 мс & не требует блокировок."
            ),
            "conclusions": [],
        },
        {
            "title": "Обзор методов сжатия временных рядов",
            "speakers": ["Орлов М. А."],
            "summary": "Обзор методов сжатия телеметрии без потерь и с потерями.",
            "conclusions": [],
        },
    ],
}


def paragraphs(docx: bytes) -> list[str]:
    """Текст абзацев; неразрывный пробел показан как ~, чтобы образец было видно глазами."""
    return [p.text.replace(NBSP, "~") for p in Document(io.BytesIO(docx)).paragraphs]


def test_starter_renders_like_the_reference() -> None:
    rendered = paragraphs(render_docx(load_template(STARTER), CONTEXT))

    assert rendered == EXPECTED.read_text(encoding="utf-8").splitlines()


def test_rendered_document_has_no_markup_left() -> None:
    text = "\n".join(paragraphs(render_docx(load_template(STARTER), CONTEXT)))

    assert "{{" not in text and "{%" not in text


def test_optional_place_paragraph_disappears() -> None:
    rendered = paragraphs(render_docx(load_template(STARTER), {**CONTEXT, "place": None}))

    assert not any(line.startswith("Место проведения") for line in rendered)


@pytest.mark.parametrize(
    ("change", "code", "name"),
    [
        ({"title": None}, TemplateErrorCode.MISSING_VARIABLE, "title"),
        ({"talks": []}, TemplateErrorCode.MISSING_VARIABLE, "talks"),
        ({"talks_count": "3"}, TemplateErrorCode.INVALID_VALUE, "talks_count"),
        ({"speakers_count": True}, TemplateErrorCode.INVALID_VALUE, "speakers_count"),
        (
            {"period": Period(dt.date(2026, 11, 1), dt.date(2026, 10, 30))},
            TemplateErrorCode.INVALID_VALUE,
            "period",
        ),
    ],
)
def test_context_errors_name_the_variable(
    change: dict[str, Any], code: TemplateErrorCode, name: str
) -> None:
    with pytest.raises(TemplateError) as error:
        render_docx(load_template(STARTER), {**CONTEXT, **change})

    assert error.value.code is code
    assert f"«{name}»" in error.value.message


def _custom_template(directory: Path, lines: list[str], variables: str) -> Path:
    document = Document()
    for line in lines:
        document.add_paragraph(line)
    document.save(str(directory / "report.docx"))
    (directory / "variables.yaml").write_text(
        f"version: test-1\ndocument: report.docx\nvariables:\n{variables}", encoding="utf-8"
    )
    return directory


TITLE_ONLY = "  title: {source: conference.title, type: str, required: true}\n"
TALKS_ONLY = "  talks: {source: report.talks, type: list, required: true}\n"


def test_template_with_undeclared_variable_is_rejected(tmp_path: Path) -> None:
    directory = _custom_template(tmp_path, ["{{ title }} {{ unknown }}"], TITLE_ONLY)

    with pytest.raises(TemplateError) as error:
        load_template(directory)

    assert error.value.code is TemplateErrorCode.UNDECLARED_VARIABLE
    assert "unknown" in error.value.message


def test_typo_in_field_name_fails_instead_of_vanishing(tmp_path: Path) -> None:
    lines = ["{%p for talk in talks %}", "{{ talk.titel }}", "{%p endfor %}"]
    template = load_template(_custom_template(tmp_path, lines, TALKS_ONLY))

    with pytest.raises(TemplateError) as error:
        render_docx(template, {"talks": [{"title": "Доклад"}]})

    assert error.value.code is TemplateErrorCode.RENDER_FAILED
    assert "titel" in error.value.message


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("version: 1\nvariables: [", "не читается"),
        ("version: test-1\ndocument: absent.docx\nvariables: {}\n", "Нет файла бланка"),
    ],
)
def test_broken_spec(tmp_path: Path, spec: str, message: str) -> None:
    (tmp_path / "variables.yaml").write_text(spec, encoding="utf-8")

    with pytest.raises(TemplateError) as error:
        load_template(tmp_path)

    assert error.value.code is TemplateErrorCode.INVALID_SPEC
    assert message in error.value.message
