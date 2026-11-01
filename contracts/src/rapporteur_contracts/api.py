"""Общие формы ответов API (spec §11)."""

from typing import Any
from uuid import UUID

from pydantic import NonNegativeInt, PositiveInt

from rapporteur_contracts.base import ContractModel, NonEmptyStr
from rapporteur_contracts.enums import ApiErrorCode


class ApiError(ContractModel):
    """Единый формат ошибки."""

    code: ApiErrorCode
    message: NonEmptyStr
    details: dict[str, Any] | None
    request_id: NonEmptyStr


class JobAccepted(ContractModel):
    """Ответ 202 на долгую операцию."""

    job_id: UUID
    status_url: NonEmptyStr


class PageMeta(ContractModel):
    """Пагинация списков: тело ответа — {items: [...], page: PageMeta}."""

    total: NonNegativeInt
    limit: PositiveInt
    offset: NonNegativeInt
