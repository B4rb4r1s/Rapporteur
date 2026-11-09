"""Файлы на томе /data (ADR-005, ADR-014): оригиналы по SHA-256, кэш разбора и шагов сводки,
картинки слайдов и файлы справок. Ключи — относительные пути внутри тома."""

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

_KEY_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cache_key(*parts: object) -> str:
    """Ключ кэша из версии шага, хэшей входов и конфигурации: при любой их смене шаг
    выполняется заново, иначе результат берётся с тома (spec §3, п. 6)."""
    payload = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return sha256_hex(payload.encode())


def blob_key(sha256: str) -> str:
    return f"blobs/{sha256[:2]}/{sha256}"


class Storage:
    """Запись атомарная: файл появляется целиком или не появляется."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, key: str) -> Path:
        parts = key.split("/")
        if not all(_KEY_PART.match(part) for part in parts):
            raise ValueError(f"недопустимый ключ хранилища: {key!r}")
        return self.root.joinpath(*parts)

    def put(self, key: str, data: bytes) -> None:
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            tmp.write(data)
        os.replace(tmp.name, path)

    def get(self, key: str) -> bytes:
        return self.path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self.path(key).is_file()

    def add_blob(self, sha256: str, staged: Path) -> None:
        """Публикует готовый файл с того же тома без копирования и перезаписи."""
        path = self.path(blob_key(sha256))
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(staged, path)
        except FileExistsError:
            pass

    def cached[T: BaseModel](self, key: str, model: type[T], compute: Callable[[], T]) -> T:
        """Результат шага из кэша, а если его нет или он не читается — вычисленный заново."""
        if self.exists(key):
            try:
                return model.model_validate_json(self.get(key))
            except ValueError:
                pass
        value = compute()
        self.put(key, value.model_dump_json(indent=2).encode())
        return value
