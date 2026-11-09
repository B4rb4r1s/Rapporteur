"""Устойчивые коды ошибок обработки файла или доклада (spec §6)."""

from enum import StrEnum


class FailureCode(StrEnum):
    """Код попадает в сообщение пользователю и в поле error доклада."""

    UNSUPPORTED_FORMAT = "unsupported_format"
    BROKEN_FILE = "broken_file"
    ENCRYPTED_FILE = "encrypted_file"
    FILE_TOO_LARGE = "file_too_large"
    TOO_MANY_PAGES = "too_many_pages"
    ARCHIVE_LIMITS = "archive_limits"
    RENDER_FAILED = "render_failed"
    # Шаги сводки (spec §6.7).
    NO_SOURCES = "no_sources"
    CONTEXT_OVERFLOW = "context_overflow"
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_TRUNCATED = "llm_truncated"
    LLM_INVALID_OUTPUT = "llm_invalid_output"
    # Сборка справки: выборка, бланк или подтверждение не позволяют собрать справку.
    REPORT_INVALID = "report_invalid"


class StageError(Exception):
    """Контролируемая ошибка входа. Обработка остальных файлов и докладов продолжается
    (spec §6)."""

    def __init__(self, code: FailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class UserError(Exception):
    """Ошибка запроса пользователя: API отдаёт её с кодом status и текстом message."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class NotFoundError(UserError):
    def __init__(self, message: str) -> None:
        super().__init__(message, 404)
