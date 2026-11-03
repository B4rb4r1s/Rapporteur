"""Русская грамматика бланка справки (spec §6.9): период, согласование фразы с числом,
кавычки-«ёлочки» и неразрывные пробелы. Числа и даты подставляет код, а не модель (принцип 2).
"""

import datetime as dt
import re
from functools import cache
from typing import Any

import pymorphy3

NBSP = "\N{NO-BREAK SPACE}"
EN_DASH = "\N{EN DASH}"

_MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

# Падежи по-русски, как их пишет автор бланка: {{ n | согласовать("докладчик", падеж="род") }}.
CASES = {"им": "nomn", "род": "gent", "дат": "datv", "вин": "accs", "твор": "ablt", "пред": "loct"}


def period(start: dt.date, end: dt.date | None = None) -> str:
    """«22 октября 2026 г.», «20–22 октября 2026 г.», «30 октября – 1 ноября 2026 г.»,
    «30 декабря 2026 г. – 2 января 2027 г.»."""
    if end is None or end == start:
        return f"{_day_month(start)} {_year(start)}"
    if end < start:
        raise ValueError("конец периода раньше начала")
    if start.year != end.year:
        return f"{_day_month(start)} {_year(start)}{NBSP}{EN_DASH} {_day_month(end)} {_year(end)}"
    if start.month != end.month:
        return f"{_day_month(start)}{NBSP}{EN_DASH} {_day_month(end)} {_year(end)}"
    return f"{start.day}{EN_DASH}{end.day}{NBSP}{_MONTHS[end.month - 1]} {_year(end)}"


def _day_month(day: dt.date) -> str:
    return f"{day.day}{NBSP}{_MONTHS[day.month - 1]}"


def _year(day: dt.date) -> str:
    return f"{day.year}{NBSP}г."


@cache
def _morph() -> pymorphy3.MorphAnalyzer:
    return pymorphy3.MorphAnalyzer()


def agree(number: int, noun: str, verb: str | None = None, case: str = "им") -> str:
    """Согласует существительное с числом, а при необходимости и глагол перед числом:
    «заслушан 1 доклад», «заслушано 3 доклада», «выступили 25 докладчиков»."""
    if number < 0:
        raise ValueError("число для согласования не может быть отрицательным")
    if case not in CASES:
        raise ValueError(f"неизвестный падеж «{case}»: допустимы {', '.join(CASES)}")
    if verb is not None and case != "им":
        raise ValueError("глагол согласуется только с числом в именительном падеже")

    parsed = _parse(noun, {"NOUN"})
    in_case = parsed.inflect({CASES[case]}) if case != "им" else parsed
    agreed = in_case.make_agree_with_number(number) if in_case is not None else None
    if agreed is None:
        raise ValueError(f"не удалось согласовать «{noun}» с числом {number}")
    phrase = f"{number}{NBSP}{_like(noun, agreed.word)}"
    if verb is None:
        return phrase
    return f"{_agree_verb(verb, number, parsed.tag.gender)} {phrase}"


def _agree_verb(verb: str, number: int, gender: str | None) -> str:
    """С числами на 1, кроме 11, глагол в единственном числе и роде существительного.
    С остальными краткое причастие в среднем роде («заслушано»), глагол во множественном
    («выступили»)."""
    parsed = _parse(verb, {"PRTS", "VERB"})
    if number % 10 == 1 and number % 100 != 11:
        grammemes = {"sing", gender or "masc"}
    elif parsed.tag.POS == "PRTS":
        grammemes = {"sing", "neut"}
    else:
        grammemes = {"plur"}
    inflected = parsed.inflect(grammemes)
    if inflected is None:
        raise ValueError(f"не удалось согласовать глагол «{verb}» с числом {number}")
    return _like(verb, inflected.word)


def _parse(word: str, parts_of_speech: set[str]) -> Any:
    for parsed in _morph().parse(word):
        if parsed.tag.POS in parts_of_speech:
            return parsed
    raise ValueError(f"«{word}» не распознано как {' или '.join(sorted(parts_of_speech))}")


def _like(original: str, word: str) -> str:
    """pymorphy3 возвращает слова строчными; заглавная буква из бланка сохраняется."""
    return word[:1].upper() + word[1:] if original[:1].isupper() else word


_OPENING_QUOTES = '«"“„'
_CLOSING_QUOTES = '»"”“'
_STRAIGHT_QUOTED = re.compile(r'"([^"]*)"')


def guillemets(text: str) -> str:
    """Название в «ёлочках»; кавычки внутри становятся „лапками“. Если название уже в кавычках,
    они не удваиваются: «„Данные“» не получится из «Данные»."""
    inner = text.strip()
    if (
        len(inner) >= 2
        and inner[0] in _OPENING_QUOTES
        and inner[-1] in _CLOSING_QUOTES
        and _balanced(inner[1:-1])
    ):
        inner = inner[1:-1].strip()
    inner = inner.replace("«", "„").replace("»", "“")
    inner = _STRAIGHT_QUOTED.sub("„\\1“", inner)
    return f"«{inner}»"


def _balanced(text: str) -> bool:
    """«Данные» и «Знания» начинается и кончается кавычкой, но это две пары, а не одна внешняя."""
    depth = 0
    for char in text:
        depth += {"«": 1, "»": -1}.get(char, 0)
        if depth < 0:
            return False
    return depth == 0 and text.count('"') % 2 == 0


# Число держится рядом со следующим словом или знаком: «23 %», «2026 г.», «4 узла».
_NUMBER_AND_WORD = re.compile(r"(\d) (?=[%‰°]|[А-Яа-яЁё])")
_SIGN_AND_NUMBER = re.compile(r"([№§]) (?=\d)")


def typography(text: str) -> str:
    """Неразрывные пробелы между числом и единицей, после № и §."""
    text = _NUMBER_AND_WORD.sub(f"\\1{NBSP}", text)
    return _SIGN_AND_NUMBER.sub(f"\\1{NBSP}", text)
