"""Формат презентации по содержимому, а не по расширению (spec §6.1)."""

import io
import zipfile
from enum import StrEnum

from rapporteur.errors import FailureCode, StageError

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
# Зашифрованный документ Office — не ZIP, а составной файл OLE с потоком EncryptionInfo.
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ENCRYPTION_INFO = "EncryptionInfo".encode("utf-16-le")


class PresentationFormat(StrEnum):
    PPTX = "pptx"
    PDF = "pdf"


def detect_presentation(data: bytes) -> PresentationFormat:
    # Некоторые генераторы пишут мусор перед заголовком PDF; спецификация допускает до 1024 байт.
    if _PDF_MAGIC in data[:1024]:
        return PresentationFormat.PDF
    if data.startswith(_ZIP_MAGIC):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile as error:
            raise StageError(FailureCode.BROKEN_FILE, "Файл повреждён: архив не читается") from (
                error
            )
        if "ppt/presentation.xml" in names:
            return PresentationFormat.PPTX
        raise StageError(FailureCode.UNSUPPORTED_FORMAT, "Архив не является презентацией pptx")
    if data.startswith(_OLE_MAGIC):
        if _ENCRYPTION_INFO in data:
            raise StageError(FailureCode.ENCRYPTED_FILE, "Презентация защищена паролем")
        raise StageError(
            FailureCode.UNSUPPORTED_FORMAT,
            "Старый формат ppt не поддерживается: сохраните презентацию как pptx или pdf",
        )
    raise StageError(FailureCode.UNSUPPORTED_FORMAT, "Файл не похож на презентацию pptx или pdf")
