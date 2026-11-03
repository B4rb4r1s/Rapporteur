"""Нормализованный текст фрагмента (ADR-008, п. 2).

normalized_text — отображаемый текст: NFC, единые переводы строк и пробелы. Регистр, пунктуация,
числа и ё сохраняются, потому что по нему считается span и его видит человек.
"""

import re
import unicodedata

# Пробелы, которые встречаются в презентациях: табуляция, неразрывные, тонкие, идеографический.
_SPACES = re.compile(
    r"[ \t\N{NO-BREAK SPACE}\N{EN QUAD}-\N{HAIR SPACE}\N{NARROW NO-BREAK SPACE}"
    r"\N{MEDIUM MATHEMATICAL SPACE}\N{IDEOGRAPHIC SPACE}]+"
)
# Символы нулевой ширины и мягкий перенос не видны читателю и мешают поиску цитат.
_INVISIBLE = re.compile(
    r"[\N{SOFT HYPHEN}\N{ZERO WIDTH SPACE}\N{ZERO WIDTH NON-JOINER}\N{ZERO WIDTH JOINER}"
    r"\N{WORD JOINER}\N{ZERO WIDTH NO-BREAK SPACE}]"
)
_LINE_BREAKS = re.compile(r"\r\n|[\r\x0b\x0c\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}]")
# PDFium помечает дефис, который может быть переносом, символом U+FFFE в тексте страницы
# и U+0002 в посимвольном API. Перед переводом строки это перенос слова, иначе обычный дефис.
_PDF_HYPHEN = "[\x02" + chr(0xFFFE) + "]"
_PDF_HYPHEN_AT_BREAK = re.compile(_PDF_HYPHEN + "\n")
_PDF_HYPHEN_INLINE = re.compile(_PDF_HYPHEN)
_CONTROL = re.compile(r"[\x00-\x08\x0e-\x1f\x7f]")


def normalize_text(raw: str) -> str:
    text = unicodedata.normalize("NFC", raw)
    text = _LINE_BREAKS.sub("\n", text)
    text = _PDF_HYPHEN_AT_BREAK.sub("", text)
    text = _PDF_HYPHEN_INLINE.sub("-", text)
    text = _CONTROL.sub("", text)
    text = _INVISIBLE.sub("", text)
    text = _SPACES.sub(" ", text)

    lines: list[str] = []
    for line in (line.strip() for line in text.split("\n")):
        if line or (lines and lines[-1]):
            lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)
