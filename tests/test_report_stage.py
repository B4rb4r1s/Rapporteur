"""Сборка справки функцией build_report: снимок входа, переменные и файлы (spec §6.9)."""

from pathlib import Path

import pytest

from rapporteur.contracts.enums import ReportFormat
from rapporteur.report.assemble import ReportError, select_talks
from rapporteur.report.build import REPORT_FORMATS, build_report
from rapporteur.storage import Storage, sha256_hex

from .test_report_build import SOURCES, SUMMARIES

# PDF проверяет test_report_build; здесь достаточно форматов без LibreOffice.
FAST = (ReportFormat.DOCX, ReportFormat.MARKDOWN)


def test_report_carries_the_input_snapshot_and_files(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "data")
    selection = select_talks(SOURCES)

    built = build_report(SOURCES, selection, True, storage, "reports/v1", tmp_path, formats=FAST)

    included = {talk.talk_id for talk in built.input.talks}
    assert included == {item.talk_id for item in SUMMARIES}
    assert included | {talk.talk_id for talk in built.input.skipped} == set(selection.talk_ids)
    assert built.variables.talks_count == len(built.input.talks)
    assert [file.format for file in built.files] == list(FAST)
    for file in built.files:
        assert sha256_hex(storage.get(file.storage_key)) == file.sha256
    assert any("не проверена" in warning for warning in built.warnings)


def test_unreviewed_talks_need_confirmation(tmp_path: Path) -> None:
    with pytest.raises(ReportError, match="непроверенные"):
        build_report(
            SOURCES, select_talks(SOURCES), False, Storage(tmp_path), "r", tmp_path, formats=FAST
        )


def test_broken_template_is_a_report_error(tmp_path: Path) -> None:
    with pytest.raises(ReportError):
        build_report(
            SOURCES,
            select_talks(SOURCES),
            True,
            Storage(tmp_path),
            "r",
            tmp_path,
            template_dir=tmp_path / "missing",
            formats=FAST,
        )


def test_all_three_formats_by_default() -> None:
    assert set(REPORT_FORMATS) == {ReportFormat.DOCX, ReportFormat.MARKDOWN, ReportFormat.PDF}
