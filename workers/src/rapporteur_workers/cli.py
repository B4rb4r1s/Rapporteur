"""Управление очередью и воркерами без зависимости от веба."""

import argparse
import os
import signal
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

import psycopg

from rapporteur_contracts import ArtifactRef, StageInputs
from rapporteur_contracts.enums import JobStatus, ResourceKind
from rapporteur_pipeline.graph import GraphError, load_graph
from rapporteur_pipeline.stage import SOURCE_INPUT, cache_key, config_hash
from rapporteur_pipeline.storage import DirectoryStorage, Storage, blob_key, sha256_hex
from rapporteur_workers.queue import Mode, Queue, migrate
from rapporteur_workers.runtime import healthy, log, run
from rapporteur_workers.scheduler import ConferenceState, Scheduler
from rapporteur_workers.stages import FILE_STAGES, RESOURCES, get_stage


def enqueue_file(
    queue: Queue, storage: Storage, stage_name: str, mode: Mode, path: Path, priority: int = 0
) -> UUID:
    stage = get_stage(stage_name, mode)  # Отказ до чтения файла, если реализации нет.
    config = stage.config_model()
    cfg_hash = config_hash(config)
    data = path.read_bytes()
    sha = sha256_hex(data)
    key = blob_key(sha)
    if not storage.exists(key):
        storage.put(key, data)
    inputs = StageInputs(
        conference_id=None,
        talk_id=None,
        artifacts=[ArtifactRef(name=SOURCE_INPUT, storage_key=key, sha256=sha)],
        config_hash=cfg_hash,
    )
    return queue.enqueue(
        key=f"{mode}:{cache_key(stage.name, stage.version, [sha], cfg_hash)}",
        stage=stage.name,
        resource=RESOURCES[stage.name],
        mode=mode,
        inputs=inputs,
        config=config.model_dump(mode="json"),
        priority=priority,
    )


def smoke(queue: Queue, storage: Storage, mode: Mode, timeout: float = 30) -> int:
    if mode != "dev":
        raise ValueError("smoke с вымышленными материалами разрешён только в dev")
    with tempfile.TemporaryDirectory(prefix="rapporteur-smoke-") as tmp:
        note, audio = Path(tmp) / "note.txt", Path(tmp) / "audio.wav"
        # Уникальный вход гарантирует проверку работающих воркеров, а не прежнего результата.
        marker = str(time.time_ns()).encode()
        note.write_bytes(b"synthetic dev note " + marker)
        audio.write_bytes(b"synthetic dev audio " + marker)
        jobs = [
            enqueue_file(queue, storage, stage, mode, path)
            for stage, path in [("notes", note), ("asr", audio)]
        ]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        states = [queue.get(job_id) for job_id in jobs]
        if any(
            job is not None and job.status in {JobStatus.FAILED, JobStatus.CANCELLED}
            for job in states
        ):
            log("smoke_failed")
            return 1
        if all(job is not None and job.status is JobStatus.SUCCEEDED for job in states):
            for job in states:
                assert job is not None and job.output is not None
                if not job.output.meta.stub or not storage.exists(job.output.artifact.storage_key):
                    raise ValueError("Smoke не получил явно помеченный артефакт заглушки")
            log("smoke_passed", stub=True, jobs=len(jobs))
            return 0
        time.sleep(0.2)
    log("smoke_timeout")
    return 1


def database_dsn() -> str:
    if dsn := os.environ.get("DATABASE_URL"):
        return dsn
    missing = [
        key for key in ("PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD") if not os.environ.get(key)
    ]
    if missing:
        raise ValueError("Укажите DATABASE_URL или переменные: " + ", ".join(missing))
    return ""  # libpq читает PG* напрямую, пароль не собирается в URL.


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Очередь и исполнители «Репортёра»")
    parser.add_argument(
        "--mode", choices=["dev", "gpu"], default=os.environ.get("RAPPORTEUR_MODE", "dev")
    )
    parser.add_argument(
        "--data-dir", type=Path, default=Path(os.environ.get("RAPPORTEUR_DATA", "/data"))
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    commands.add_parser("smoke")
    for command in ("run", "health"):
        child = commands.add_parser(command)
        child.add_argument(
            "--resource", type=ResourceKind, choices=list(ResourceKind), required=True
        )
        child.add_argument("--health-file", type=Path)
    enqueue = commands.add_parser("enqueue")
    enqueue.add_argument("stage", choices=sorted(FILE_STAGES))
    enqueue.add_argument("path", type=Path)
    enqueue.add_argument("--priority", type=int, default=0)
    for command in ("show", "cancel"):
        commands.add_parser(command).add_argument("job_id", type=UUID)
    for command in ("plan", "advance"):
        child = commands.add_parser(command)
        child.add_argument("snapshot", type=Path)
        child.add_argument("--graph", type=Path)
        child.add_argument("--priority", type=int, default=0)
    args = parser.parse_args(argv)
    mode: Mode = args.mode
    try:
        if mode not in {"dev", "gpu"}:
            raise ValueError("RAPPORTEUR_MODE должен быть dev или gpu")
        dsn = database_dsn()
        if args.command == "migrate":
            migrate(dsn)
            log("queue_migrated", version=1)
            return 0
        queue = Queue(dsn)
        queue.check()
        storage = DirectoryStorage(args.data_dir)
        if args.command in {"run", "health"}:
            health_file = args.health_file or Path(f"/tmp/rapporteur-{args.resource.value}.json")
            if args.command == "health":
                return 0 if healthy(health_file, args.resource, mode) else 1
            stop = threading.Event()
            for signum in (signal.SIGINT, signal.SIGTERM):
                signal.signal(signum, lambda _signum, _frame: stop.set())
            run(queue, storage, args.resource, mode, health_file, stop)
        elif args.command == "enqueue":
            print(enqueue_file(queue, storage, args.stage, mode, args.path, args.priority))
        elif args.command == "show":
            job = queue.get(args.job_id)
            if job is None:
                return 1
            print(job.model_dump_json(indent=2))
        elif args.command == "cancel":
            return 0 if queue.cancel(args.job_id) else 1
        elif args.command == "smoke":
            return smoke(queue, storage, mode)
        elif args.command in {"plan", "advance"}:
            state = ConferenceState.model_validate_json(args.snapshot.read_text(encoding="utf-8"))
            scheduler = Scheduler(
                queue, lambda _id: state, mode, graph=load_graph(args.graph) if args.graph else None
            )
            if args.command == "plan":
                print(scheduler.preview(state.conference_id).model_dump_json(indent=2))
            else:
                print(
                    scheduler.advance(state.conference_id, priority=args.priority).model_dump_json(
                        indent=2
                    )
                )
    except psycopg.Error:
        log("command_failed", code="database_unavailable_or_migration_required")
        return 1
    except (ValueError, RuntimeError, OSError, GraphError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
