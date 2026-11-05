"""Служебные команды backend."""

import argparse
import json
import os
import signal
from collections.abc import Sequence
from pathlib import Path
from threading import Event

import psycopg
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from rapporteur_backend.binding_benchmark import benchmark_bindings
from rapporteur_backend.db import configured_url, make_engine
from rapporteur_backend.migrate import migrate
from rapporteur_backend.processing import advance_started, run_scheduler
from rapporteur_pipeline.graph import GRAPH_FILE, GraphError, load_graph
from rapporteur_workers.queue import Queue


def schedule(engine: Engine, args: argparse.Namespace) -> int:
    graph = load_graph(args.graph)
    queue = Queue(engine.url.set(drivername="postgresql").render_as_string(hide_password=False))
    if args.once:
        queue.check()
        return int(advance_started(engine, queue, args.mode, graph) > 0)
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda _signum, _frame: stop.set())
    run_scheduler(engine, queue, args.mode, graph, stop)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backend «Репортёра»")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    commands.add_parser("openapi").add_argument("output", type=Path)
    benchmark = commands.add_parser("benchmark-bindings", help="оценить подсказки по разметке")
    benchmark.add_argument("snapshot", type=Path)
    benchmark.add_argument("reference", type=Path)
    benchmark.add_argument("--review-seconds", type=float)
    scheduler = commands.add_parser("schedule")
    scheduler.add_argument(
        "--mode", choices=["dev", "gpu"], default=os.environ.get("RAPPORTEUR_MODE", "dev")
    )
    scheduler.add_argument("--graph", type=Path, default=GRAPH_FILE)
    scheduler.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "benchmark-bindings":
        return benchmark_bindings(args.snapshot, args.reference, args.review_seconds)
    if args.command == "openapi":
        from rapporteur_backend.app import create_app

        args.output.write_text(
            json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return 0
    url = configured_url()
    if url is None:
        print("Укажите DATABASE_URL либо PGHOST, PGUSER, PGPASSWORD и PGDATABASE")
        return 1
    engine = make_engine(url)
    try:
        if args.command == "schedule":
            if args.mode not in {"dev", "gpu"}:
                parser.error("RAPPORTEUR_MODE должен быть dev или gpu")
            return schedule(engine, args)
        migrate(engine)
        print("Миграции backend применены")
        return 0
    except (SQLAlchemyError, psycopg.Error):
        print("Проверьте доступ к PostgreSQL и наличие миграций")
        return 1
    except (GraphError, RuntimeError) as error:
        print(error)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
