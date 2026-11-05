"""Каждый интеграционный тест получает отдельную временную БД PostgreSQL."""

import os
from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from rapporteur_workers.queue import Queue, migrate


@pytest.fixture
def dsn() -> Iterator[str]:
    admin_dsn = os.environ.get("TEST_DATABASE_URL")
    if not admin_dsn:
        pytest.skip(
            "Интеграционные тесты требуют TEST_DATABASE_URL; make test запускает PostgreSQL"
        )
    name = f"rapporteur_test_{uuid4().hex}"
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} ENCODING 'UTF8' TEMPLATE template0").format(
                sql.Identifier(name)
            )
        )
    test_dsn = make_conninfo(admin_dsn, dbname=name)
    try:
        migrate(test_dsn)
        yield test_dsn
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def queue(dsn: str) -> Queue:
    return Queue(dsn)
