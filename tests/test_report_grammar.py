import datetime as dt
from collections.abc import Callable

import pytest

from rapporteur.report.grammar import NBSP, agree, guillemets, period, typography


@pytest.mark.parametrize(
    ("number", "phrase"),
    [
        (1, "заслушан 1 доклад"),
        (2, "заслушано 2 доклада"),
        (5, "заслушано 5 докладов"),
        (11, "заслушано 11 докладов"),
        (21, "заслушан 21 доклад"),
        (22, "заслушано 22 доклада"),
        (25, "заслушано 25 докладов"),
        (111, "заслушано 111 докладов"),
    ],
)
def test_short_participle_agrees_with_number(number: int, phrase: str) -> None:
    verb, rest = phrase.split(" ", 1)
    assert agree(number, "доклад", verb="заслушан") == f"{verb} {rest.replace(' ', NBSP)}"


@pytest.mark.parametrize(
    ("number", "nominative", "genitive"),
    [
        (1, "докладчик", "докладчика"),
        (2, "докладчика", "докладчиков"),
        (5, "докладчиков", "докладчиков"),
        (11, "докладчиков", "докладчиков"),
        (21, "докладчик", "докладчика"),
        (22, "докладчика", "докладчиков"),
        (25, "докладчиков", "докладчиков"),
        (111, "докладчиков", "докладчиков"),
    ],
)
def test_noun_agrees_in_case(number: int, nominative: str, genitive: str) -> None:
    assert agree(number, "докладчик") == f"{number}{NBSP}{nominative}"
    assert agree(number, "докладчик", case="род") == f"{number}{NBSP}{genitive}"


@pytest.mark.parametrize(
    ("number", "noun", "verb", "expected"),
    [
        (1, "работа", "заслушан", f"заслушана 1{NBSP}работа"),
        (3, "работа", "заслушан", f"заслушано 3{NBSP}работы"),
        (1, "докладчик", "выступил", f"выступил 1{NBSP}докладчик"),
        (3, "докладчик", "выступил", f"выступили 3{NBSP}докладчика"),
        (21, "докладчик", "выступил", f"выступил 21{NBSP}докладчик"),
        (3, "доклад", "Заслушан", f"Заслушано 3{NBSP}доклада"),
    ],
)
def test_verb_follows_gender_and_kind(number: int, noun: str, verb: str, expected: str) -> None:
    assert agree(number, noun, verb=verb) == expected


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda: agree(-1, "доклад"), "отрицательным"),
        (lambda: agree(3, "доклад", case="звательный"), "неизвестный падеж"),
        (lambda: agree(3, "доклад", verb="заслушан", case="род"), "именительном"),
        (lambda: agree(3, "быстро"), "не распознано"),
    ],
    ids=["negative", "unknown-case", "verb-in-genitive", "not-a-noun"],
)
def test_agree_rejects_bad_arguments(call: Callable[[], str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        call()


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (dt.date(2026, 10, 22), None, "22 октября 2026 г."),
        (dt.date(2026, 10, 22), dt.date(2026, 10, 22), "22 октября 2026 г."),
        (dt.date(2026, 10, 20), dt.date(2026, 10, 22), "20–22 октября 2026 г."),
        (dt.date(2026, 10, 30), dt.date(2026, 11, 1), "30 октября – 1 ноября 2026 г."),
        (dt.date(2026, 12, 30), dt.date(2027, 1, 2), "30 декабря 2026 г. – 2 января 2027 г."),
    ],
    ids=["day", "same-day", "month", "across-months", "across-years"],
)
def test_period(start: dt.date, end: dt.date | None, expected: str) -> None:
    assert period(start, end).replace(NBSP, " ") == expected


def test_period_keeps_numbers_with_words() -> None:
    assert period(dt.date(2026, 10, 30), dt.date(2026, 11, 1)) == (
        f"30{NBSP}октября{NBSP}\N{EN DASH} 1{NBSP}ноября 2026{NBSP}г."
    )


def test_period_rejects_reversed_dates() -> None:
    with pytest.raises(ValueError, match="раньше начала"):
        period(dt.date(2026, 10, 22), dt.date(2026, 10, 20))


@pytest.mark.parametrize(
    ("raw", "quoted"),
    [
        ("Данные", "«Данные»"),
        ("«Данные»", "«Данные»"),
        ('"Данные"', "«Данные»"),
        ("Конференция «Данные»", "«Конференция „Данные“»"),
        ("«Данные» и «Знания»", "«„Данные“ и „Знания“»"),
        ('Форум "Осень-2026"', "«Форум „Осень-2026“»"),
    ],
)
def test_guillemets(raw: str, quoted: str) -> None:
    assert guillemets(raw) == quoted


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("снизила задержку на 23 %", f"снизила задержку на 23{NBSP}%"),
        ("в 2026 г. на 4 узлах", f"в 2026{NBSP}г. на 4{NBSP}узлах"),
        ("приказ № 5", f"приказ №{NBSP}5"),
        ("версия 2 из 3", f"версия 2{NBSP}из 3"),
    ],
)
def test_typography(raw: str, expected: str) -> None:
    assert typography(raw) == expected
