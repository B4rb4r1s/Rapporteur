"""Запуск этапов из командной строки без веба и БД (ADR-007):

    rapporteur run slides ./Конференция --data-dir ./.rapporteur-data
    rapporteur report ./Конференция --data-dir ./.rapporteur-data --unreviewed
    rapporteur graph

Каждый подходящий файл папки кладётся в хранилище по хэшу и обрабатывается отдельно. Ошибка
одного файла не останавливает остальные; код возврата 1, если хотя бы один файл не обработан.
Результат с тем же ключом кэша берётся из хранилища без повторного запуска (ADR-005).
"""

import argparse
import datetime as dt
import os
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from rapporteur_contracts import ArtifactRef, NotesArtifact, SlidesArtifact, StageInputs
from rapporteur_contracts.artifacts import FileFailure
from rapporteur_pipeline.chain import (
    NOTES_SUFFIXES,
    PRESENTATION_SUFFIXES,
    ChainError,
    collect_files,
    run_report,
)
from rapporteur_pipeline.graph import GRAPH_FILE, GraphError, execution_order, load_graph
from rapporteur_pipeline.ingest.notes import NotesStage
from rapporteur_pipeline.ingest.slides import SlidesStage
from rapporteur_pipeline.llm.client import DEFAULT_OLLAMA_URL, make_client
from rapporteur_pipeline.llm.profiles import ProfileError, load_profile
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
    "slides": StageEntry(SlidesStage(), StubSlidesStage(), PRESENTATION_SUFFIXES),
    "notes": StageEntry(NotesStage(), StubNotesStage(), NOTES_SUFFIXES),
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rapporteur", description="Этапы конвейера «Репортёра»")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="прогнать этап на файле или папке")
    run.add_argument("stage", choices=sorted(STAGES))
    run.add_argument("path", type=Path)
    run.add_argument("--data-dir", type=Path, default=Path(".rapporteur-data"))
    run.add_argument("--stub", action="store_true", help="заглушка вместо настоящего этапа")
    report = commands.add_parser(
        "report", help="справка по папке конференции: разбор, сводки по докладам, бланк"
    )
    report.add_argument("path", type=Path)
    report.add_argument("--data-dir", type=Path, default=Path(".rapporteur-data"))
    report.add_argument(
        "--registry",
        type=Path,
        help="реестр докладов YAML; по умолчанию <data-dir>/registry.yaml, "
        "без него — черновик по титульным слайдам",
    )
    report.add_argument("--profile", help="профиль модели из pipeline/profiles.yaml")
    report.add_argument("--stub", action="store_true", help="заглушки вместо модели")
    report.add_argument("--title", help="название конференции, если его нет в реестре")
    report.add_argument("--place", help="место проведения в форме для бланка, «г. Казань»")
    report.add_argument("--dates", type=_dates, default=[], help="даты через запятую, ГГГГ-ММ-ДД")
    report.add_argument("--talks", type=int, help="обработать только первые N докладов")
    report.add_argument(
        "--unreviewed",
        action="store_true",
        help="подтвердить сборку с непроверенными сводками (без редактора все такие)",
    )
    graph = commands.add_parser("graph", help="проверить граф этапов и показать порядок")
    graph.add_argument("--file", type=Path, default=GRAPH_FILE)
    args = parser.parse_args(argv)

    if args.command == "graph":
        return _show_graph(args.file)
    if args.command == "report":
        return _report(args)

    entry = STAGES[args.stage]
    stage = entry.stub if args.stub else entry.real
    files = collect_files(args.path, entry.suffixes)
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


def _show_graph(path: Path) -> int:
    try:
        graph = load_graph(path)
    except GraphError as error:
        print(error, file=sys.stderr)
        return 1
    stages = {stage.name: stage for stage in graph.stages}
    print(f"Граф этапов, версия {graph.version}: порядок исполнения")
    for number, name in enumerate(execution_order(graph), 1):
        stage = stages[name]
        details = [stage.scope.value, stage.resource.value]
        if stage.file_kinds:
            details.append("файлы: " + ", ".join(kind.value for kind in stage.file_kinds))
        if stage.needs:
            details.append("после: " + ", ".join(stage.needs))
        if stage.gates:
            details.append("ждёт: " + ", ".join(gate.value for gate in stage.gates))
        if stage.uses_llm:
            details.append("LLM")
        print(f"{number}. {name} → {stage.output} ({'; '.join(details)})")
    return 0


def _dates(value: str) -> list[dt.date]:
    try:
        return [dt.date.fromisoformat(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"даты в виде ГГГГ-ММ-ДД: {error}") from error


def _report(args: argparse.Namespace) -> int:
    try:
        profile = None if args.stub else load_profile(args.profile)
    except ProfileError as error:
        print(error, file=sys.stderr)
        return 1
    client = None
    if profile is not None:
        client = make_client(profile, os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL))
        print(
            f"Профиль {profile.name}: {profile.model}, рассуждение {profile.generation.reasoning}"
        )
    else:
        print("Заглушки вместо модели: тексты сводок не настоящие")
    try:
        result = run_report(
            args.path,
            args.data_dir,
            registry_path=args.registry,
            profile=profile,
            client=client,
            title=args.title,
            place=args.place,
            dates=args.dates,
            unreviewed_confirmed=args.unreviewed,
            limit=args.talks,
        )
    except ChainError as error:
        print(error, file=sys.stderr)
        return 1
    print("\nСправка:")
    for path in result.files:
        print(f"  {path}")
    if result.failures:
        print(f"\nНе обработано: {len(result.failures)}")
        for failure in result.failures:
            print(f"  {failure}")
    return 1 if result.failures else 0


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
