"""Настройки и лимиты приёма файлов (spec §5.4, §10). Значения входят в ключ кэша."""

import zipfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, PositiveInt

from rapporteur.errors import FailureCode, StageError

MIB = 2**20


class PresentationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_file_bytes: PositiveInt = 200 * MIB
    max_pages: PositiveInt = 300
    # pptx — это ZIP: проверяется фактически распакованный объём, а не заголовки архива.
    max_zip_entries: PositiveInt = 10_000
    max_unpacked_bytes: PositiveInt = 2048 * MIB
    max_compression_ratio: PositiveInt = 100
    # Коэффициент сжатия проверяется только у крупных элементов: XML сжимается в десятки раз.
    ratio_check_min_bytes: PositiveInt = 1 * MIB
    render_long_side_px: PositiveInt = 1600
    max_render_pixels: PositiveInt = 40_000_000
    render_timeout_s: PositiveInt = 180


class NotesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_file_bytes: PositiveInt = 5 * MIB
    # Кодировка, которую выбрал пользователь, когда автоопределение не уверено (spec §5.2).
    encoding: Literal["utf-8", "cp1251"] | None = None


def check_size(size: int, limit: int) -> None:
    if size > limit:
        raise StageError(
            FailureCode.FILE_TOO_LARGE,
            f"Файл больше допустимого: {size // MIB} МБ при лимите {limit // MIB} МБ",
        )


def check_zip(path: Path, limits: PresentationConfig) -> None:
    """Распаковывает архив в никуда и считает настоящие байты, прерываясь на превышении."""
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > limits.max_zip_entries:
                raise _archive_error(f"в архиве {len(entries)} элементов")
            total = 0
            for entry in entries:
                if entry.flag_bits & 1:
                    raise StageError(FailureCode.ENCRYPTED_FILE, "Архив защищён паролем")
                unpacked = 0
                with archive.open(entry) as stream:
                    while chunk := stream.read(MIB):
                        unpacked += len(chunk)
                        total += len(chunk)
                        if total > limits.max_unpacked_bytes:
                            raise _archive_error("распакованный объём больше допустимого")
                if (
                    unpacked >= limits.ratio_check_min_bytes
                    and unpacked > entry.compress_size * limits.max_compression_ratio
                ):
                    raise _archive_error(f"подозрительно сильное сжатие у {entry.filename}")
    except (zipfile.BadZipFile, EOFError, OSError, NotImplementedError) as error:
        raise StageError(FailureCode.BROKEN_FILE, "Файл повреждён: архив pptx не читается") from (
            error
        )


def _archive_error(detail: str) -> StageError:
    return StageError(FailureCode.ARCHIVE_LIMITS, f"Архив pptx отклонён: {detail}")
