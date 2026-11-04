"""Явный запуск Alembic. Процессы web не изменяют схему при старте."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text


def migration_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    return config


def migrate(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(728103103)"))
        config = migration_config()
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
