"""Этап report: справка по запросу сборки и сводкам из артефактов (ADR-013)."""

import uuid
from pathlib import Path

import pytest

from rapporteur_contracts import (
    ArtifactRef,
    Report,
    ReportArtifact,
    ReportRequest,
)
from rapporteur_contracts.enums import ReportFormat
from rapporteur_pipeline.chain import put_blob, run_cached
from rapporteur_pipeline.report.assemble import select_talks
from rapporteur_pipeline.report.stage import (
    REPORT_FORMATS,
    REQUEST_INPUT,
    ReportConfig,
    ReportStage,
)
from rapporteur_pipeline.stage import FailureCode, StageError
from rapporteur_pipeline.storage import DirectoryStorage, sha256_hex

from .test_report_build import CONFERENCE, PEOPLE, SECTIONS, SOURCES, SUMMARIES, TALKS

# PDF проверяет test_report_build; здесь достаточно форматов без LibreOffice.
FAST = ReportConfig(formats=(ReportFormat.DOCX, ReportFormat.MARKDOWN))


@pytest.fixture
def storage(tmp_path: Path) -> DirectoryStorage:
    return DirectoryStorage(tmp_path / "data")


def request(
    *, template_version: str = "starter-1", unreviewed_confirmed: bool = True
) -> ReportRequest:
    return ReportRequest(
        report=Report(
            id=uuid.UUID("00000000-0000-4000-8000-000000000070"),
            author_id=uuid.UUID("00000000-0000-4000-8000-000000000002"),
            conference_id=CONFERENCE.id,
            selection=select_talks(SOURCES),
            template_version=template_version,
            version=1,
        ),
        conference=CONFERENCE,
        sections=SECTIONS,
        talks=TALKS,
        people=PEOPLE,
        summaries=SUMMARIES,
        unreviewed_confirmed=unreviewed_confirmed,
    )


def inputs(storage: DirectoryStorage, value: ReportRequest) -> list[ArtifactRef]:
    return [put_blob(storage, value.model_dump_json().encode(), name=REQUEST_INPUT)]


def test_report_artifact_carries_the_input_snapshot_and_files(storage: DirectoryStorage) -> None:
    ran = run_cached(ReportStage(), storage, FAST, inputs(storage, request()))

    artifact = ReportArtifact.model_validate_json(storage.get(ran.ref.storage_key))
    assert artifact.report_id == request().report.id
    included = {talk.talk_id for talk in artifact.input.talks}
    assert included == {item.talk_id for item in SUMMARIES}
    assert included | {talk.talk_id for talk in artifact.input.skipped} == set(
        request().report.selection.talk_ids
    )
    assert artifact.variables.talks_count == len(artifact.input.talks)
    assert [file.format for file in artifact.files] == [ReportFormat.DOCX, ReportFormat.MARKDOWN]
    for file in artifact.files:
        assert sha256_hex(storage.get(file.storage_key)) == file.sha256
    assert any("не проверена" in warning for warning in ran.warnings)


def test_same_request_reuses_the_cached_report(storage: DirectoryStorage) -> None:
    refs = inputs(storage, request())

    first = run_cached(ReportStage(), storage, FAST, refs)
    second = run_cached(ReportStage(), storage, FAST, refs)

    assert second.cached and second.ref == first.ref


def test_template_version_must_match_the_report(storage: DirectoryStorage) -> None:
    with pytest.raises(StageError) as error:
        run_cached(
            ReportStage(), storage, FAST, inputs(storage, request(template_version="starter-0"))
        )
    assert error.value.code is FailureCode.REPORT_INVALID
    assert "starter-0" in error.value.message


def test_unreviewed_talks_need_confirmation(storage: DirectoryStorage) -> None:
    with pytest.raises(StageError) as error:
        run_cached(
            ReportStage(), storage, FAST, inputs(storage, request(unreviewed_confirmed=False))
        )
    assert error.value.code is FailureCode.REPORT_INVALID


def test_unknown_inputs_are_rejected(storage: DirectoryStorage) -> None:
    refs = inputs(storage, request())
    stray = put_blob(storage, b"{}", name="facts")

    with pytest.raises(ValueError, match="facts"):
        run_cached(ReportStage(), storage, FAST, [*refs, stray])


def test_all_three_formats_by_default() -> None:
    assert ReportConfig().formats == REPORT_FORMATS
    assert set(REPORT_FORMATS) == {ReportFormat.DOCX, ReportFormat.MARKDOWN, ReportFormat.PDF}
