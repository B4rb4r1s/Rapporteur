"""Единый формат ошибок, включая валидацию маршрутов и ошибки PostgreSQL."""

from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from rapporteur_contracts import ApiError
from rapporteur_contracts.enums import API_ERROR_STATUS, ApiErrorCode


class ApiRequestError(Exception):
    def __init__(
        self,
        code: ApiErrorCode,
        message: str,
        *,
        status: int | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        self.code, self.message = code, message
        self.status, self.details = status, details


API_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ApiError} for status in (404, 409, 422, 500)
}


def error_response(
    request: Request,
    code: ApiErrorCode,
    message: str,
    *,
    status: int | None = None,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    error = ApiError(code=code, message=message, details=details, request_id=request_id)
    return JSONResponse(
        error.model_dump(mode="json"),
        status_code=status or API_ERROR_STATUS[code],
        headers={"X-Request-ID": request_id},
    )


def install_errors(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(ApiRequestError)
    async def api_failure(request: Request, exc: ApiRequestError) -> JSONResponse:
        return error_response(
            request, exc.code, exc.message, status=exc.status, details=exc.details
        )

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        issues = [
            {"loc": list(item["loc"]), "type": item["type"], "message": item["msg"]}
            for item in exc.errors()
        ]
        return error_response(
            request,
            ApiErrorCode.VALIDATION_ERROR,
            "Проверьте поля запроса",
            details={"errors": issues},
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code = ApiErrorCode.NOT_FOUND if exc.status_code == 404 else ApiErrorCode.VALIDATION_ERROR
        response = error_response(
            request,
            code,
            "Ресурс не найден" if exc.status_code == 404 else "Метод или запрос не поддерживается",
            status=exc.status_code,
        )
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(IntegrityError)
    async def integrity(request: Request, _exc: IntegrityError) -> JSONResponse:
        return error_response(
            request,
            ApiErrorCode.VALIDATION_ERROR,
            "Нарушена целостность данных: проверьте ссылки и уникальность",
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, _exc: SQLAlchemyError) -> JSONResponse:
        return error_response(
            request, ApiErrorCode.INTERNAL_ERROR, "База недоступна или миграции не выполнены"
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, _exc: Exception) -> JSONResponse:
        return error_response(request, ApiErrorCode.INTERNAL_ERROR, "Внутренняя ошибка сервера")
