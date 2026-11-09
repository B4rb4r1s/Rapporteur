"""Промпты из версионируемых файлов prompts/<шаг>/<версия>.md (spec §9, принцип 5).

Файл — Markdown: свободное описание, затем разделы «## system», «## user», «## repair».
Текст раздела — шаблон Jinja; неизвестная переменная — ошибка, а не пустое место.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jinja2

from rapporteur.settings import PROMPTS_DIR

SECTIONS = ("system", "user", "repair")


class PromptError(Exception):
    pass


@dataclass(frozen=True)
class Prompt:
    step: str
    version: str
    templates: dict[str, jinja2.Template] = field(repr=False)

    def render(self, section: str, **context: Any) -> str:
        try:
            return self.templates[section].render(**context).strip()
        except jinja2.TemplateError as error:
            raise PromptError(
                f"Промпт {self.step}/{self.version}, раздел {section}: {error}"
            ) from error


def load_prompt(step: str, version: str, directory: Path = PROMPTS_DIR) -> Prompt:
    path = directory / step / f"{version}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise PromptError(f"Нет промпта {path}") from error

    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("## ") and line[3:].strip() in SECTIONS:
            current = sections.setdefault(line[3:].strip(), [])
        elif current is not None:
            current.append(line)
    missing = [name for name in SECTIONS if name not in sections]
    if missing:
        raise PromptError(f"В {path} нет разделов: {', '.join(missing)}")

    environment = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )
    try:
        templates = {
            name: environment.from_string("\n".join(lines)) for name, lines in sections.items()
        }
    except jinja2.TemplateSyntaxError as error:
        raise PromptError(f"Ошибка в разметке {path}: {error}") from error
    return Prompt(step=step, version=version, templates=templates)
