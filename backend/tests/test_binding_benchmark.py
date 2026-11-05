from pathlib import Path
from uuid import UUID, uuid4

import pytest

from rapporteur_backend.cli import main
from rapporteur_backend.materials import Material, Materials, ReviewTalk
from rapporteur_contracts import FileBinding, SourceFile
from rapporteur_contracts.enums import FileKind, TalkStatus


def test_benchmark_counts_wrong_missing_and_alternate_bindings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    conference, section, first, second = uuid4(), uuid4(), uuid4(), uuid4()

    def material(path: str, suggestion: UUID | None) -> Material:
        file_id = uuid4()
        return Material(
            source=SourceFile(
                id=file_id,
                conference_id=conference,
                sha256="0" * 64,
                original_name=path,
                kind=FileKind.PRESENTATION,
                media_type="application/pdf",
                size_bytes=1,
                duration_ms=None,
                encoding=None,
            ),
            binding=FileBinding(
                id=uuid4(), source_file_id=file_id, talk_id=None, confirmed=False, confidence=None
            ),
            relative_path=path,
            title_candidates=None,
            warning=None,
            suggested_talk_id=suggestion,
        )

    primary = material("main.pptx", first)
    alternate = material("main.pdf", first)
    alternate.alternate_of = primary.source.id
    snapshot = Materials(
        processing_started=False,
        sections=[],
        talks=[
            ReviewTalk(
                id=uid,
                section_id=section,
                section="Тема",
                order=order,
                title=None,
                status=TalkStatus.DRAFT,
            )
            for order, uid in enumerate((first, second), 1)
        ],
        files=[
            primary,
            alternate,
            material("wrong.pdf", first),
            material("missing.pdf", None),
            material("general.pdf", second),
        ],
    )
    snapshot_path = tmp_path / "materials.json"
    snapshot_path.write_text(snapshot.model_dump_json(), encoding="utf-8")
    reference_path = tmp_path / "reference.yaml"
    partial_reference = """\
files:
  - {path: main.pptx, talk: {section: Тема, order: 1}}
  - {path: main.pdf, talk: null}
  - {path: wrong.pdf, talk: {section: Тема, order: 2}}
  - {path: missing.pdf, talk: {section: Тема, order: 2}}
"""
    command = ["benchmark-bindings", str(snapshot_path), str(reference_path)]
    reference_path.write_text(partial_reference, encoding="utf-8")
    assert main(command) == 1
    assert "Без разметки: 1" in capsys.readouterr().err

    reference_path.write_text(
        partial_reference + "  - {path: general.pdf, talk: null}\n", encoding="utf-8"
    )
    assert main([*command, "--review-seconds", "42"]) == 0
    output = capsys.readouterr().out
    assert "Верные подсказки среди предложенных: 1/3" in output
    assert "Найденные привязки по разметке: 1/3" in output
    assert "Верные начальные решения: 2/5" in output
    assert "Время ручной проверки: 42.0 с" in output
