"""Общие типы контрактов."""

from datetime import datetime, timedelta
from typing import Annotated

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    StringConstraints,
)
from pydantic_core import PydanticCustomError


class ContractModel(BaseModel):
    """Неизменяемая модель без лишних полей: опечатка в имени поля — ошибка, а не тишина."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_utc(value: datetime) -> datetime:
    if value.utcoffset() != timedelta(0):
        raise PydanticCustomError("not_utc", "время должно быть в UTC")
    return value


def _require_ordered(value: tuple[int, int]) -> tuple[int, int]:
    start, end = value
    if start >= end:
        raise PydanticCustomError("empty_span", "диапазон должен быть непустым: start < end")
    return value


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_require_utc)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
Probability = Annotated[float, Field(ge=0, le=1)]
Milliseconds = NonNegativeInt
StageName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]

# Полуоткрытый диапазон [start, end) в кодовых точках normalized_text ревизии (ADR-003, ADR-008).
Span = Annotated[tuple[NonNegativeInt, NonNegativeInt], AfterValidator(_require_ordered)]


def require_interval(start_ms: int, end_ms: int) -> None:
    if start_ms >= end_ms:
        raise PydanticCustomError("empty_interval", "начало должно быть раньше конца")
