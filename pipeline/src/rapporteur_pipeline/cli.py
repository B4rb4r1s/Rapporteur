"""Запуск этапа из командной строки без веба и БД (ADR-007):

    rapporteur run slides ./Конференция --data-dir ./.rapporteur-data

Каждый подходящий файл папки кладётся в хранилище по хэшу и обрабатывается отдельно. Ошибка
одного файла не останавливает остальные; код возврата 1, если хотя бы один файл не обработан.
Результат с тем же ключом кэша берётся из хранилища без повторного запуска (ADR-005).
"""

import argparse
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from rapporteur_contracts import ArtifactRef, NotesArtifact, SlidesArtifact, StageInputs
from rapporteur_contracts.artifacts import FileFailure
from rapporteur_pipeline.ingest.notes import NotesStage
from rapporteur_pipeline.ingest.slides import SlidesStage
from rapporteur_pipeline.stage import (
    SOURCE_INPUT,
    Stage,
    StageContext,
    StageError,
    cache_key,
    config_hash,
)
from rapporteur_pipeline.storage import (
    DirectoryStorage,
    Storage,
    artifact_key,
    blob_key,
    sha256_hex,
)
from rapporteur_pipeline.stubs import StubNotesStage, StubSlidesStage


@dataclass(frozen=True)
class StageEntry:
    real: Stage
    stub: Stage
    suffixes: frozenset[str]


STAGES: dict[str, StageEntry] = {
    "slides": StageEntry(SlidesStage(), StubSlidesStage(), frozenset({".pptx", ".pdf", ".ppt"})),
    "notes": StageEntry(NotesStage(), StubNotesStage(), frozenset({".txt", ".md"})),
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rapporteur", description="Этапы конвейера «Репортёра»")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="прогнать этап на файле или папке")
    run.add_argument("stage", choices=sorted(STAGES))
    run.add_argument("path", type=Path)
    run.add_argument("--data-dir", type=Path, default=Path(".rapporteur-data"))
    run.add_argument("--stub", action="store_true", help="заглушка вместо настоящего этапа")
    args = parser.parse_args(argv)

    entry = STAGES[args.stage]
    stage = entry.stub if args.stub else entry.real
    files = _collect(args.path, entry.suffixes)
    if not files:
        print(f"В {args.path} нет файлов {', '.join(sorted(entry.suffixes))}", file=sys.stderr)
        return 1

    storage = DirectoryStorage(args.data_dir)
    failures = [
        failure
        for path in files
        if (failure := _run_file(stage, storage, path, args.path)) is not None
    ]
    print(f"\nФайлов: {len(files)}, с ошибкой: {len(failures)}")
    for failure in failures:
        print(f"  {failure.original_name}: {failure.code} — {failure.message}")
    return 1 if failures else 0


def _collect(path: Path, suffixes: frozenset[str]) -> list[Path]:
    candidates = [path] if path.is_file() else sorted(path.rglob("*"))
    return [
        item
        for item in candidates
        if item.is_file()
        and item.suffix.lower() in suffixes
        and not item.name.startswith((".", "~$"))
    ]


def _run_file(stage: Stage, storage: Storage, path: Path, root: Path) -> FileFailure | None:
    name = str(path.relative_to(root)) if path != root else path.name
    data = path.read_bytes()
    sha = sha256_hex(data)
    if not storage.exists(blob_key(sha)):
        storage.put(blob_key(sha), data)

    config = stage.config_model()
    cfg_hash = config_hash(config)
    inputs = StageInputs(
        conference_id=None,
        talk_id=None,
        artifacts=[ArtifactRef(name=SOURCE_INPUT, storage_key=blob_key(sha), sha256=sha)],
        config_hash=cfg_hash,
    )
    key = artifact_key(stage.name, cache_key(stage.name, stage.version, [sha], cfg_hash))
    if storage.exists(key):
        print(f"{name}: {_describe(stage.name, storage.get(key))} (из кэша)")
        return None

    try:
        with tempfile.TemporaryDirectory(prefix="rapporteur-") as tmp:
            output = stage.run(
                StageContext(storage=storage, config=config, tmp_dir=Path(tmp)), inputs
            )
    except StageError as error:
        print(f"{name}: ошибка {error.code}")
        return FileFailure(original_name=name, code=error.code, message=error.message)

    print(f"{name}: {_describe(stage.name, storage.get(output.artifact.storage_key))}")
    for warning in output.warnings:
        print(f"  ! {warning}")
    return None


def _describe(stage: str, data: bytes) -> str:
    if stage == "slides":
        slides = SlidesArtifact.model_validate_json(data)
        mark = " [ЗАГЛУШКА]" if slides.meta.stub else ""
        title = slides.title_candidates.title or "название не найдено"
        speakers = ", ".join(slides.title_candidates.speakers) or "докладчик не найден"
        return (
            f"{len(slides.slides)} слайдов, фрагментов {len(slides.fragments)}, "
            f"терминов {len(slides.dictionary)}; «{title}»; {speakers}{mark}"
        )
    notes = NotesArtifact.model_validate_json(data)
    mark = " [ЗАГЛУШКА]" if notes.meta.stub else ""
    sure = "" if notes.encoding_confident else ", не уверена"
    return f"{len(notes.fragments)} блоков, кодировка {notes.encoding}{sure}{mark}"


if __name__ == "__main__":
    raise SystemExit(main())
