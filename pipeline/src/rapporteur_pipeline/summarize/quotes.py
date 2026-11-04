"""Поиск цитаты модели в тексте фрагмента (spec §6.7, шаг Б; ADR-003; ADR-008, п. 1–2).

Сравнение идёт по поисковой форме: нижний регистр, ё → е, без пунктуации, кавычек и тире,
пробелы схлопнуты, числа целиком. Форма живёт только во время поиска, а карта смещений
переводит найденное место обратно в нормализованный текст ревизии: span указывает на исходное
написание с ё, кавычками и тире.

Нечёткое сравнение само по себе пропускает подмену числа или снятое отрицание: «84,5» вместо
«94,5» даёт оценку выше 95 из 100. Поэтому числа и отрицания `не`, `ни` сверяются точно.
"""

from collections import Counter
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator
from rapidfuzz import fuzz

from rapporteur_contracts.enums import EvidenceRejection

_NEGATIONS = frozenset({"не", "ни"})


class QuoteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Оценка совпадения от 0 до 1, не ниже которой цитата подтверждается.
    min_score: float = Field(default=0.9, ge=0, le=1)
    # Ниже этой оценки цитата считается не найденной, а не искажённой.
    not_found_below: float = Field(default=0.6, ge=0, le=1)
    # Короткая цитата вроде «94» находится где угодно и ничего не подтверждает.
    min_quote_chars: PositiveInt = 10

    @model_validator(mode="after")
    def _ordered(self) -> "QuoteConfig":
        if self.not_found_below > self.min_score:
            raise ValueError("not_found_below не может быть больше min_score")
        return self


@dataclass(frozen=True)
class SearchForm:
    text: str
    # offsets[i] — позиция символа text[i] в исходном тексте.
    offsets: tuple[int, ...]

    def source_span(self, start: int, end: int) -> tuple[int, int]:
        return self.offsets[start], self.offsets[end - 1] + 1


def search_form(text: str) -> SearchForm:
    chars: list[str] = []
    offsets: list[int] = []
    for index, char in enumerate(text):
        lower = char.lower()
        if lower == "ё":
            lower = "е"
        if lower.isalnum():
            chars.append(lower)
            offsets.append(index)
        elif char in ",." and _between_digits(text, index):
            # 94,5 и 94.5 — одно и то же число.
            chars.append(",")
            offsets.append(index)
        elif char.isspace() and _thousands_gap(text, index):
            # 1 000 и 1000 — одно и то же число.
            continue
        elif chars and chars[-1] != " ":
            chars.append(" ")
            offsets.append(index)
    if chars and chars[-1] == " ":
        chars.pop()
        offsets.pop()
    return SearchForm("".join(chars), tuple(offsets))


def _between_digits(text: str, index: int) -> bool:
    return 0 < index < len(text) - 1 and text[index - 1].isdigit() and text[index + 1].isdigit()


def _thousands_gap(text: str, index: int) -> bool:
    """Пробел между разрядами: слева 1–3 цифры, справа ровно три."""
    left = index
    while left > 0 and text[left - 1].isdigit():
        left -= 1
    right = text[index + 1 : index + 4]
    after = text[index + 4 : index + 5]
    return 1 <= index - left <= 3 and len(right) == 3 and right.isdigit() and not after.isdigit()


@dataclass(frozen=True)
class QuoteMatch:
    span: tuple[int, int] | None
    score: float | None
    rejection: EvidenceRejection | None


def match_quote(quote: str, source: str, config: QuoteConfig) -> QuoteMatch:
    """Ищет цитату в тексте фрагмента. span — в кодовых точках нормализованного текста."""
    wanted = search_form(quote)
    found_in = search_form(source)
    if len(wanted.text) < config.min_quote_chars:
        return QuoteMatch(None, None, EvidenceRejection.TOO_SHORT)
    if not found_in.text:
        return QuoteMatch(None, 0.0, EvidenceRejection.QUOTE_NOT_FOUND)

    if len(wanted.text) > len(found_in.text):
        # Цитата длиннее фрагмента: частичное сравнение простило бы приписанный хвост.
        score = fuzz.ratio(wanted.text, found_in.text) / 100
        start, end = 0, len(found_in.text)
    else:
        alignment = fuzz.partial_ratio_alignment(wanted.text, found_in.text)
        if alignment is None:
            return QuoteMatch(None, 0.0, EvidenceRejection.QUOTE_NOT_FOUND)
        score = alignment.score / 100
        start, end = _word_bounds(found_in.text, alignment.dest_start, alignment.dest_end)

    if score < config.not_found_below:
        return QuoteMatch(None, score, EvidenceRejection.QUOTE_NOT_FOUND)
    if score < config.min_score:
        return QuoteMatch(None, score, EvidenceRejection.BELOW_THRESHOLD)

    quote_tokens = wanted.text.split(" ")
    place_tokens = found_in.text[start:end].split(" ")
    if not _numbers_present(quote_tokens, place_tokens):
        return QuoteMatch(None, score, EvidenceRejection.NUMBER_MISMATCH)
    if not _negations_match(quote_tokens, place_tokens, found_in.text[:start]):
        return QuoteMatch(None, score, EvidenceRejection.NEGATION_MISMATCH)
    return QuoteMatch(found_in.source_span(start, end), score, None)


def _word_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Найденное место расширяется до целых слов и обрезается по пробелам."""
    while start > 0 and text[start - 1] != " ":
        start -= 1
    while end < len(text) and text[end] != " ":
        end += 1
    while start < end and text[start] == " ":
        start += 1
    while end > start and text[end - 1] == " ":
        end -= 1
    return start, end


def _numbers_present(quote_tokens: list[str], place_tokens: list[str]) -> bool:
    """Каждое число цитаты есть в найденном месте. Лишние числа источника не мешают:
    цитата может опускать часть фразы, но не может менять числа."""
    numbers = Counter(token for token in quote_tokens if any(c.isdigit() for c in token))
    available = Counter(token for token in place_tokens if any(c.isdigit() for c in token))
    return not numbers - available


def _negations_match(quote_tokens: list[str], place_tokens: list[str], before: str) -> bool:
    """Отрицаний столько же, сколько в найденном месте; «не» сразу перед местом тоже считается."""
    in_place = sum(token in _NEGATIONS for token in place_tokens)
    previous = before.rstrip().rsplit(" ", 1)[-1] if before.strip() else ""
    if previous in _NEGATIONS and (not quote_tokens or quote_tokens[0] not in _NEGATIONS):
        in_place += 1
    return sum(token in _NEGATIONS for token in quote_tokens) == in_place
