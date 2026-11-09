"""Бланк справки: DOCX с плейсхолдерами Jinja (docxtpl) и описание переменных в YAML (spec §6.9).

Бланк заменяется без правки кода (принцип 9): каталог бланка содержит variables.yaml и DOCX.
Код проверяет, что бланк использует только описанные переменные, а контекст содержит все
обязательные, и только потом рендерит.
"""

import datetime as dt
import io
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import jinja2
import yaml
from docxtpl import DocxTemplate
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from rapporteur.report.grammar import agree, guillemets, period, typography

VARIABLES_FILE = "variables.yaml"


class VariableType(StrEnum):
    STR = "str"
    INT = "int"
    PERIOD = "period"
    LIST = "list"


class VariableSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1)
    type: VariableType
    required: bool
    description: str = ""


class TemplateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1)
    document: str = Field(min_length=1)
    variables: dict[str, VariableSpec]


class TemplateErrorCode(StrEnum):
    INVALID_SPEC = "invalid_spec"
    UNDECLARED_VARIABLE = "undeclared_variable"
    MISSING_VARIABLE = "missing_variable"
    INVALID_VALUE = "invalid_value"
    RENDER_FAILED = "render_failed"


class TemplateError(Exception):
    def __init__(self, code: TemplateErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Period:
    """Даты справки: день начала и окончания включительно."""

    start: dt.date
    end: dt.date


def _period_filter(value: Period | dt.date) -> str:
    if isinstance(value, Period):
        return period(value.start, value.end)
    return period(value)


def _agree_filter(number: int, noun: str, глагол: str | None = None, падеж: str = "им") -> str:
    """Аргументы по-русски, как их пишет автор бланка: согласовать("доклад", глагол="заслушан")."""
    return agree(number, noun, verb=глагол, case=падеж)


def make_environment() -> jinja2.Environment:
    """StrictUndefined: опечатка в бланке — ошибка, а не тихо пропавший текст (принцип 11)."""
    environment = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=True)
    environment.filters.update(
        {
            "период": _period_filter,
            "согласовать": _agree_filter,
            "ёлочки": guillemets,
            "типографика": typography,
        }
    )
    return environment


@dataclass(frozen=True)
class Template:
    spec: TemplateSpec
    document: Path


def load_template(directory: Path) -> Template:
    try:
        raw = yaml.safe_load((directory / VARIABLES_FILE).read_text(encoding="utf-8"))
        spec = TemplateSpec.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise TemplateError(
            TemplateErrorCode.INVALID_SPEC, f"Описание переменных бланка не читается: {error}"
        ) from error

    document = directory / spec.document
    if not document.is_file():
        raise TemplateError(TemplateErrorCode.INVALID_SPEC, f"Нет файла бланка {spec.document}")
    try:
        used = DocxTemplate(document).get_undeclared_template_variables(make_environment())
    except jinja2.TemplateError as error:
        raise TemplateError(
            TemplateErrorCode.INVALID_SPEC, f"Ошибка в разметке бланка: {error}"
        ) from error
    undeclared = sorted(set(used) - set(spec.variables))
    if undeclared:
        raise TemplateError(
            TemplateErrorCode.UNDECLARED_VARIABLE,
            f"Бланк использует переменные, которых нет в {VARIABLES_FILE}: {', '.join(undeclared)}",
        )
    return Template(spec=spec, document=document)


def check_context(spec: TemplateSpec, context: Mapping[str, Any]) -> dict[str, Any]:
    """Все описанные переменные попадают в контекст: необязательные без значения — как None."""
    checked: dict[str, Any] = {}
    for name, variable in spec.variables.items():
        value = context.get(name)
        if _is_empty(value):
            if variable.required:
                raise TemplateError(
                    TemplateErrorCode.MISSING_VARIABLE,
                    f"Не задана обязательная переменная бланка «{name}» ({variable.source})",
                )
            checked[name] = None
            continue
        if not _has_type(value, variable.type):
            raise TemplateError(
                TemplateErrorCode.INVALID_VALUE,
                f"Переменная бланка «{name}» должна быть типа {variable.type.value}",
            )
        checked[name] = value
    return checked


def _is_empty(value: object) -> bool:
    return value is None or (isinstance(value, (str, list, tuple)) and len(value) == 0)


def _has_type(value: object, expected: VariableType) -> bool:
    match expected:
        case VariableType.STR:
            return isinstance(value, str)
        case VariableType.INT:
            return isinstance(value, int) and not isinstance(value, bool) and value >= 0
        case VariableType.PERIOD:
            return isinstance(value, Period) and value.start <= value.end
        case VariableType.LIST:
            return isinstance(value, (list, tuple))


def render_docx(template: Template, context: Mapping[str, Any]) -> bytes:
    checked = check_context(template.spec, context)
    document = DocxTemplate(template.document)
    try:
        document.render(checked, jinja_env=make_environment(), autoescape=True)
    except (jinja2.TemplateError, ValueError) as error:
        raise TemplateError(
            TemplateErrorCode.RENDER_FAILED, f"Бланк не собирается: {error}"
        ) from error
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
