from pathlib import Path

import pytest

from rapporteur_pipeline.cli import main
from rapporteur_pipeline.storage import DirectoryStorage

from .conftest import CONTENT_SLIDE, TITLE_SLIDE, make_pptx


@pytest.fixture
def conference(tmp_path: Path) -> Path:
    root = tmp_path / "Конференция"
    (root / "01_Иванов").mkdir(parents=True)
    (root / "01_Иванов" / "доклад.pptx").write_bytes(make_pptx([TITLE_SLIDE, CONTENT_SLIDE]))
    (root / "02_Петров").mkdir()
    (root / "02_Петров" / "битый.pptx").write_bytes(b"\x00\x01\x02")
    (root / "02_Петров" / "заметки.txt").write_bytes("Вопрос про данные.".encode("cp1251"))
    return root


def test_run_processes_every_file_and_reports_failures(
    conference: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "data"

    assert main(["run", "slides", str(conference), "--data-dir", str(data_dir)]) == 1
    first = capsys.readouterr().out
    assert "01_Иванов/доклад.pptx: 2 слайдов" in first
    assert "02_Петров/битый.pptx: unsupported_format" in first

    assert main(["run", "slides", str(conference), "--data-dir", str(data_dir)]) == 1
    assert "(из кэша)" in capsys.readouterr().out


def test_notes_and_stub(
    conference: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "data"

    assert main(["run", "notes", str(conference), "--data-dir", str(data_dir)]) == 0
    assert "1 блоков, кодировка cp1251" in capsys.readouterr().out

    assert main(["run", "slides", str(conference), "--data-dir", str(data_dir), "--stub"]) == 0
    assert capsys.readouterr().out.count("[ЗАГЛУШКА]") == 2


@pytest.mark.parametrize("key", ["../outside", "/etc/passwd", "a//b", "blobs/.hidden", ""])
def test_storage_rejects_unsafe_keys(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValueError):
        DirectoryStorage(tmp_path).put(key, b"x")


def test_storage_roundtrip(tmp_path: Path) -> None:
    storage = DirectoryStorage(tmp_path)
    storage.put("artifacts/slides/key.json", b"{}")

    assert storage.exists("artifacts/slides/key.json")
    assert storage.get("artifacts/slides/key.json") == b"{}"
    storage.delete("artifacts/slides/key.json")
    assert not storage.exists("artifacts/slides/key.json")
