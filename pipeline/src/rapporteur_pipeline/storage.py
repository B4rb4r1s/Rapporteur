"""Хранилище файлов и артефактов (ADR-005). Ключи — относительные пути внутри тома."""

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

_KEY_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def blob_key(sha256: str) -> str:
    return f"blobs/{sha256[:2]}/{sha256}"


def artifact_key(stage: str, cache_key: str) -> str:
    return f"artifacts/{stage}/{cache_key}.json"


def derived_key(cache_key: str, name: str) -> str:
    return f"derived/{cache_key}/{name}"


class DirectoryStorage:
    """Том /data. Запись атомарная: файл появляется целиком или не появляется."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, key: str) -> Path:
        parts = key.split("/")
        if not all(_KEY_PART.match(part) for part in parts):
            raise ValueError(f"недопустимый ключ хранилища: {key!r}")
        return self._root.joinpath(*parts)

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            tmp.write(data)
        os.replace(tmp.name, path)

    def add_blob(self, sha256: str, staged: Path) -> None:
        """Публикует готовый файл с того же тома без копирования и перезаписи."""
        path = self._path(blob_key(sha256))
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(staged, path)
        except FileExistsError:
            pass

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
