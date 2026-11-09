import pytest

from rapporteur.ingest.dictionary import build_dictionary
from rapporteur.ingest.pdf_reader import PdfLine
from rapporteur.ingest.titles import (
    candidates_from_lines,
    is_conclusion,
    pdf_heading,
    speakers_in_line,
)
from rapporteur.text import normalize_text

PDF_HYPHEN = chr(0xFFFE)


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("и\N{COMBINING BREVE}од", "йод"),
        ("строка\x0bперенос", "строка\nперенос"),
        ("94,5\N{NO-BREAK SPACE}%\tточность", "94,5 % точность"),
        (f"квантильно{PDF_HYPHEN}кластерного", "квантильно-кластерного"),
        ("квантильно\x02кластерного", "квантильно-кластерного"),
        (f"кван{PDF_HYPHEN}\r\nтильного", "квантильного"),
        ("кван\x02\nтильного", "квантильного"),
        ("мяг\N{SOFT HYPHEN}кий", "мягкий"),
        ("управляющий\x07символ", "управляющийсимвол"),
        ("  первая  \n\n\n\nвторая  \n\n", "первая\n\nвторая"),
        ("Ёлка, ёж: 3,14!", "Ёлка, ёж: 3,14!"),
    ],
)
def test_normalize_text(raw: str, normalized: str) -> None:
    assert normalize_text(raw) == normalized


@pytest.mark.parametrize(
    ("line", "speakers"),
    [
        ("Подготовил: Сидоров П.К", ["Сидоров П.К"]),
        ("Подготовила: Белова О. Н.", ["Белова О. Н."]),
        ("Студент: Орлов Максим ИУ6-31М", ["Орлов Максим"]),
        ("Белова Ольга, ИУ6-31М", ["Белова Ольга"]),
        ("К. Д. Лебедев", ["К. Д. Лебедев"]),
        ("ИУ6-31М", []),
        ("Группа: ИУ6-31М", []),
        ("Научный руководитель: Ершов А. В.", []),
        ("Обзор подходов и примеров использования", []),
    ],
)
def test_speakers_in_line(line: str, speakers: list[str]) -> None:
    assert speakers_in_line(line) == speakers


def test_affiliation_is_not_mistaken_for_speaker() -> None:
    candidates = candidates_from_lines(
        "Поиск аномалий", ["МГТУ им. Н. Э. Баумана", "Выполнил: Орлов М. А."]
    )

    assert candidates.speakers == ["Орлов М. А."]
    assert candidates.affiliations == ["МГТУ им. Н. Э. Баумана"]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Выводы", True),
        ("Заключение", True),
        ("Основные выводы", True),
        ("Conclusions", True),
        ("Результаты", False),
        ("Промежуточным результатом сравнения является карта отличий", False),
        ("Выводы " + "и рекомендации " * 6, False),
        (None, False),
    ],
)
def test_is_conclusion(title: str | None, expected: bool) -> None:
    assert is_conclusion(title) is expected


def test_pdf_heading_is_largest_font_run() -> None:
    lines = [
        PdfLine("Автоэнкодеры: от теории", 32.9),
        PdfLine("к практике", 32.9),
        PdfLine("Обзор применения в телеметрии", 18.8),
        PdfLine("Подготовил: Лебедев К. Д.", 22.4),
    ]

    assert pdf_heading(lines) == ("Автоэнкодеры: от теории к практике", [0, 1])
    assert pdf_heading([]) == (None, [])


def test_pdf_heading_stops_at_byline_of_same_size() -> None:
    lines = [
        PdfLine("Методы прогнозирования", 28.0),
        PdfLine("временных рядов", 28.0),
        PdfLine("Орлов Максим ИУ6-31М", 28.0),
    ]

    assert pdf_heading(lines) == ("Методы прогнозирования временных рядов", [0, 1])


def test_dictionary_terms() -> None:
    texts = [
        "Используем LSTM-автоэнкодер и Text-to-SQL, код на C++: https://example.org/LSTM",
        "ИИ-модели и ГОСТ Р; список of terms",
        "АВТОЭНКОДЕРЫ ДЛЯ ЗАДАЧИ СЖАТИЯ",
        "ВВЕДЕНИЕ",
        "ДЛЯ ЗАДАЧИ",
        "вес τij, цвет aae7a7",
    ]

    assert build_dictionary(texts, ["Орлов М. А.", "лстм"]) == [
        "LSTM",
        "Text-to-SQL",
        "C++",
        "ИИ",
        "ГОСТ",
        "terms",
        "Орлов М. А.",
        "лстм",
    ]
