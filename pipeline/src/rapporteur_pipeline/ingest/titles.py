"""Титульные данные и пометка слайдов с выводами (spec §6.2).

Это подсказки для привязки файлов к докладам (P1-05): в справку название и докладчики идут
только из реестра (ADR-008, п. 6). Эвристики настроены на титульные слайды вида
«Название / Подготовил: Фамилия И. О. / Группа» и «Фамилия Имя, группа».
"""

import re
from collections.abc import Sequence

from rapporteur_contracts.artifacts import TitleCandidates
from rapporteur_pipeline.ingest.pdf_reader import PdfLine, PdfPage
from rapporteur_pipeline.ingest.pptx_reader import ExtractedSlide
from rapporteur_pipeline.text import normalize_text

_UPPER = "А-ЯЁ"
_LOWER = "а-яё"
_SURNAME = rf"[{_UPPER}][{_LOWER}]+(?:-[{_UPPER}][{_LOWER}]+)?"
_INITIALS = rf"[{_UPPER}]\.\s?[{_UPPER}]\.?"
_NAME_PATTERNS = (
    re.compile(rf"{_SURNAME}\s+{_INITIALS}"),  # Иванов И. И.
    re.compile(rf"[{_UPPER}]\.\s?[{_UPPER}]\.\s?{_SURNAME}"),  # И. И. Иванов
)
# Фамилия и имя (и отчество) целиком: строка из 2–3 слов с заглавной буквы.
_FULL_NAME = re.compile(rf"^{_SURNAME}(?:\s+[{_UPPER}][{_LOWER}]+){{1,2}}$")
# Учебная группа вроде ИУ6-31М: к докладчику не относится.
_GROUP_CODE = re.compile(rf"\b[{_UPPER}]{{1,5}}\d{{1,2}}[{_UPPER}]?-\d{{2,3}}[{_UPPER}]?\b")
_SPEAKER_LABEL = re.compile(
    r"^(?:подготовил[аи]?|выполнил[аи]?|студент(?:ка|ы)?|докладчик(?:и)?|автор(?:ы)?)\s*:?\s*",
    re.IGNORECASE,
)
_NOT_SPEAKER = re.compile(r"руководител|преподавател|проверил", re.IGNORECASE)
_AFFILIATION = re.compile(
    r"университет|институт|академи|кафедр|факультет|лаборатори|\bМГТУ\b|\bМГУ\b|\bРАН\b|"
    r"\bНИИ\b|university|institute",
    re.IGNORECASE,
)
_CONCLUSION = re.compile(
    r"^(?:основные\s+)?(?:выводы?|заключение|итоги|conclusions?)\b", re.IGNORECASE
)
_MAX_CONCLUSION_TITLE = 60
# Строки шрифтом не меньше этой доли от крупнейшего на странице считаются заголовком.
_TITLE_FONT_SHARE = 0.9
_MAX_PDF_TITLE = 160
# Длинный текст в плейсхолдере может быть обычным текстом слайда.
MAX_PLACEHOLDER_TITLE = 100


def pptx_title(first: ExtractedSlide, heading: str | None = None) -> TitleCandidates:
    """Название из плейсхолдера или верхнего текстового блока титульного слайда."""
    blocks = [" ".join(block.split()) for block in map(normalize_text, first.blocks) if block]
    if first.title is not None and len(first.title) <= MAX_PLACEHOLDER_TITLE:
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


def pdf_title(first: PdfPage) -> TitleCandidates:
    title, used = pdf_heading(first.lines)
    others = [line.text for index, line in enumerate(first.lines) if index not in used]
    return candidates_from_lines(title, others)


def is_conclusion(title: str | None) -> bool:
    """«Выводы», «Заключение», «Итоги». «Результаты» не помечаются: результаты — не выводы."""
    if not title or len(title) > _MAX_CONCLUSION_TITLE:
        return False
    return _CONCLUSION.match(title.strip()) is not None


def pdf_heading(lines: Sequence[PdfLine]) -> tuple[str | None, list[int]]:
    """Заголовок страницы PDF: первые подряд идущие строки самого крупного шрифта.

    Строка с автором бывает набрана тем же кеглем, что и название, поэтому на ней заголовок
    обрывается. Возвращает текст заголовка и номера строк, которые в него вошли.
    """
    largest = max((line.font_size for line in lines), default=0.0)
    if largest <= 0:
        return None, []
    indexes: list[int] = []
    for index, line in enumerate(lines):
        large = line.font_size >= largest * _TITLE_FONT_SHARE
        if large and not _is_byline(_one_line(normalize_text(line.text))):
            indexes.append(index)
        elif indexes:
            break
    text = _one_line(normalize_text("\n".join(lines[index].text for index in indexes)))
    return (text if 0 < len(text) <= _MAX_PDF_TITLE else None), indexes


def candidates_from_lines(title: str | None, other_lines: Sequence[str]) -> TitleCandidates:
    speakers: list[str] = []
    affiliations: list[str] = []
    for line in (_one_line(normalize_text(line)) for line in other_lines):
        if not line:
            continue
        if _AFFILIATION.search(line):
            _append_unique(affiliations, line)
            continue
        for speaker in speakers_in_line(line):
            _append_unique(speakers, speaker)
    return TitleCandidates(
        title=_one_line(title) or None, speakers=speakers, affiliations=affiliations
    )


def speakers_in_line(line: str) -> list[str]:
    if _NOT_SPEAKER.search(line):
        return []
    rest = _SPEAKER_LABEL.sub("", line, count=1)
    rest = _GROUP_CODE.sub(" ", rest)
    rest = re.sub(r"\b[Гг]руппа\b\s*:?", " ", rest)
    rest = " ".join(rest.replace(",", " ").split())

    found = [
        _one_line(match.group(0)) for pattern in _NAME_PATTERNS for match in pattern.finditer(rest)
    ]
    if found:
        return found
    candidate = rest.strip(" .;:")
    return [candidate] if _FULL_NAME.match(candidate) else []


def _is_byline(line: str) -> bool:
    """Строка с автором или группой: «Подготовил: …», «Иванов И. И.», «ИУ6-31М»."""
    return bool(_GROUP_CODE.search(line) or _SPEAKER_LABEL.match(line) or speakers_in_line(line))


def _one_line(text: str | None) -> str:
    return " ".join((text or "").split())


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)
