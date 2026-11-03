"""Словарь доклада: слова на латинице, аббревиатуры, ФИО (spec §6.2).

Нужен распознаванию речи как подсказка и коррекции терминов (§6.4, §6.6). Специальные термины
на русском без заглавных букв сюда не попадают: их без языковой модели не отличить от обычных слов.
"""

import re
from collections.abc import Iterable

_URL = re.compile(r"(?:https?://|www\.)\S+|\S+@\S+")
# Text-to-SQL, C++, F1-score, PyTorch: буквы и цифры с внутренними -, +, #, точкой.
# Дефис с русским словом после термина не мешает: «LSTM-автоэнкодер» даёт LSTM.
_LATIN = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*(?:[-+.#][A-Za-z0-9+#]+)*\+*(?![A-Za-z0-9])"
)
# ИИ, СНС, ГОСТ, ИУ6-31М: аббревиатура из заглавных, без строчных букв; «ИИ-модели» даёт ИИ.
_ABBREVIATION = re.compile(
    r"(?<![А-ЯЁа-яё0-9])[А-ЯЁ]{2,}[А-ЯЁ0-9]*(?:-[А-ЯЁ0-9]+)*(?![А-ЯЁа-яё0-9])"
)
# Аббревиатура из одних букв обычно не длиннее пяти; с цифрами и дефисом бывает длиннее.
_MAX_LETTER_ABBREVIATION = 5
_MAX_ABBREVIATION = 8
# Строка из нескольких длинных слов заглавными — заголовок, а не набор аббревиатур.
_CAPS_WORD = re.compile(r"\b[А-ЯЁ]{4,}\b")
# Заголовки заглавными разбиваются на строки, и служебные слова попадают в них по одному.
_CAPS_STOPWORDS = frozenset(
    "БЕЗ ВСЕ ГДЕ ДЛЯ ДО ЕГО ЕЁ ЕСЛИ ЖЕ ЗА ИЗ ИЛИ ИХ КАК КТО ЛИ НА НЕ НО ОН ОНА ОНИ ОТ ПО ПРИ "
    "ПРО ТАК ТО ЧТО ЭТО".split()
)
_ENGLISH_STOPWORDS = frozenset(
    "a an and are as at be by for from in into is it of on or the to vs with".split()
)
# Хэши и цвета вроде aae7a7 — не термины.
_HEX = re.compile(r"^(?=.*\d)[0-9a-fA-F]{5,}$")


def _is_latin_term(term: str) -> bool:
    if len(term) < 2 or term.casefold() in _ENGLISH_STOPWORDS or _HEX.match(term):
        return False
    # Индексы формул вроде ij — не термины, а ML, Qt и PyTorch — термины.
    return not (term.islower() and len(term) <= 2)


def _is_abbreviation(term: str) -> bool:
    if term in _CAPS_STOPWORDS:
        return False
    has_digits = any(char.isdigit() or char == "-" for char in term)
    return len(term) <= (_MAX_ABBREVIATION if has_digits else _MAX_LETTER_ABBREVIATION)


def build_dictionary(texts: Iterable[str], names: Iterable[str]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            terms.append(term)

    for text in texts:
        for line in _URL.sub(" ", text).splitlines():
            found = [
                (match.start(), match.group(0))
                for match in _LATIN.finditer(line)
                if _is_latin_term(match.group(0))
            ]
            if len(_CAPS_WORD.findall(line)) < 2:
                found += [
                    (match.start(), match.group(0))
                    for match in _ABBREVIATION.finditer(line)
                    if _is_abbreviation(match.group(0))
                ]
            for _, term in sorted(found):
                add(term)
    for name in names:
        add(name)
    return terms
