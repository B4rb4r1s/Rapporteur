"""Модели Ollama для комплекта установки без интернета (spec §8.3, docs/offline.md).

    python -m rapporteur.bundle <каталог моделей> <каталог комплекта> [модель ...]

Копирует манифест и блобы каждой модели. Без имён — модель профиля по умолчанию
из profiles.yaml. Блобы Ollama адресуются по SHA-256, поэтому каждый сверяется со своим
именем при копировании: повреждённый файл в комплект не попадёт.
"""

import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from rapporteur.llm.profiles import load_profile

REGISTRY = "registry.ollama.ai"
_CHUNK = 16 * 2**20


class BundleError(Exception):
    pass


def manifest_path(name: str) -> Path:
    """qwen3.5:9b → registry.ollama.ai/library/qwen3.5/9b; user/model:tag — без library."""
    model, _, tag = name.partition(":")
    parts = model.split("/")
    if len(parts) == 1:
        parts = ["library", *parts]
    if not model or any(part in {"", ".", ".."} for part in [*parts, tag or "latest"]):
        raise BundleError(f"Недопустимое имя модели: {name!r}")
    return Path(REGISTRY, *parts, tag or "latest")


def export_model(name: str, source: Path, target: Path) -> int:
    """Возвращает размер блобов модели в байтах."""
    relative = manifest_path(name)
    manifest_file = source / "manifests" / relative
    if not manifest_file.is_file():
        raise BundleError(f"Модели {name} нет в {source}: скачайте её командой make pull")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    digests = [manifest["config"]["digest"], *(layer["digest"] for layer in manifest["layers"])]
    (target / "blobs").mkdir(parents=True, exist_ok=True)
    total = 0
    for digest in digests:
        algorithm, _, expected = digest.partition(":")
        blob = f"{algorithm}-{expected}"
        total += _copy_checked(source / "blobs" / blob, target / "blobs" / blob, expected)
    destination = target / "manifests" / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(manifest_file.read_bytes())
    return total


def _copy_checked(source: Path, destination: Path, expected: str) -> int:
    if not source.is_file():
        raise BundleError(f"Нет блоба {source.name}: каталог моделей неполный")
    size = source.stat().st_size
    if destination.is_file() and destination.stat().st_size == size:
        return size  # Уже скопирован прошлым запуском; SHA256SUMS проверит его на месте.
    partial = destination.with_name(destination.name + ".partial")
    digest = hashlib.sha256()
    with source.open("rb") as reader, partial.open("wb") as writer:
        while chunk := reader.read(_CHUNK):
            digest.update(chunk)
            writer.write(chunk)
    if digest.hexdigest() != expected:
        partial.unlink()
        raise BundleError(f"Блоб {source.name} повреждён: SHA-256 не совпадает с именем")
    partial.replace(destination)
    return size


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    source, target, names = Path(args[0]), Path(args[1]), args[2:]
    try:
        for name in names or [load_profile().model]:
            size = export_model(name, source, target)
            print(f"{name}: {size / 2**30:.1f} ГБ")
    except BundleError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
