"""Цепочка «папка → справка» из командной строки с заглушками вместо модели (P1-06, P1-11)."""

from pathlib import Path

import pytest
import yaml

from rapporteur_pipeline.cli import main
from rapporteur_pipeline.report.grammar import NBSP

from .conftest import CONTENT_SLIDE, TITLE_SLIDE, SlideSpec, make_pptx

OTHER_TITLE = SlideSpec(
    title="Журналирование в колоночных хранилищах",
    body=["Подготовила: Петрова А. А.", "Группа: ИУ6-32М"],
)


@pytest.fixture
def conference(tmp_path: Path) -> Path:
    root = tmp_path / "Конференция"
    root.mkdir()
    (root / "01_кэш.pptx").write_bytes(make_pptx([TITLE_SLIDE, CONTENT_SLIDE]))
    # Та же презентация в PDF-выгрузке — тот же доклад.
    (root / "01_кэш_копия.pptx").write_bytes(make_pptx([TITLE_SLIDE, CONTENT_SLIDE]))
    (root / "02_журнал.pptx").write_bytes(make_pptx([OTHER_TITLE, CONTENT_SLIDE]))
    (root / "02_журнал.txt").write_text("Из зала спросили про сжатие журнала.", encoding="utf-8")
    return root


def report(conference: Path, data: Path, *extra: str) -> int:
    return main(
        [
            "report",
            str(conference),
            "--data-dir",
            str(data),
            "--stub",
            "--title",
            "Данные и знания",
            "--dates",
            "2026-10-30,2026-10-31",
            *extra,
        ]
    )


def test_folder_becomes_a_report_in_three_formats(
    conference: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "data"

    assert report(conference, data, "--unreviewed") == 0
    out = capsys.readouterr().out

    draft = yaml.safe_load((data / "registry-draft.yaml").read_text(encoding="utf-8"))
    talks = draft["talks"]
    assert [t["sources"]["slides"] for t in talks] == [
        ["01_кэш.pptx", "01_кэш_копия.pptx"],
        ["02_журнал.pptx"],
    ]
    assert talks[1]["sources"]["notes"] == ["02_журнал.txt"]
    exports = sorted(data.glob("exports/*/spravka.*"))
    assert [p.suffix for p in exports] == [".docx", ".md", ".pdf"]
    md_file = next(p for p in exports if p.suffix == ".md")
    markdown = md_file.read_text(encoding="utf-8").replace(NBSP, " ")
    assert "Конференция проходила 30–31 октября 2026" in markdown
    assert "заслушано 2" in markdown
    assert "«Кэширование запросов в распределённых базах данных» (Иванов И. И.)" in markdown
    assert "Сводка не проверена человеком" in out


def test_second_run_takes_everything_from_cache(
    conference: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "data"
    assert report(conference, data, "--unreviewed") == 0
    capsys.readouterr()

    assert report(conference, data, "--unreviewed") == 0
    assert "факты: из кэша" in capsys.readouterr().out


def test_registry_overrides_titles_and_speakers(
    conference: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "data"
    assert report(conference, data, "--unreviewed") == 0
    draft = yaml.safe_load((data / "registry-draft.yaml").read_text(encoding="utf-8"))
    draft["talks"][0]["title"] = "Кэш запросов"
    draft["talks"][0]["speakers"] = ["Иванов Иван Иванович"]
    draft["talks"][1]["status"] = "not_held"
    (data / "registry.yaml").write_text(yaml.safe_dump(draft, allow_unicode=True))
    capsys.readouterr()

    assert report(conference, data, "--unreviewed") == 0
    markdown = max(data.glob("exports/*/spravka.md"), key=lambda p: p.stat().st_mtime)
    text = markdown.read_text(encoding="utf-8").replace(NBSP, " ")
    assert "«Кэш запросов» (Иванов Иван Иванович)" in text
    assert "заслушан 1 доклад" in text and "Журналирование" not in text


def test_unreviewed_report_needs_confirmation(
    conference: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert report(conference, tmp_path / "data") == 1
    assert "подтвердите сборку без проверки" in capsys.readouterr().err
