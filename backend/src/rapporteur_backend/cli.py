"""Служебные команды backend."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from rapporteur_backend.db import configured_url, make_engine
from rapporteur_backend.migrate import migrate


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backend «Репортёра»")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    commands.add_parser("openapi").add_argument("output", type=Path)
    args = parser.parse_args(argv)
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
        migrate(engine)
        print("Миграции backend применены")
        return 0
    except SQLAlchemyError:
        print("Миграция не выполнена: проверьте доступ к PostgreSQL")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
