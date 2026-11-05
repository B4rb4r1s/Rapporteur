"""Детерминированные идентификаторы, общие для конвейера и backend (ADR-013)."""

import re
from uuid import UUID, uuid5

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def source_file_id(conference_id: UUID, sha256: str) -> UUID:
    """id файла конференции. Файл уникален в конференции по SHA-256, поэтому id вычисляется
    из этой пары: этап сводки и БД получают одно и то же значение без обмена данными."""
    if not _SHA256.fullmatch(sha256):
        raise ValueError("sha256 должен состоять из 64 строчных шестнадцатеричных символов")
    return uuid5(conference_id, f"file:{sha256}")
